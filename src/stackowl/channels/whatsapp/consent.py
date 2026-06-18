"""WhatsApp consent prompter — numbered-text round-trip for the consent gate.

WhatsApp is text-only (no buttons), so consent degrades to a numbered prompt:
the acting pipeline coroutine calls :meth:`WhatsAppConsentPrompter.prompt`, which
posts a two-line ``1. approve / 2. deny`` message and suspends on an
:class:`asyncio.Future`. The user's NEXT inbound message resolves it: the
WhatsApp loop calls :meth:`resolve_reply` (BEFORE the clarify pump) — a reply of
``1`` grants, ``2`` denies, anything else stays parked (the message runs as an
ordinary turn; consent is never guessed).

The WhatsApp ``session_id`` is a LOSSY hash of the JID, so the destination is
resolved via ``adapter.resolve_target(session_id)`` (the adapter-owned
session→JID map). An unresolved JID fails CLOSED without a send — silence is
never consent.

Numbering is language-neutral (``N. label``; labels via :func:`localize`) so no
English is hardcoded. At most ONE consent is parked per session at a time (a
second prompt for a busy session fails closed rather than racing two Futures).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tools.consent import ConsentRequest, ConsentScope
from stackowl.tui.i18n import localize

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter

__all__ = ["WhatsAppConsentPrompter"]

# Default time a consent prompt stays open before failing closed.
_DEFAULT_TIMEOUT_SECONDS = 120.0
# The numbered reply tokens (control tokens, NOT user-facing copy — the labels
# beside them are localized). "1" approves, "2" denies.
_APPROVE_CHOICE = "1"
_DENY_CHOICE = "2"


@dataclass(slots=True)
class _Pending:
    """A live consent prompt: the suspended Future + the approving scope drawn."""

    future: asyncio.Future[ConsentScope]
    approve_scope: ConsentScope


class WhatsAppConsentPrompter:
    """Bridges :class:`ConsentPolicy` to a WhatsApp numbered-text round-trip."""

    def __init__(
        self,
        adapter: WhatsAppChannelAdapter,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        lang: str = "auto",
    ) -> None:
        self._adapter = adapter
        self._timeout = timeout_seconds
        self._lang = lang
        # At most one parked consent per session (keyed by session_id).
        self._pending: dict[str, _Pending] = {}

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        """Post the numbered prompt and suspend until a reply resolves it."""
        # 1. ENTRY
        log.whatsapp.debug(
            "[whatsapp] consent.prompt: entry",
            extra={"_fields": {"tool": req.tool_name, "relax": req.allow_relaxation}},
        )
        # 2. DECISION — resolve the initiating session's JID; never guess.
        dest = self._adapter.resolve_target(req.session_id)
        if not isinstance(dest, str) or not dest:
            log.whatsapp.error(
                "[whatsapp] consent.prompt: no JID for session — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        # Only one parked consent per session — a second prompt while one is live
        # fails closed rather than racing two Futures over one reply stream.
        if req.session_id in self._pending and not self._pending[req.session_id].future.done():
            log.whatsapp.warning(
                "[whatsapp] consent.prompt: a consent is already parked — denying",
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConsentScope] = loop.create_future()
        approve_scope = (
            ConsentScope.SESSION if req.allow_relaxation else ConsentScope.ONCE
        )
        self._pending[req.session_id] = _Pending(
            future=future, approve_scope=approve_scope
        )

        text = self._build_text(req)
        # 3. STEP — send to the resolved JID; any failure fails closed.
        try:
            await self._adapter.send_text(text, target=dest)
        except Exception as exc:
            self._pending.pop(req.session_id, None)
            log.whatsapp.error(
                "[whatsapp] consent.prompt: send failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY

        try:
            scope = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            log.whatsapp.warning(
                "[whatsapp] consent.prompt: timed out — denying (fail closed)",
                extra={"_fields": {"tool": req.tool_name, "timeout_s": self._timeout}},
            )
            return ConsentScope.DENY
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] consent.prompt: await failed — denying",
                exc_info=exc,
                extra={"_fields": {"tool": req.tool_name}},
            )
            return ConsentScope.DENY
        finally:
            self._pending.pop(req.session_id, None)

        # 4. EXIT
        log.whatsapp.info(
            "[whatsapp] consent.prompt: exit",
            extra={"_fields": {"tool": req.tool_name, "scope": scope.value}},
        )
        return scope

    async def resolve_reply(self, session_id: str, text: str) -> bool:
        """Resolve a parked consent from the user's next inbound reply.

        Called by the WhatsApp loop BEFORE the clarify pump. Returns ``True`` iff
        the reply RESOLVED a parked consent (the loop must start NO new turn);
        ``False`` when nothing is parked OR the reply is unparseable (the message
        runs as an ordinary turn — consent stays parked, never guessed).
        """
        log.whatsapp.debug(
            "[whatsapp] consent.resolve_reply: entry",
            extra={"_fields": {"has_pending": session_id in self._pending}},
        )
        pending = self._pending.get(session_id)
        if pending is None or pending.future.done():
            return False
        choice = text.strip()
        if choice == _APPROVE_CHOICE:
            scope = pending.approve_scope
        elif choice == _DENY_CHOICE:
            scope = ConsentScope.DENY_SESSION
        else:
            # Unparseable — do NOT guess. Stays parked; the message is a normal turn.
            log.whatsapp.debug(
                "[whatsapp] consent.resolve_reply: unparseable reply — staying parked",
            )
            return False
        pending.future.set_result(scope)
        log.whatsapp.info(
            "[whatsapp] consent.resolve_reply: resolved",
            extra={"_fields": {"scope": scope.value}},
        )
        return True

    def _build_text(self, req: ConsentRequest) -> str:
        """Build the localized, language-neutral numbered consent prompt."""
        title = localize("consent.prompt.title", self._lang)
        approve_label = localize("consent.btn.approve", self._lang)
        deny_label = localize("consent.btn.deny", self._lang)
        # The numeric cadence is language-neutral; the labels are localized.
        return (
            f"{title}\n\n{req.tool_name}\n{req.summary}\n\n"
            f"{_APPROVE_CHOICE}. {approve_label}\n{_DENY_CHOICE}. {deny_label}"
        ).strip()
