"""Telegram consent prompter — inline-keyboard round-trip for the consent gate.

The acting pipeline coroutine calls :meth:`TelegramConsentPrompter.prompt`,
which sends a two-button (Approve / Deny) inline keyboard and then
suspends on an :class:`asyncio.Future`. When the user taps a button the
Telegram callback-query handler calls :meth:`handle_callback`, which resolves
the Future with the chosen :class:`~stackowl.tools.consent.ConsentScope`.

Fail-closed: a send failure, a malformed callback, or a timeout all resolve to
``DENY`` — silence is never consent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.infra.observability import log
from stackowl.tools.consent import ConsentRequest, ConsentScope
from stackowl.tui.i18n import localize

__all__ = ["TelegramConsentPrompter"]

_CALLBACK_PREFIX = "consent"
# Default time a consent prompt stays open before failing closed.
_DEFAULT_TIMEOUT_SECONDS = 120.0

# Decision → leading symbol, mapped once over the whole ConsentScope enum.
# Language-neutral on purpose (the platform is multilingual): a glyph conveys
# the outcome — ✅ granted, 🔒 scoped/conditional allow, ❌ refused — without
# any English copy. The original action summary follows the symbol.
_DECISION_SYMBOLS = {
    ConsentScope.ONCE: "✅",
    ConsentScope.SESSION: "🔒",
    ConsentScope.WINDOW: "🔒",
    ConsentScope.DENY: "❌",
    ConsentScope.DENY_SESSION: "❌",
}
# Fallback symbol for any scope not explicitly mapped (defensive — keeps the
# edit best-effort rather than raising a KeyError mid-resolution).
_DEFAULT_SYMBOL = "•"


@dataclass(slots=True)
class _Pending:
    """A live consent prompt: the suspended Future plus the message to edit."""

    future: asyncio.Future[ConsentScope]
    chat_id: int
    message_id: int | None
    summary: str


class _SupportsInlineKeyboard(Protocol):
    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict[str, object],
        chat_id: int | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> Any: ...

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Any | None = None,
    ) -> bool: ...


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
        self._pending: dict[str, _Pending] = {}

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
        # Stash the action summary so handle_callback can rewrite the message to
        # "{symbol} {summary}" without re-deriving it. message_id is filled in
        # once the send returns the Message.
        self._pending[rid] = _Pending(
            future=future, chat_id=chat_id, message_id=None, summary=req.summary
        )

        keyboard = self._build_keyboard(rid, req)
        text = self._build_text(req)

        # 3. STEP — send to the resolved chat; any failure fails closed. A consent
        # prompt is RAW text (tool name + a literal shell command, paths, '.'/'-'/'='
        # /'/' chars). MarkdownV2 would reject those unescaped → HTTP 400 → fail
        # closed → spurious DENY. So send as plain text (parse_mode=None): a consent
        # prompt needs no markdown and plain text can never 400 on entity parsing.
        try:
            message = await self._adapter.send_inline_keyboard(
                text, keyboard, chat_id=chat_id, parse_mode=None
            )
            # Capture the message identity so the tap handler can edit it later.
            # Defensive getattr: a no-target/best-effort send may return None, and
            # a missing id simply skips the cosmetic edit (decision still works).
            self._pending[rid].message_id = getattr(message, "message_id", None)
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
        pending = self._pending.get(rid)
        if pending is None or pending.future.done():
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
        # Resolve the decision FIRST — the prompt() coroutine must wake regardless
        # of whether the cosmetic message edit below succeeds (fail-open UX).
        pending.future.set_result(scope)
        log.telegram.info(
            "[telegram] consent.handle_callback: resolved",
            extra={"_fields": {"rid": rid, "scope": scope.value}},
        )
        # UX: rewrite the original prompt to the chosen decision and drop the
        # keyboard so it reads as resolved and can't be re-tapped. Best-effort —
        # the decision is already recorded; a failed edit must never lose it.
        await self._edit_to_decision(pending, scope)

    async def _edit_to_decision(self, pending: _Pending, scope: ConsentScope) -> None:
        """Best-effort: rewrite the prompt message to "{symbol} {summary}", no keys."""
        if pending.message_id is None:
            log.telegram.debug(
                "[telegram] consent.handle_callback: no message_id — edit skipped",
            )
            return
        symbol = _DECISION_SYMBOLS.get(scope, _DEFAULT_SYMBOL)
        decision_text = f"{symbol} {pending.summary}".strip()
        try:
            await self._adapter.edit_message(
                pending.chat_id, pending.message_id, decision_text, reply_markup=None
            )
        except Exception as exc:  # fail-open — decision already resolved
            log.telegram.error(
                "[telegram] consent.handle_callback: message edit failed — decision kept",
                exc_info=exc,
                extra={"_fields": {
                    "chat_id": pending.chat_id, "message_id": pending.message_id,
                    "scope": scope.value,
                }},
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_keyboard(self, rid: str, req: ConsentRequest) -> dict[str, object]:
        approve_scope = ConsentScope.SESSION if req.allow_relaxation else ConsentScope.ONCE
        builder = InlineKeyboardBuilder()
        builder.add_button(
            localize("consent.btn.approve", self._lang),
            f"{_CALLBACK_PREFIX}:{rid}:{approve_scope.value}",
        )
        builder.add_button(
            localize("consent.btn.deny", self._lang),
            f"{_CALLBACK_PREFIX}:{rid}:{ConsentScope.DENY_SESSION.value}",
        )
        return builder.build()

    def _build_text(self, req: ConsentRequest) -> str:
        title = localize("consent.prompt.title", self._lang)
        # Tool name + summary give the user the concrete action; title is localized.
        return f"{title}\n\n{req.tool_name}\n{req.summary}".strip()
