"""F002/C-1 — WhatsApp send_text fail-loud on an explicit unresolvable target.

On the on-turn path ``send()`` passes the captured JID EXPLICITLY. When it is
unresolvable (no chunk target AND no ``_last_target``) the answer must fail loud
(``DeliveryError("whatsapp", "no_target")``) — NEVER navigate to an empty chat.
A proactive/best-effort send with no explicit target stays a logged no-op.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.pipeline.streaming import ResponseChunk


def _adapter() -> WhatsAppChannelAdapter:
    settings = WhatsAppSettings(
        allowed_phone_numbers=frozenset(["15551234567"]),
        session_dir="/tmp/test_whatsapp_notarget",
    )
    adapter = WhatsAppChannelAdapter(settings, data_dir="/tmp/test_data")
    adapter._browser.send_message = AsyncMock()  # type: ignore[method-assign]
    return adapter


async def _stray() -> Any:
    # An int target cannot reach WhatsApp (str-JID only) → narrowed to None on the
    # on-turn path with no _last_target → no_target.
    yield ResponseChunk(
        content="hi", is_final=True, chunk_index=0,
        trace_id="t", owl_name="o", target=12345,
    )


@pytest.mark.asyncio
async def test_send_explicit_unresolvable_target_raises() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        assert adapter._last_target is None
        with pytest.raises(DeliveryError) as ei:
            await adapter.send(_stray())
        assert ei.value.channel == "whatsapp"
        assert ei.value.reason == "no_target"
        # Never navigated to an empty chat.
        adapter._browser.send_message.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_text_best_effort_noop_no_target() -> None:
    """No explicit target + no _last_target → logged no-op, never raises."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        assert adapter._last_target is None
        await adapter.send_text("proactive ping")  # must not raise
        adapter._browser.send_message.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()
