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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from stackowl.channels.chat_id import chat_id_from_session
from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder
from stackowl.infra.observability import log
from stackowl.tools.consent import (
    HUMAN_DECISION_TIMEOUT_SECONDS,
    ConsentRequest,
    ConsentScope,
)
from stackowl.tui.i18n import localize

__all__ = ["TelegramConsentPrompter"]

_CALLBACK_PREFIX = "consent"
# Default time a consent prompt stays open before failing closed.
#: How long an approval button stays live. Bakir, 2026-08-20: "I see approve button
#: i am clicking but no reaction" — and "Increase 120 to 1200".
#:
#: 120s was shorter than a phone notification often takes to reach someone, and the
#: INFO line a resolved click writes (`consent.handle_callback: resolved`) had NEVER
#: appeared in any log: no click had ever landed inside the window. Every prompt
#: expired, failed closed, and was recorded as `user_denied` — blaming him for a
#: refusal he never made.
#: ASKED, NEVER RESTATED. This was a local literal until 2026-09-10, when five
#: copies of one deadline were found disagreeing — this copy already held the
#: right value and was overridden by the bridge underneath it.
#: Failure shape #3: one source, and the others ask it.
_DEFAULT_TIMEOUT_SECONDS = HUMAN_DECISION_TIMEOUT_SECONDS

#: How many resolved requests stay answerable after they resolve.
#:
#: Sized for the case it exists to fix — a person tapping a second button on a
#: prompt they just answered, seconds later — with enough slack that a busy hour
#: of approvals cannot push a still-visible prompt out of memory. Past it the
#: reply degrades to "I no longer have that request", which is true, rather than
#: to a claim that it expired, which was not.
_DECIDED_MEMORY = 256

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
        # WHAT WAS DECIDED, kept after the request resolves.
        #
        # Bakir, 2026-09-10: "I do not like when approval is expiring. It kills
        # all platform vibe." MEASURED on the run he was describing: his tap
        # RESOLVED at 01:46:51 and `skill_manage` ran in the same second — the job
        # continued and finished. Two further taps on that same, already-answered
        # request, 11s and 25s later, were told "That approval request has
        # expired, so the tap did nothing. Ask me again and I'll re-request it."
        #
        # Every word of that was wrong: the tap HAD done something, it had not
        # expired, and asking again would have re-run a consequential action that
        # had already run. Without this map the prompter cannot tell an ANSWERED
        # request from a LOST one, because both are simply absent from _pending.
        #
        # BOUNDED, because failure shape #4 in CLAUDE.md is anything that only
        # appends. An OrderedDict used as an LRU keeps the newest _DECIDED_MEMORY
        # and forgets the rest; forgetting degrades to the honest "I no longer
        # have that request" branch, never to the false expiry claim.
        self._decided: OrderedDict[str, ConsentScope] = OrderedDict()

    def _remember_decision(self, rid: str, scope: ConsentScope) -> None:
        """Record how a request ended so a later tap can be answered truthfully."""
        self._decided[rid] = scope
        self._decided.move_to_end(rid)
        while len(self._decided) > _DECIDED_MEMORY:
            self._decided.popitem(last=False)

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        """Send the keyboard and suspend until a button resolves it (or timeout)."""
        # 1. ENTRY
        log.telegram.debug(
            "[telegram] consent.prompt: entry",
            extra={"_fields": {"tool": req.tool_name, "relax": req.allow_relaxation}},
        )
        # 2. DECISION — target the INITIATING user's chat (session_key == Telegram
        # user id), never a shared/last chat (prevents a confused-deputy where a
        # different user sees/answers the prompt). Fail closed if unresolvable.
        # ADDRESS FIRST, IDENTITY ONLY AS A FALLBACK. `reply_target` is the turn's
        # own chat, threaded from IngressMessage.chat_id — the same value the
        # deliver step uses so a reply cannot land in someone else's window. It was
        # never passed to consent, so this prompter had only the session KEY, did
        # `int(session_key)`, and failed closed on every structured lane. That is
        # what denied Bakir every owl_build from Telegram on 2026-08-19.
        #
        # The session-key read stays for callers that legitimately have no turn
        # target (proactive/recovery paths, where the key IS a bare chat id).
        chat_id: int | None = None
        target = req.reply_target
        if isinstance(target, int):
            chat_id = target
        elif isinstance(target, str) and target.strip():
            chat_id = chat_id_from_session(target)
        if chat_id is None:
            chat_id = chat_id_from_session(req.session_key)
        if chat_id is None:
            log.telegram.error(
                "[telegram] consent.prompt: session_key is not a chat id — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "session": req.session_key}},
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
            # REMEMBER THAT IT TIMED OUT. A tap arriving after the window closed
            # deserves to be told that specifically — it is the one case where
            # "expired" is the true word, and it is distinguishable from a request
            # that was answered only because this line records which happened.
            self._remember_decision(rid, ConsentScope.DENY)
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

    async def handle_callback(
        self, callback_id: str, callback_data: str, chat_id: int | None = None
    ) -> None:
        """Resolve the pending Future for ``consent:{rid}:{scope}`` callbacks.

        ``chat_id`` is accepted for signature parity with the router's
        handler contract (the pending entry already carries its own
        authoritative chat_id) and is not otherwise needed here.
        """
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
            # THREE STATES, NOT ONE. This branch is reached by a request that was
            # ANSWERED, one that TIMED OUT, and one this process never saw — and
            # it used to answer all three with "That approval request has expired,
            # so the tap did nothing. Ask me again and I'll re-request it."
            #
            # For the commonest of the three that is false in every clause, and
            # MEASURED so on 2026-09-10: the tap resolved at 01:46:51 and
            # `skill_manage` ran in the same second, then two further taps at
            # 01:47:02 and 01:47:16 were told nothing had happened. Telling
            # someone to ask again for a consequential action that already ran is
            # an invitation to do it twice.
            decided = self._decided.get(rid)
            state = (
                "already_answered" if decided is not None and decided != ConsentScope.DENY
                else "already_declined" if decided is not None
                else "not_known_to_this_process"
            )
            # INFO, not DEBUG: production runs at INFO, and this line is the
            # evidence that a click arrived at all.
            log.telegram.info(
                "[telegram] consent.handle_callback: a tap arrived for a request "
                "that is no longer pending — telling the user which of the three "
                "things actually happened",
                extra={"_fields": {
                    "rid": rid, "chat_id": chat_id, "state": state,
                    "scope": decided.value if decided is not None else None,
                }},
            )
            if chat_id is not None:
                await self._tell_the_truth_about(rid, chat_id, state)
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
        self._remember_decision(rid, scope)
        log.telegram.info(
            "[telegram] consent.handle_callback: resolved",
            extra={"_fields": {"rid": rid, "scope": scope.value}},
        )
        # UX: rewrite the original prompt to the chosen decision and drop the
        # keyboard so it reads as resolved and can't be re-tapped. Best-effort —
        # the decision is already recorded; a failed edit must never lose it.
        await self._edit_to_decision(pending, scope)

    async def _tell_the_truth_about(self, rid: str, chat_id: int, state: str) -> None:
        """Reply to a tap on a request that is no longer pending. Never raises.

        NO SENTENCE HERE MAY CLAIM SOMETHING THIS METHOD DID NOT CHECK. The text
        it replaces asserted an expiry, a no-op and a re-request in eleven words,
        and on the measured occurrence all three were untrue.
        """
        messages = {
            # The one that bit him. The decision stood and the work went ahead, so
            # this says exactly that and asks for nothing.
            "already_answered": (
                "You already approved that one — it went ahead. "
                "Nothing more to do."
            ),
            "already_declined": (
                "You already declined that one, so nothing ran."
            ),
            # The honest version of the old message: this process has no record of
            # the request, which happens when it was raised before a restart. It
            # does NOT claim the request expired, because that is not known here.
            "not_known_to_this_process": (
                "I no longer have that request — it was raised before my last "
                "restart, so nothing ran. Tell me to go ahead and I'll redo it."
            ),
        }
        try:
            await self._adapter.send_inline_keyboard(
                messages[state], {}, chat_id=chat_id, parse_mode=None,
            )
        except Exception as exc:  # never raise into the callback router
            log.telegram.error(
                "[telegram] consent.handle_callback: could not tell the user what "
                "happened to their approval",
                exc_info=exc, extra={"_fields": {"rid": rid, "state": state}},
            )

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
