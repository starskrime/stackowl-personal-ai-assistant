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
