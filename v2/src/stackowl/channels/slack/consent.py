"""Slack consent prompter — Block Kit round-trip for the consent gate.

The acting pipeline coroutine calls :meth:`SlackConsentPrompter.prompt`, which
posts a two-button (Approve / Deny) Block Kit message and then suspends on an
:class:`asyncio.Future`. When the user taps a button the Slack ``block_actions``
handler calls :meth:`handle_action`, which resolves the Future with the chosen
:class:`~stackowl.tools.consent.ConsentScope`.

Slack-specific target resolution: the ``slack:{hash}`` ``session_id`` is NOT a
send target. The destination channel is resolved via
``adapter.target_for_session(session_id)`` (the session→channel map populated by
the adapter on inbound events). When that returns ``None`` the prompt fails
CLOSED — never guess a channel.

Fail-closed everywhere: an unresolved target, a send failure, a malformed
action, an unknown scope, or a timeout all resolve to ``DENY`` — silence is
never consent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from stackowl.channels.slack.helpers import (
    ActionsBlock,
    ButtonElement,
    PlainText,
    SectionBlock,
)
from stackowl.infra.observability import log
from stackowl.tools.consent import ConsentRequest, ConsentScope
from stackowl.tui.i18n import localize

if TYPE_CHECKING:
    from stackowl.channels.slack.adapter import SlackChannelAdapter

__all__ = ["SlackConsentPrompter"]

_ACTION_PREFIX = "consent"
# Default time a consent prompt stays open before failing closed.
_DEFAULT_TIMEOUT_SECONDS = 120.0

# Decision → leading symbol, mapped once over the whole ConsentScope enum.
# Language-neutral on purpose (the platform is multilingual): a glyph conveys
# the outcome — ✅ granted, 🔒 scoped/conditional allow, ❌ refused — without any
# English copy. The original action summary follows the symbol.
_DECISION_SYMBOLS = {
    ConsentScope.ONCE: "✅",
    ConsentScope.SESSION: "🔒",
    ConsentScope.WINDOW: "🔒",
    ConsentScope.DENY: "❌",
    ConsentScope.DENY_SESSION: "❌",
}
# Fallback symbol for any scope not explicitly mapped (defensive — keeps the
# update best-effort rather than raising a KeyError mid-resolution).
_DEFAULT_SYMBOL = "•"


@dataclass(slots=True)
class _Pending:
    """A live consent prompt: the suspended Future plus the message to update."""

    future: asyncio.Future[ConsentScope]
    channel: str
    message_ts: str | None
    summary: str


class SlackConsentPrompter:
    """Bridges :class:`ConsentPolicy` to a Slack Block Kit round-trip."""

    def __init__(
        self,
        adapter: SlackChannelAdapter,
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
        log.slack.debug(
            "[slack] consent.prompt: entry",
            extra={"_fields": {"tool": req.tool_name, "relax": req.allow_relaxation}},
        )
        # 2. DECISION — resolve the Slack destination from the session→channel map.
        # The session_id (``slack:{hash}``) is NOT itself a target, so we never
        # guess: an unresolved channel fails CLOSED (deny) without a send.
        dest = self._adapter.target_for_session(req.session_id)
        if dest is None:
            log.slack.error(
                "[slack] consent.prompt: no Slack target for session — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "session": req.session_id}},
            )
            return ConsentScope.DENY

        rid = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConsentScope] = loop.create_future()
        # Stash the action summary so handle_action can rewrite the message to
        # "{symbol} {summary}" without re-deriving it. message_ts is filled in
        # once the post returns the timestamp.
        self._pending[rid] = _Pending(
            future=future, channel=dest, message_ts=None, summary=req.summary
        )

        blocks = self._build_blocks(rid, req)
        text = self._build_text(req)
        thread_ts = self._thread_for(dest)

        # 3. STEP — post to the resolved channel; any failure fails closed.
        client = self._client()
        if client is None:
            self._pending.pop(rid, None)
            log.slack.error(
                "[slack] consent.prompt: no Slack client attached — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        post_kwargs: dict[str, Any] = {"channel": dest, "text": text, "blocks": blocks}
        if thread_ts is not None:
            post_kwargs["thread_ts"] = thread_ts
        try:
            result = await client.chat_postMessage(**post_kwargs)
            # Capture the message ts so the tap handler can update it later.
            # Defensive: a best-effort post may return None / lack a ts — a
            # missing ts simply skips the cosmetic update (decision still works).
            self._pending[rid].message_ts = self._extract_ts(result)
        except Exception as exc:
            self._pending.pop(rid, None)
            log.slack.error(
                "[slack] consent.prompt: post failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        try:
            scope = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            log.slack.warning(
                "[slack] consent.prompt: timed out — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "timeout_s": self._timeout}},
            )
            return ConsentScope.DENY
        except Exception as exc:
            log.slack.error(
                "[slack] consent.prompt: await failed — denying",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        finally:
            self._pending.pop(rid, None)

        # 4. EXIT
        log.slack.info(
            "[slack] consent.prompt: exit",
            extra={"_fields": {"tool": req.tool_name, "scope": scope.value}},
        )
        return scope

    async def handle_action(self, action_id_or_value: str) -> None:
        """Resolve the pending Future for ``consent:{rid}:{scope}`` actions."""
        log.slack.debug(
            "[slack] consent.handle_action: entry",
            extra={"_fields": {"data_prefix": action_id_or_value[:16]}},
        )
        parts = action_id_or_value.split(":")
        if len(parts) != 3 or parts[0] != _ACTION_PREFIX:
            log.slack.debug("[slack] consent.handle_action: not a consent action — ignored")
            return
        rid, scope_raw = parts[1], parts[2]
        pending = self._pending.get(rid)
        if pending is None or pending.future.done():
            log.slack.debug(
                "[slack] consent.handle_action: no live request — ignored",
                extra={"_fields": {"rid": rid}},
            )
            return
        try:
            scope = ConsentScope(scope_raw)
        except ValueError:
            log.slack.warning(
                "[slack] consent.handle_action: unknown scope — denying",
                extra={"_fields": {"scope_raw": scope_raw}},
            )
            scope = ConsentScope.DENY
        # Resolve the decision FIRST — the prompt() coroutine must wake regardless
        # of whether the cosmetic message update below succeeds (fail-open UX).
        pending.future.set_result(scope)
        log.slack.info(
            "[slack] consent.handle_action: resolved",
            extra={"_fields": {"rid": rid, "scope": scope.value}},
        )
        # UX: rewrite the original prompt to the chosen decision and drop the
        # buttons so it reads as resolved and can't be re-tapped. Best-effort —
        # the decision is already recorded; a failed update must never lose it.
        await self._update_to_decision(pending, scope)

    async def _update_to_decision(self, pending: _Pending, scope: ConsentScope) -> None:
        """Best-effort: rewrite the prompt message to "{symbol} {summary}", no buttons."""
        if pending.message_ts is None:
            log.slack.debug("[slack] consent.handle_action: no message_ts — update skipped")
            return
        client = self._client()
        if client is None:
            log.slack.debug("[slack] consent.handle_action: no client — update skipped")
            return
        symbol = _DECISION_SYMBOLS.get(scope, _DEFAULT_SYMBOL)
        decision_text = f"{symbol} {pending.summary}".strip()
        decision_block = SectionBlock(text=PlainText(text=decision_text))
        try:
            await client.chat_update(
                channel=pending.channel,
                ts=pending.message_ts,
                text=decision_text,
                blocks=[decision_block.model_dump()],
            )
        except Exception as exc:  # fail-open — decision already resolved
            log.slack.error(
                "[slack] consent.handle_action: message update failed — decision kept",
                exc_info=exc,
                extra={
                    "_fields": {
                        "channel": pending.channel,
                        "message_ts": pending.message_ts,
                        "scope": scope.value,
                    }
                },
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_blocks(self, rid: str, req: ConsentRequest) -> list[dict[str, object]]:
        approve_scope = (
            ConsentScope.SESSION if req.allow_relaxation else ConsentScope.ONCE
        )
        prompt_block = SectionBlock(text=PlainText(text=self._build_text(req)))
        approve = ButtonElement(
            text=PlainText(text=localize("consent.btn.approve", self._lang)),
            action_id=f"{_ACTION_PREFIX}:{rid}:{approve_scope.value}",
            value=f"{_ACTION_PREFIX}:{rid}:{approve_scope.value}",
        )
        deny = ButtonElement(
            text=PlainText(text=localize("consent.btn.deny", self._lang)),
            action_id=f"{_ACTION_PREFIX}:{rid}:{ConsentScope.DENY_SESSION.value}",
            value=f"{_ACTION_PREFIX}:{rid}:{ConsentScope.DENY_SESSION.value}",
        )
        actions = ActionsBlock(elements=[approve, deny])
        return [prompt_block.model_dump(), actions.model_dump()]

    def _build_text(self, req: ConsentRequest) -> str:
        title = localize("consent.prompt.title", self._lang)
        # Tool name + summary give the user the concrete action; title is localized.
        return f"{title}\n\n{req.tool_name}\n{req.summary}".strip()

    def _thread_for(self, channel: str) -> str | None:
        """Resolve the originating thread_ts for ``channel`` (DM path → None).

        Mirrors the adapter's send-time threading: a channel reply threads under
        the originating message, a DM replies to the channel root.
        """
        threads = getattr(self._adapter, "_threads", None)
        if isinstance(threads, dict):
            value = threads.get(channel)
            return value if isinstance(value, str) else None
        return None

    def _client(self) -> Any | None:
        """Return the live Bolt client (``app.client``), or None when unattached."""
        app = getattr(self._adapter, "_app", None)
        if app is None:
            return None
        return getattr(app, "client", None)

    @staticmethod
    def _extract_ts(result: object) -> str | None:
        """Pull the message ``ts`` from a chat_postMessage response, if present."""
        if isinstance(result, dict):
            ts = result.get("ts")
            return ts if isinstance(ts, str) else None
        return None
