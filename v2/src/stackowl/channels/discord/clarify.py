"""Discord clarify resolver — button tap → resolves the parked turn.

When the owl calls ``clarify`` with choices on a Discord session, the adapter
renders one ``discord.ui.Button`` per choice whose ``custom_id`` is
``clarify:{clarify_id}:{idx}`` (see ``DiscordChannelAdapter.send_clarify``).
Tapping a button fires an interaction that the
:class:`~stackowl.channels.discord.callbacks.DiscordCallbackRouter` dispatches
here by the ``clarify:`` prefix.

:class:`DiscordClarifyResolver` mirrors the Telegram/Slack resolvers exactly: it
maps the tapped ``idx`` back to the entry's choice TEXT via
:meth:`~stackowl.interaction.clarify_gateway.ClarifyGateway.peek` and calls
:meth:`~stackowl.interaction.clarify_gateway.ClarifyGateway.try_resolve_by_id`
with the disambiguating ``clarify_id`` the tap carries — which sets the entry's
event and wakes the parked turn IN PLACE (the resumed turn's already-running
decoupled send delivers the continuation). Resolving by id (not by a
session+channel re-match) keeps the tap correct even if the cap-one-per-session
rule is ever relaxed. This runs in PARALLEL with the typed text-reply path.

No confirmation message is sent — the owl's resumed continuation is the visible
response. Fail-safe: a malformed callback, a stale/superseded ``clarify_id``, or
an out-of-range index is logged and ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway

__all__ = ["DiscordClarifyResolver"]

# Prefix the orchestrator registers on the DiscordCallbackRouter (`router.register`).
_CALLBACK_PREFIX = "clarify"


class DiscordClarifyResolver:
    """Resolves a parked clarify when the user taps a Discord choice button."""

    def __init__(self, clarify_gateway: ClarifyGateway) -> None:
        self._gateway = clarify_gateway

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        """Resolve the parked clarify for a ``clarify:{clarify_id}:{idx}`` tap.

        Parses the callback, maps ``idx`` to the entry's choice text via
        :meth:`ClarifyGateway.peek`, and calls
        :meth:`ClarifyGateway.try_resolve_by_id` (keyed on the tapped
        ``clarify_id``) to wake the parked turn. Idempotent and fail-safe: a
        malformed payload, a stale/superseded ``clarify_id`` (peek → ``None``),
        or an out-of-range index is logged and ignored. ``callback_id`` is
        accepted for router-handler signature parity and is not otherwise needed.
        """
        log.discord.debug(
            "[discord] clarify.handle_callback: entry",
            extra={"_fields": {"data_prefix": callback_data[:24]}},
        )
        # 2. DECISION — parse + validate the callback payload.
        parts = callback_data.split(":")
        if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
            log.discord.debug(
                "[discord] clarify.handle_callback: not a clarify callback — ignored",
            )
            return
        clarify_id, idx_raw = parts[1], parts[2]
        try:
            idx = int(idx_raw)
        except ValueError:
            log.discord.warning(
                "[discord] clarify.handle_callback: non-int index — ignored",
                extra={"_fields": {"idx_raw": idx_raw}},
            )
            return

        # 3. STEP — read-only lookup; a missing entry means the question was
        # already answered / superseded / expired (a stale tap). Idempotent.
        entry = self._gateway.peek(clarify_id)
        if entry is None:
            log.discord.info(
                "[discord] clarify.handle_callback: stale clarify tap — ignored",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return
        if idx < 0 or idx >= len(entry.choices):
            log.discord.warning(
                "[discord] clarify.handle_callback: index out of range — ignored",
                extra={"_fields": {"clarify_id": clarify_id, "idx": idx, "n": len(entry.choices)}},
            )
            return

        text = entry.choices[idx]
        # Resolve through the gateway BY THE TAPPED id — keeps the resolve correct
        # even if cap-one-per-session is relaxed. Sets the event + wakes the parked
        # turn in place. No await may be added between peek and try_resolve_by_id.
        self._gateway.try_resolve_by_id(clarify_id, text)
        # 4. EXIT
        log.discord.info(
            "[discord] clarify.handle_callback: resolved",
            extra={
                "_fields": {
                    "clarify_id": clarify_id,
                    "idx": idx,
                    "session_id": entry.session_id,
                    "channel": entry.channel,
                }
            },
        )
