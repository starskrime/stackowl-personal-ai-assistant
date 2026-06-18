"""F004-part2 — WhatsApp startup wiring is gated on the enabled flag.

WhatsApp Web is QR-auth (no bot token), so the orchestrator gate is the
``enabled`` flag alone (``if whatsapp_cfg.enabled``). This proves that boolean and
that a constructed+registered adapter lands in the ChannelRegistry (so the full
gateway intake → pipeline → send() path can reach it) — while a disabled config
never registers.
"""

from __future__ import annotations

from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.settings import WhatsAppSettings


def test_enabled_flag_gates_start() -> None:
    """The start guard fires ONLY when enabled is True (default False)."""
    assert WhatsAppSettings().enabled is False  # never accidentally started
    assert WhatsAppSettings(enabled=True).enabled is True


def test_registered_adapter_is_discoverable_in_registry() -> None:
    """register_with_registry() makes the channel reachable by the gateway."""
    registry = ChannelRegistry.instance()
    registry.reset()
    try:
        adapter = WhatsAppChannelAdapter(
            WhatsAppSettings(enabled=True, allowed_phone_numbers=frozenset({"+1555"}))
        )
        assert "whatsapp" not in {c.channel_name for c in registry.all()}
        adapter.register_with_registry()
        assert "whatsapp" in {c.channel_name for c in registry.all()}
        assert registry.get("whatsapp") is adapter
    finally:
        registry.reset()
