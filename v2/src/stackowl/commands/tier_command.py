"""TierCommand — /tier slash command for per-session provider preference.

Stores the preferred provider tier (``fast`` | ``standard`` | ``powerful`` |
``local``) keyed by ``session_id``.  Because :class:`PipelineState` is frozen,
state lives in a module-level dict and the gateway/router queries
:func:`get_session_tier` when picking a provider.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_tier_preferences: dict[str, str] = {}

_VALID_TIERS: frozenset[str] = frozenset({"fast", "standard", "powerful", "local"})


class TierCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "tier"

    @property
    def description(self) -> str:
        return "Set the preferred provider tier for this session."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.engine.debug(
            "[commands] tier.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        tier = args.strip().lower()
        if not tier:
            current = _tier_preferences.get(state.session_id, "default")
            log.engine.debug(
                "[commands] tier.handle: decision — show current",
                extra={"_fields": {"session": state.session_id, "current": current}},
            )
            return f"Current tier preference: {current}\nValid tiers: fast, standard, powerful, local"
        if tier not in _VALID_TIERS:
            log.engine.warning(
                "[commands] tier.handle: rejected unknown tier",
                extra={"_fields": {"session": state.session_id, "tier": tier[:40]}},
            )
            return f"✗ Unknown tier: {tier} — valid tiers: fast, standard, powerful, local"
        _tier_preferences[state.session_id] = tier
        log.engine.info(
            "[commands] tier.handle: exit — preference stored",
            extra={"_fields": {"session": state.session_id, "tier": tier}},
        )
        return f"Tier preference set to {tier} for this session"


def get_session_tier(session_id: str) -> str | None:
    """Return the stored tier for ``session_id`` or ``None`` if unset."""
    return _tier_preferences.get(session_id)


def reset_session_tiers() -> None:
    """Test helper — wipe in-memory tier preferences."""
    _tier_preferences.clear()


_CMD = register_command(TierCommand())
