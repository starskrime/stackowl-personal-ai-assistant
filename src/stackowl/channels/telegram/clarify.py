"""Telegram clarify resolver — inline-button tap → resolves the parked turn.

When the owl calls ``clarify`` with choices on a Telegram session, the adapter
renders one inline button per choice whose ``callback_data`` is
``clarify:{clarify_id}:{idx}`` (see ``TelegramChannelAdapter.send_clarify``).
Tapping a button delivers a ``callback_query`` that the shared
:class:`~stackowl.channels.telegram.callbacks.CallbackRouter` dispatches here by
the ``clarify:`` prefix.

:class:`TelegramClarifyResolver` mirrors the consent prompter's
``handle_callback`` structure, but resolves through the
:class:`~stackowl.interaction.clarify_gateway.ClarifyGateway` rather than a local
Future: it maps the tapped ``idx`` back to the entry's choice TEXT and calls
``try_resolve_by_id`` with the disambiguating ``clarify_id`` the tap carries —
which sets the entry's event and wakes the parked turn IN PLACE (the resumed
turn's already-running decoupled send delivers the continuation). Resolving by
id (not by a session+channel re-match) keeps the tap correct even if the
cap-one-per-session rule is ever relaxed. This runs in PARALLEL with the existing
text-reply path (a typed reply matches by session+channel since it carries no
id); either resolves the same parked clarify.

No confirmation message is sent — the owl's resumed continuation is the visible
response. Fail-safe: a malformed callback, a stale/superseded ``clarify_id``, or
an out-of-range index is logged and ignored (the router still acks the tap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway

__all__ = ["TelegramClarifyResolver"]

# Prefix the orchestrator registers on the CallbackRouter (`router.register`).
_CALLBACK_PREFIX = "clarify"


class TelegramClarifyResolver:
    """Resolves a parked clarify when the user taps an inline choice button."""

    def __init__(self, clarify_gateway: ClarifyGateway) -> None:
        self._gateway = clarify_gateway

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve the parked clarify for a ``clarify:{clarify_id}:{idx}`` tap.

        Parses the callback, maps ``idx`` to the entry's choice text via
        :meth:`ClarifyGateway.peek`, and calls
        :meth:`ClarifyGateway.try_resolve_by_id` (keyed on the tapped
        ``clarify_id``) to wake the parked turn. Idempotent and fail-safe: a malformed payload, a
        stale/superseded ``clarify_id`` (peek → ``None``), or an out-of-range
        index is logged and ignored. Never raises (the router catches, but this
        stays clean). ``callback_id`` is accepted for signature parity with the
        router's handler contract and is not otherwise needed here.
        """
        log.telegram.debug(
            "[telegram] clarify.handle_callback: entry",
            extra={"_fields": {"data_prefix": callback_data[:24]}},
        )
        # 2. DECISION — parse + validate the callback payload.
        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
            log.telegram.debug(
                "[telegram] clarify.handle_callback: not a clarify callback — ignored",
            )
            return
        clarify_id, idx_raw = parts[1], parts[2]
        try:
            idx = int(idx_raw)
        except ValueError:
            log.telegram.warning(
                "[telegram] clarify.handle_callback: non-int index — ignored",
                extra={"_fields": {"idx_raw": idx_raw}},
            )
            return

        # 3. STEP — read-only lookup; a missing entry means the question was
        # already answered / superseded / expired (a stale tap). Idempotent.
        entry = self._gateway.peek(clarify_id)
        if entry is None:
            log.telegram.info(
                "[telegram] clarify.handle_callback: stale clarify tap — ignored",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return
        if idx < 0 or idx >= len(entry.choices):
            log.telegram.warning(
                "[telegram] clarify.handle_callback: index out of range — ignored",
                extra={"_fields": {"clarify_id": clarify_id, "idx": idx, "n": len(entry.choices)}},
            )
            return

        text = entry.choices[idx]
        # Resolve through the gateway BY THE TAPPED id — the disambiguating
        # clarify_id the tap carries, not a session+channel re-match. This keeps
        # the resolve correct even if cap-one-per-session is relaxed (a session
        # could then hold several pending entries; a session-match would resolve
        # whichever is first, maybe not the tapped one). Sets the event + wakes
        # the parked turn in place (the resumed turn's decoupled send delivers
        # the continuation).
        # no await may be added between peek and try_resolve_by_id.
        self._gateway.try_resolve_by_id(clarify_id, text)
        # 4. EXIT
        log.telegram.info(
            "[telegram] clarify.handle_callback: resolved",
            extra={
                "_fields": {
                    "clarify_id": clarify_id,
                    "idx": idx,
                    "session_id": entry.session_id,
                    "channel": entry.channel,
                }
            },
        )
