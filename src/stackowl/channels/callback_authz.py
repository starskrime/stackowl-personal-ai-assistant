"""One allow-list decision for a BUTTON PRESS, shared by every channel.

WHY THIS EXISTS, MEASURED 2026-09-04. Every channel gates inbound MESSAGES
against its allow-list and none of them gated TAPS. Telegram checks
``is_authorized`` in ``_handle_update``, ``_handle_document`` and the voice
handler — three paths — and registers ``CallbackQueryHandler(router.route)``
with no filter at all. Discord's button seam receives the whole ``interaction``
and never reads ``interaction.user``. Slack's action seam receives ``body`` and
never reads ``body["user"]["id"]``. In all three the presser's identity was
right there and reached no decision.

WHAT THAT ALLOWED. The routers dispatch ``consent:`` and ``clarify:`` prefixes,
so the tap is not cosmetic — it resolves a pending approval. The Telegram adapter
documents itself as "DM + group support, allowlist-gated", and a group is exactly
where someone who is not on the allow-list can see the bot's prompt and press its
button. The allow-list held the front door and the side door was unlatched.

WHY IT LIVES HERE RATHER THAN IN EACH ROUTER. The presser's identity exists only
at the platform seam, and there are exactly three of those; the routers never
receive it. Putting the decision in one function keeps the refusal log identical
across channels and means a fourth channel gets the rule by calling one thing —
rather than growing a fourth slightly-different copy, which is the shape the
channel layer already demonstrates: ``is_authorized`` itself exists in four
variants of 3, 3, 8 and 23 lines.

FAILS CLOSED, ALWAYS. An unknown presser, a missing allow-list or a predicate
that raises all deny. A button that silently does nothing for a stranger is a
much smaller problem than one that approves a tool call for them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stackowl.infra.observability import log

__all__ = ["press_is_authorized"]


def press_is_authorized(
    channel: str,
    presser_id: Any,
    is_authorized: Callable[[Any], bool] | None,
) -> bool:
    """Whether this button press may be acted on. Never raises.

    Args:
        channel: Logger name — ``"telegram"``, ``"discord"``, ``"slack"``.
        presser_id: The platform id of whoever pressed. ``None`` denies.
        is_authorized: The channel's own allow-list predicate. ``None`` denies.

    Returns:
        True only when the presser is positively on the allow-list.
    """
    # Annotated Any on purpose: `log` is a container of named loggers and a
    # getattr on it widens to a union mypy cannot narrow. The fallback is a
    # REAL logger, so an unknown channel name still gets its refusal recorded
    # rather than losing the only evidence that a tap was turned away.
    channel_log: Any = getattr(log, channel, None) or log.gateway
    if presser_id is None or is_authorized is None:
        # INFO, not debug: production runs at INFO, and this line is the only
        # evidence that a tap arrived and was turned away. At DEBUG a refused
        # press would be indistinguishable from a press that never happened.
        channel_log.info(
            f"[{channel}] callback_authz: press REFUSED — no presser id or no "
            f"allow-list to check it against",
            extra={"_fields": {"has_presser": presser_id is not None,
                               "has_predicate": is_authorized is not None}},
        )
        return False
    try:
        allowed = bool(is_authorized(presser_id))
    except Exception as exc:  # no-hidden-errors: a raising check DENIES
        channel_log.error(
            f"[{channel}] callback_authz: allow-list check FAILED — refusing the press",
            exc_info=exc, extra={"_fields": {}},
        )
        return False
    if not allowed:
        channel_log.info(
            f"[{channel}] callback_authz: press REFUSED — presser is not on the allow-list",
            extra={"_fields": {}},
        )
    return allowed
