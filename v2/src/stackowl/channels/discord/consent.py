"""Discord consent prompter — View/Button round-trip for the consent gate.

Mirrors :class:`~stackowl.channels.telegram.consent.TelegramConsentPrompter`:
the acting pipeline coroutine calls :meth:`DiscordConsentPrompter.prompt`, which
posts a two-button (Approve / Deny) ``discord.ui.View`` and suspends on an
:class:`asyncio.Future`. When the user taps a button the
:class:`~stackowl.channels.discord.callbacks.DiscordCallbackRouter` dispatches the
``consent:`` ``custom_id`` to :meth:`handle_callback`, which resolves the Future
with the chosen :class:`~stackowl.tools.consent.ConsentScope`.

The Discord ``session_id`` (``str(user_id)``) is NOT itself a send target — a
guild reply must reach ``message.channel.id``. So the destination is resolved via
``adapter.resolve_target(session_id)`` (the adapter-owned session→channel map);
an unresolved channel fails CLOSED without a send — silence is never consent.

Fail-closed everywhere: an unresolved target, a send failure, a malformed
callback, an unknown/unoffered scope, or a timeout all resolve to ``DENY``.
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

__all__ = ["DiscordConsentPrompter"]

_CALLBACK_PREFIX = "consent"
# Default time a consent prompt stays open before failing closed.
_DEFAULT_TIMEOUT_SECONDS = 120.0

# Decision → leading symbol, mapped once over the whole ConsentScope enum.
# Language-neutral on purpose (the platform is multilingual): a glyph conveys the
# outcome — ✅ granted, 🔒 scoped allow, ❌ refused — without English copy.
_DECISION_SYMBOLS = {
    ConsentScope.ONCE: "✅",
    ConsentScope.SESSION: "🔒",
    ConsentScope.WINDOW: "🔒",
    ConsentScope.DENY: "❌",
    ConsentScope.DENY_SESSION: "❌",
}
_DEFAULT_SYMBOL = "•"


@dataclass(slots=True)
class _Pending:
    """A live consent prompt: the suspended Future plus the message to edit."""

    future: asyncio.Future[ConsentScope]
    message: Any | None
    summary: str
    # The single APPROVING scope actually drawn (session-if-relaxation-else-once).
    # handle_callback honors ONLY this token + deny_session; any other valid-but-
    # unoffered enum value fails closed to DENY.
    approve_scope: ConsentScope


class _SupportsInlineKeyboard(Protocol):
    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict[str, object],
        channel_id: int | None = None,
    ) -> Any: ...

    def resolve_target(self, session_id: str) -> str | int | None: ...

    async def edit_message_to_text(self, message: Any, text: str) -> None: ...


class DiscordConsentPrompter:
    """Bridges :class:`ConsentPolicy` to a Discord View/Button round-trip."""

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
        """Post the buttons and suspend until a tap resolves them (or timeout)."""
        # 1. ENTRY
        log.discord.debug(
            "[discord] consent.prompt: entry",
            extra={"_fields": {"tool": req.tool_name, "relax": req.allow_relaxation}},
        )
        # 2. DECISION — resolve the INITIATING user's channel from the adapter's
        # session→channel map. The session_id is NOT itself a target, so never
        # guess: an unresolved channel fails CLOSED (deny) without a send.
        dest = self._adapter.resolve_target(req.session_id)
        if not isinstance(dest, int):
            log.discord.error(
                "[discord] consent.prompt: no channel for session — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        rid = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConsentScope] = loop.create_future()
        self._pending[rid] = _Pending(
            future=future,
            message=None,
            summary=req.summary,
            approve_scope=self._approve_scope_for(req),
        )

        keyboard = self._build_keyboard(rid, req)
        text = self._build_text(req)

        # 3. STEP — post to the resolved channel; any failure fails closed.
        try:
            message = await self._adapter.send_inline_keyboard(
                text, keyboard, channel_id=dest
            )
            self._pending[rid].message = message
        except Exception as exc:
            self._pending.pop(rid, None)
            log.discord.error(
                "[discord] consent.prompt: send failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        try:
            scope = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            log.discord.warning(
                "[discord] consent.prompt: timed out — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "timeout_s": self._timeout}},
            )
            return ConsentScope.DENY
        except Exception as exc:
            log.discord.error(
                "[discord] consent.prompt: await failed — denying",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        finally:
            self._pending.pop(rid, None)

        # 4. EXIT
        log.discord.info(
            "[discord] consent.prompt: exit",
            extra={"_fields": {"tool": req.tool_name, "scope": scope.value}},
        )
        return scope

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve the pending Future for ``consent:{rid}:{scope}`` callbacks."""
        log.discord.debug(
            "[discord] consent.handle_callback: entry",
            extra={"_fields": {"data_prefix": callback_data[:16]}},
        )
        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
            log.discord.debug("[discord] consent.handle_callback: not a consent callback — ignored")
            return
        rid, scope_raw = parts[1], parts[2]
        pending = self._pending.get(rid)
        if pending is None or pending.future.done():
            log.discord.debug(
                "[discord] consent.handle_callback: no live request — ignored",
                extra={"_fields": {"rid": rid}},
            )
            return
        try:
            scope = ConsentScope(scope_raw)
        except ValueError:
            log.discord.warning(
                "[discord] consent.handle_callback: unknown scope — denying",
                extra={"_fields": {"scope_raw": scope_raw}},
            )
            scope = ConsentScope.DENY
        else:
            # Honor ONLY a button we actually drew ({approve_scope, deny_session});
            # any other valid-but-unoffered enum value fails CLOSED to DENY.
            offered = {pending.approve_scope, ConsentScope.DENY_SESSION}
            if scope not in offered:
                log.discord.warning(
                    "[discord] consent: unoffered scope token — denying",
                    extra={"_fields": {"rid": rid, "received": scope.value}},
                )
                scope = ConsentScope.DENY
        # Resolve the decision FIRST — the prompt() coroutine must wake regardless
        # of whether the cosmetic message edit below succeeds (fail-open UX).
        pending.future.set_result(scope)
        log.discord.info(
            "[discord] consent.handle_callback: resolved",
            extra={"_fields": {"rid": rid, "scope": scope.value}},
        )
        await self._edit_to_decision(pending, scope)

    async def _edit_to_decision(self, pending: _Pending, scope: ConsentScope) -> None:
        """Best-effort: rewrite the prompt message to "{symbol} {summary}", no buttons."""
        if pending.message is None:
            log.discord.debug("[discord] consent.handle_callback: no message — edit skipped")
            return
        symbol = _DECISION_SYMBOLS.get(scope, _DEFAULT_SYMBOL)
        decision_text = f"{symbol} {pending.summary}".strip()
        try:
            await self._adapter.edit_message_to_text(pending.message, decision_text)
        except Exception as exc:  # fail-open — decision already resolved
            log.discord.error(
                "[discord] consent.handle_callback: message edit failed — decision kept",
                exc_info=exc,
                extra={"_fields": {"scope": scope.value}},
            )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _approve_scope_for(req: ConsentRequest) -> ConsentScope:
        return ConsentScope.SESSION if req.allow_relaxation else ConsentScope.ONCE

    def _build_keyboard(self, rid: str, req: ConsentRequest) -> dict[str, object]:
        approve_scope = self._approve_scope_for(req)
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
        return f"{title}\n\n{req.tool_name}\n{req.summary}".strip()
