"""Telegram consent prompter — inline-keyboard round-trip for the consent gate.

The acting pipeline coroutine calls :meth:`TelegramConsentPrompter.prompt`,
which sends a yes/no(/approve-session/trust-window) inline keyboard and then
suspends on an :class:`asyncio.Future`. When the user taps a button the
Telegram callback-query handler calls :meth:`handle_callback`, which resolves
the Future with the chosen :class:`~stackowl.tools.consent.ConsentScope`.

Fail-closed: a send failure, a malformed callback, or a timeout all resolve to
``DENY`` — silence is never consent.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import uuid4

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.infra.observability import log
from stackowl.tools.consent import ConsentRequest, ConsentScope
from stackowl.tui.i18n import localize

__all__ = ["TelegramConsentPrompter"]

_CALLBACK_PREFIX = "consent"
# Default time a consent prompt stays open before failing closed.
_DEFAULT_TIMEOUT_SECONDS = 120.0

# Which scopes get a button, in display order, keyed by whether relaxation
# (batch/window) is permitted for this request.
_BASE_SCOPES = (ConsentScope.ONCE, ConsentScope.DENY)
_RELAXATION_SCOPES = (ConsentScope.SESSION, ConsentScope.WINDOW)

# Stable i18n keys (labels render via localize; English copy lives in catalogs).
_LABEL_KEYS = {
    ConsentScope.ONCE: "consent.btn.approve_once",
    ConsentScope.DENY: "consent.btn.deny",
    ConsentScope.SESSION: "consent.btn.approve_session",
    ConsentScope.WINDOW: "consent.btn.trust_window",
}


class _SupportsInlineKeyboard(Protocol):
    async def send_inline_keyboard(
        self, text: str, keyboard: dict[str, object], chat_id: int | None = None
    ) -> None: ...


class TelegramConsentPrompter:
    """Bridges :class:`ConsentPolicy` to a Telegram inline-keyboard round-trip."""

    def __init__(
        self,
        adapter: _SupportsInlineKeyboard,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        lang: str = "auto",
    ) -> None:
        self._adapter = adapter
        self._timeout = timeout_seconds
        self._lang = lang
        self._pending: dict[str, asyncio.Future[ConsentScope]] = {}

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        """Send the keyboard and suspend until a button resolves it (or timeout)."""
        # 1. ENTRY
        log.telegram.debug(
            "[telegram] consent.prompt: entry",
            extra={"_fields": {"tool": req.tool_name, "relax": req.allow_relaxation}},
        )
        # 2. DECISION — target the INITIATING user's chat (session_id == Telegram
        # user id), never a shared/last chat (prevents a confused-deputy where a
        # different user sees/answers the prompt). Fail closed if unresolvable.
        try:
            chat_id = int(req.session_id)
        except (TypeError, ValueError):
            log.telegram.error(
                "[telegram] consent.prompt: session_id is not a chat id — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "session": req.session_id}},
            )
            return ConsentScope.DENY

        rid = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConsentScope] = loop.create_future()
        self._pending[rid] = future

        keyboard = self._build_keyboard(rid, req)
        text = self._build_text(req)

        # 3. STEP — send to the resolved chat; any failure fails closed
        try:
            await self._adapter.send_inline_keyboard(text, keyboard, chat_id=chat_id)
        except Exception as exc:
            self._pending.pop(rid, None)
            log.telegram.error(
                "[telegram] consent.prompt: send failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        try:
            scope = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            log.telegram.warning(
                "[telegram] consent.prompt: timed out — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "timeout_s": self._timeout}},
            )
            return ConsentScope.DENY
        except Exception as exc:
            log.telegram.error(
                "[telegram] consent.prompt: await failed — denying", exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        finally:
            self._pending.pop(rid, None)

        # 4. EXIT
        log.telegram.info(
            "[telegram] consent.prompt: exit",
            extra={"_fields": {"tool": req.tool_name, "scope": scope.value}},
        )
        return scope

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve the pending Future for ``consent:{rid}:{scope}`` callbacks."""
        log.telegram.debug(
            "[telegram] consent.handle_callback: entry",
            extra={"_fields": {"data_prefix": callback_data[:16]}},
        )
        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
            log.telegram.debug("[telegram] consent.handle_callback: not a consent callback — ignored")
            return
        rid, scope_raw = parts[1], parts[2]
        future = self._pending.get(rid)
        if future is None or future.done():
            log.telegram.debug(
                "[telegram] consent.handle_callback: no live request — ignored",
                extra={"_fields": {"rid": rid}},
            )
            return
        try:
            scope = ConsentScope(scope_raw)
        except ValueError:
            log.telegram.warning(
                "[telegram] consent.handle_callback: unknown scope — denying",
                extra={"_fields": {"scope_raw": scope_raw}},
            )
            scope = ConsentScope.DENY
        future.set_result(scope)
        log.telegram.info(
            "[telegram] consent.handle_callback: resolved",
            extra={"_fields": {"rid": rid, "scope": scope.value}},
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_keyboard(self, rid: str, req: ConsentRequest) -> dict[str, object]:
        scopes = list(_BASE_SCOPES)
        if req.allow_relaxation:
            scopes.extend(_RELAXATION_SCOPES)
        builder = InlineKeyboardBuilder()
        for scope in scopes:
            label = localize(_LABEL_KEYS[scope], self._lang)
            builder.add_button(label, f"{_CALLBACK_PREFIX}:{rid}:{scope.value}")
        return builder.build()

    def _build_text(self, req: ConsentRequest) -> str:
        title = localize("consent.prompt.title", self._lang)
        # Tool name + summary give the user the concrete action; title is localized.
        return f"{title}\n\n{req.tool_name}\n{req.summary}".strip()
