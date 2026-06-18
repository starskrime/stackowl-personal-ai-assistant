"""F001/F002 merge-gate — WhatsApp reply target threading (no cross-deliver).

Two CONCURRENT inbound messages arrive from DIFFERENT JIDs (one user, one
group). Each turn's reply (driven through the real ``send()`` path with the
chunk carrying the originating JID as ``target`` — what ``deliver.py`` stamps)
MUST reach ITS OWN JID via ``browser.send_message`` — never the other's, and
never the empty JID the adapter used to hardcode.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.streaming import ResponseChunk

_USER_JID = "15551234567@s.whatsapp.net"
_GROUP_JID = "120363041234567890@g.us"


def _adapter() -> WhatsAppChannelAdapter:
    settings = WhatsAppSettings(
        allowed_phone_numbers=frozenset(["15551234567", "120363041234567890"]),
        session_dir="/tmp/test_whatsapp_routing",
    )
    adapter = WhatsAppChannelAdapter(settings, data_dir="/tmp/test_data")
    # Replace the live browser with a recording mock.
    adapter._browser.send_message = AsyncMock()  # type: ignore[method-assign]
    return adapter


async def _chunks(*chunks: ResponseChunk) -> Any:
    for c in chunks:
        yield c


def _chunk(content: str, target: int | str | None) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=True, chunk_index=0,
        trace_id="t", owl_name="owl", target=target,
    )


@pytest.mark.asyncio
async def test_whatsapp_stamps_jid_and_resolves_target() -> None:
    adapter = _adapter()
    await adapter.handle_message(_USER_JID, "hi")
    ingress = await adapter._queue.get()
    assert ingress.chat_id == _USER_JID  # raw JID stamped as the target
    assert ingress.session_id.startswith("whatsapp:")
    assert "15551234567" not in ingress.session_id  # session stays hashed
    assert adapter.resolve_target(ingress.session_id) == _USER_JID
    assert adapter.resolve_target("whatsapp:unknown") is None


@pytest.mark.asyncio
async def test_concurrent_inbound_no_cross_deliver_and_never_empty_jid() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        await adapter.handle_message(_USER_JID, "from user")
        await adapter.handle_message(_GROUP_JID, "from group")

        await adapter.send(_chunks(_chunk("reply to user", target=_USER_JID)))
        await adapter.send(_chunks(_chunk("reply to group", target=_GROUP_JID)))

        calls = adapter._browser.send_message.await_args_list
        by_jid: dict[str, str] = {}
        for c in calls:
            jid = c.args[0]
            body = c.args[1]
            assert jid != ""  # never the hardcoded empty JID
            by_jid.setdefault(jid, "")
            by_jid[jid] += body

        assert "reply to user" in by_jid[_USER_JID]
        assert "reply to group" in by_jid[_GROUP_JID]
        # No cross-deliver across JIDs.
        assert "reply to group" not in by_jid[_USER_JID]
        assert "reply to user" not in by_jid[_GROUP_JID]
    finally:
        TestModeGuard.deactivate()
