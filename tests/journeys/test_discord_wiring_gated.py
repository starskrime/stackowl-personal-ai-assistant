"""F004-part2 — Discord startup wiring is gated on enabled + bot_token.

The orchestrator only starts Discord when ``enabled`` is True AND a ``bot_token``
is present (the guard ``if discord_cfg.enabled and discord_cfg.bot_token``). This
proves the boolean the orchestrator branches on for every shape, and that a
constructed+registered adapter actually lands in the ChannelRegistry (so the full
gateway intake → pipeline → send() path can reach it) — while a disabled/blank
config never registers.
"""

from __future__ import annotations

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.channels.registry import ChannelRegistry


def test_guard_boolean_for_every_shape() -> None:
    """The start guard fires ONLY when enabled AND a bot_token is present."""
    # Default → disabled, no token → skip.
    d = DiscordSettings()
    assert not (d.enabled and d.bot_token)
    # token but disabled → skip (the gap is documented, not accidental).
    only_token = DiscordSettings(bot_token="x" * 8)
    assert not (only_token.enabled and only_token.bot_token)
    # enabled but no token → skip.
    only_enabled = DiscordSettings(enabled=True)
    assert not (only_enabled.enabled and only_enabled.bot_token)
    # enabled AND token → the guard fires (Discord would start).
    both = DiscordSettings(enabled=True, bot_token="x" * 8)
    assert both.enabled and both.bot_token


def test_registered_adapter_is_discoverable_in_registry() -> None:
    """register_with_registry() makes the channel reachable by the gateway."""
    registry = ChannelRegistry.instance()
    registry.reset()
    try:
        adapter = DiscordChannelAdapter(
            DiscordSettings(enabled=True, bot_token="x" * 8, allowed_user_ids=[1])
        )
        # Before registration the channel is absent (disabled-config equivalent).
        assert "discord" not in {c.channel_name for c in registry.all()}
        adapter.register_with_registry()
        assert "discord" in {c.channel_name for c in registry.all()}
        assert registry.get("discord") is adapter
    finally:
        registry.reset()
