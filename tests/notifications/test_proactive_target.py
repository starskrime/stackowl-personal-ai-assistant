"""Task 6 — proactive/heartbeat delivery targets the explicit chat_id.

A proactive ``send_text`` with no chat_id targets the shared mutable
``_last_chat_id`` and, under concurrency, can deliver to the WRONG chat. This
test pins the fix: when ``ProactiveDeliverer._transport`` is given an explicit
``chat_id``, the message is sent to THAT chat (via ``send_text(text,
chat_id=...)``), not the global ``_last_chat_id``. Back-compat callers pass
``None`` and the adapter is called WITHOUT a ``chat_id`` kwarg so non-telegram
adapters (whose ``send_text`` takes only ``text``) keep working.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_proactive_uses_explicit_chat_id() -> None:
    sent: list[tuple[int | None, str]] = []

    class _Adapter:
        async def send_text(self, text: str, *, chat_id: int | None = None) -> None:
            sent.append((chat_id, text))

    class _Reg:
        def get(self, channel: str) -> _Adapter:
            return _Adapter()

    from stackowl.notifications.deliverer import ProactiveDeliverer

    d = ProactiveDeliverer.__new__(ProactiveDeliverer)
    d._registry = _Reg()  # type: ignore[attr-defined]
    result = await d._transport("telegram", "ping", chat_id=777)

    assert result == "delivered"
    assert sent == [(777, "ping")]


@pytest.mark.asyncio
async def test_proactive_no_chat_id_omits_kwarg_for_backcompat() -> None:
    """A None chat_id must NOT pass a ``chat_id`` kwarg — non-telegram adapters'
    ``send_text(self, text)`` would raise ``TypeError`` on an unexpected kwarg."""
    sent: list[str] = []

    class _BackCompatAdapter:
        # Mirrors cli/slack/discord/whatsapp: text-only, no chat_id parameter.
        async def send_text(self, text: str) -> None:
            sent.append(text)

    class _Reg:
        def get(self, channel: str) -> _BackCompatAdapter:
            return _BackCompatAdapter()

    from stackowl.notifications.deliverer import ProactiveDeliverer

    d = ProactiveDeliverer.__new__(ProactiveDeliverer)
    d._registry = _Reg()  # type: ignore[attr-defined]
    result = await d._transport("cli", "ping", chat_id=None)

    assert result == "delivered"
    assert sent == ["ping"]


def test_resolve_target_chat_id_telegram_numeric_session() -> None:
    """A numeric session_id on telegram (private chat: session_id == chat_id)
    resolves to that chat_id — the genuine recipient."""
    from stackowl.notifications.router_helpers import resolve_target_chat_id

    assert resolve_target_chat_id("telegram", "12345") == 12345
    assert resolve_target_chat_id("telegram", "  678  ") == 678


def test_resolve_target_chat_id_unresolvable_returns_none() -> None:
    """Ambiguous / non-resolvable targets fall back to None (the deliverer then
    uses _last_chat_id, and the ambiguity is logged loudly — no silent guess)."""
    from stackowl.notifications.router_helpers import resolve_target_chat_id

    # Non-telegram channels have no session==chat_id invariant.
    assert resolve_target_chat_id("cli", "12345") is None
    assert resolve_target_chat_id("slack", "U123") is None
    # Missing / blank session id.
    assert resolve_target_chat_id("telegram", None) is None
    assert resolve_target_chat_id("telegram", "") is None
    # Non-numeric session id (e.g. a group chat whose session id != chat id).
    assert resolve_target_chat_id("telegram", "group:abc") is None
    # Missing channel.
    assert resolve_target_chat_id(None, "12345") is None


@pytest.mark.asyncio
async def test_deliver_threads_notification_target_chat_id() -> None:
    """End-to-end: a Notification carrying ``target_chat_id`` makes ``deliver``
    send to THAT chat, not the adapter's shared mutable ``_last_chat_id``.

    This pins the cross-delivery fix: the proactive sources stamp the recipient
    onto the notification and the deliverer threads it through to ``send_text``.
    The adapter here simulates ``_last_chat_id`` being a DIFFERENT chat (someone
    else messaged last); the proactive send must still reach its own recipient.
    """
    from types import SimpleNamespace
    from typing import cast

    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.router import Notification

    sent: list[tuple[int | None, str]] = []
    last_chat_id = 999  # whoever messaged last — the WRONG target

    class _Adapter:
        async def send_text(self, text: str, *, chat_id: int | None = None) -> None:
            # Mirror telegram: fall back to _last_chat_id when no explicit target.
            sent.append((chat_id if chat_id is not None else last_chat_id, text))

    class _Reg:
        def get(self, channel: str) -> _Adapter:
            return _Adapter()

    class _Router:
        async def deliver(self, notification: Notification) -> str:
            return "delivered"

    settings = cast(
        "object", SimpleNamespace(notifications=SimpleNamespace(default_channel="telegram"))
    )
    d = ProactiveDeliverer.__new__(ProactiveDeliverer)
    d._registry = _Reg()  # type: ignore[attr-defined]
    d._router = _Router()  # type: ignore[attr-defined]
    d._settings = settings  # type: ignore[attr-defined]

    notification = Notification(
        message="proactive ping",
        urgency="normal",
        category="heartbeat",
        channel_name="telegram",
        target_chat_id=777,
    )
    result = await d.deliver(notification)

    assert result == "delivered"
    # Reached the recipient (777), NOT the shared _last_chat_id (999).
    assert sent == [(777, "proactive ping")]
