"""Slack clarify resolver — button tap → resolves the parked turn.

When the owl calls ``clarify`` with choices on a Slack session, the adapter
renders one Block Kit button per choice whose ``action_id``/``value`` is
``clarify:{clarify_id}:{idx}`` (see ``SlackChannelAdapter.send_clarify``).
Tapping a button delivers a ``block_actions`` payload that the shared
:class:`~stackowl.channels.slack.callbacks.SlackActionRouter` dispatches here by
the ``clarify:`` prefix.

:class:`SlackClarifyResolver` mirrors the Telegram resolver exactly: it maps the
tapped ``idx`` back to the entry's choice TEXT via
:meth:`~stackowl.interaction.clarify_gateway.ClarifyGateway.peek` and calls
:meth:`~stackowl.interaction.clarify_gateway.ClarifyGateway.try_resolve_by_id`
with the disambiguating ``clarify_id`` the tap carries — which sets the entry's
event and wakes the parked turn IN PLACE (the resumed turn's already-running
decoupled send delivers the continuation). Resolving by id (not by a
session+channel re-match) keeps the tap correct even if the
cap-one-per-session rule is ever relaxed. This runs in PARALLEL with the typed
text-reply path (a typed reply matches by session+channel since it carries no
id); either resolves the same parked clarify.

No confirmation message is sent — the owl's resumed continuation is the visible
response. Fail-safe: a malformed action, a stale/superseded ``clarify_id``, or
an out-of-range index is logged and ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway

__all__ = ["SlackClarifyResolver"]

# Prefix the orchestrator registers on the SlackActionRouter (`router.register`).
_ACTION_PREFIX = "clarify"


class SlackClarifyResolver:
    """Resolves a parked clarify when the user taps a Slack choice button."""

    def __init__(self, clarify_gateway: ClarifyGateway) -> None:
        self._gateway = clarify_gateway

    async def handle_action(self, action_id_or_value: str) -> None:
        """Resolve the parked clarify for a ``clarify:{clarify_id}:{idx}`` tap.

        4-point logging: entry / decision / step / exit.

        Parses the action, maps ``idx`` to the entry's choice text via
        :meth:`ClarifyGateway.peek`, and calls
        :meth:`ClarifyGateway.try_resolve_by_id` (keyed on the tapped
        ``clarify_id``) to wake the parked turn. Idempotent and fail-safe: a
        malformed payload, a stale/superseded ``clarify_id`` (peek → ``None``),
        or an out-of-range index is logged and ignored. Never raises (the router
        catches, but this stays clean).
        """
        log.slack.debug(
            "[slack] clarify.handle_action: entry",
            extra={"_fields": {"action_prefix": action_id_or_value[:24]}},
        )
        # 2. DECISION — parse + validate the action payload.
        parts = action_id_or_value.split(":")
        if len(parts) != 3 or parts[0] != _ACTION_PREFIX:
            log.slack.debug(
                "[slack] clarify.handle_action: not a clarify action — ignored",
            )
            return
        clarify_id, idx_raw = parts[1], parts[2]
        try:
            idx = int(idx_raw)
        except ValueError:
            log.slack.warning(
                "[slack] clarify.handle_action: non-int index — ignored",
                extra={"_fields": {"idx_raw": idx_raw}},
            )
            return

        # 3. STEP — read-only lookup; a missing entry means the question was
        # already answered / superseded / expired (a stale tap). Idempotent.
        entry = self._gateway.peek(clarify_id)
        if entry is None:
            log.slack.info(
                "[slack] clarify.handle_action: stale clarify tap — ignored",
                extra={"_fields": {"clarify_id": clarify_id}},
            )
            return
        if idx < 0 or idx >= len(entry.choices):
            log.slack.warning(
                "[slack] clarify.handle_action: index out of range — ignored",
                extra={
                    "_fields": {
                        "clarify_id": clarify_id,
                        "idx": idx,
                        "n": len(entry.choices),
                    }
                },
            )
            return

        text = entry.choices[idx]
        # Resolve through the gateway BY THE TAPPED id — the disambiguating
        # clarify_id the tap carries, not a session+channel re-match. Sets the
        # event + wakes the parked turn in place (the resumed turn's decoupled
        # send delivers the continuation). No await may be added between peek and
        # try_resolve_by_id.
        self._gateway.try_resolve_by_id(clarify_id, text)
        # 4. EXIT
        log.slack.info(
            "[slack] clarify.handle_action: resolved",
            extra={
                "_fields": {
                    "clarify_id": clarify_id,
                    "idx": idx,
                    "session_id": entry.session_id,
                    "channel": entry.channel,
                }
            },
        )
