"""CHAN-4 (F013) — WhatsApp send_file via the Playwright attach flow.

The adapter resolves the destination JID exactly as send_text (per-session
target threading), then drives the browser's attach flow. An explicit-but-
unresolvable target fails loud (no silent drop); best-effort with no target is a
logged no-op (never navigate to an empty chat).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError


def _adapter() -> WhatsAppChannelAdapter:
    settings = WhatsAppSettings(
        allowed_phone_numbers=frozenset(["15551234567"]),
        session_dir="/tmp/test_whatsapp_files",
    )
    adapter = WhatsAppChannelAdapter(settings, data_dir="/tmp/test_data")
    adapter._browser.send_file = AsyncMock()  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_send_file_drives_browser_attach(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 data")
        jid = "15551234567@s.whatsapp.net"
        await adapter.send_file(str(f), caption="report", target=jid)
        adapter._browser.send_file.assert_awaited_once()
        args = adapter._browser.send_file.await_args
        assert args.args[0] == jid
        assert str(f) in args.args
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_explicit_unresolvable_raises(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        f = tmp_path / "x.txt"
        f.write_text("x")
        assert adapter._last_target is None
        with pytest.raises(DeliveryError) as ei:
            await adapter.send_file(str(f), target=None)
        assert ei.value.reason == "no_target"
        adapter._browser.send_file.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_attach_error_raises_delivery_error(tmp_path: Path) -> None:
    """F-66: an attach-flow failure to a RESOLVED chat must not be swallowed.

    Previously the browser attach exception was logged and swallowed, so the
    user never got the file yet the deliverer recorded a clean send. It must
    re-raise as DeliveryError so the ProactiveDeliverer maps it to ``failed``.
    """
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        adapter._browser.send_file = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("whatsapp attach boom")
        )
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 data")
        jid = "15551234567@s.whatsapp.net"
        with pytest.raises(DeliveryError) as ei:
            await adapter.send_file(str(f), caption="report", target=jid)
        assert ei.value.channel == "whatsapp"
        assert ei.value.reason == "transport_error"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_best_effort_noop(tmp_path: Path) -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        f = tmp_path / "x.txt"
        f.write_text("x")
        assert adapter._last_target is None
        await adapter.send_file(str(f))  # no target, best-effort → no raise
        adapter._browser.send_file.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()
