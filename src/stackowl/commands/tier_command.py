"""TierCommand — /tier slash command for per-identity provider preference.

Persists the preferred provider tier (``fast`` | ``standard`` | ``powerful`` |
``local``) via :class:`PreferenceStore` so it survives ``stackowl serve``
restarts.  The preference is keyed by the resolved identity: when
``state.identity_key`` is set (cross-channel identity threading is active) the
preference follows the user across channels — a Telegram session and a Slack
session that resolve to the same identity share one tier preference.  When
``identity_key`` is empty (channel not yet configured for cross-channel
identity) the preference falls back to ``state.session_id``, which is
byte-identical to prior behaviour.  Falls back to an in-memory dict when the
store is unavailable (e.g. unit tests).
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

_PREF_KEY = "provider_tier"
_VALID_TIERS: frozenset[str] = frozenset({"fast", "standard", "powerful", "local"})

_TIER_META = CommandMeta(
    grammar="flag",
    group="Providers & Routing",
    args=(
        Arg(
            "tier",
            required=False,
            choices=("fast", "standard", "powerful", "local"),
            summary="preferred provider tier",
        ),
    ),
)

# Fallback in-memory store when PreferenceStore not wired (tests, dry-run, etc.).
# This is intentionally per-process and lost on restart — the persisted
# PreferenceStore is the authoritative source when available.
_fallback_prefs: dict[str, str] = {}


def _owner_key_for_state(state: PipelineState) -> str:
    """Derive the owner_key for preference scoping from a PipelineState.

    Returns ``state.identity_key`` when set — the resolved cross-channel
    identity so preferences follow the user across Telegram, Slack, CLI, etc.
    Falls back to ``state.session_id`` for channels not yet configured for
    cross-channel identity (byte-identical to prior behaviour).
    """
    return state.identity_key or state.session_id


class TierCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "tier"

    @property
    def description(self) -> str:
        return "Set the preferred provider tier (session-scoped; follows identity across channels when configured)."

    @property
    def meta(self) -> CommandMeta:
        return _TIER_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.engine.debug(
            "[commands] tier.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        owner_key = _owner_key_for_state(state)
        tier = args.strip().lower()
        store = get_services().preference_store
        if not tier:
            current = await _read_tier(store, owner_key) or "default"
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
        await _write_tier(store, owner_key, tier)
        log.engine.info(
            "[commands] tier.handle: exit — preference stored",
            extra={"_fields": {"session": state.session_id, "tier": tier}},
        )
        return f"Tier preference set to {tier} for this session"


async def _read_tier(store: object, owner_key: str) -> str | None:
    """Read tier from PreferenceStore, falling back to in-memory dict."""
    if store is not None:
        try:
            # store is duck-typed `object`; .get returns Any. Pre-existing on the
            # branch (identity work) — surfaced now that mypy checks this file.
            return await store.get(owner_key, _PREF_KEY)  # type: ignore[attr-defined,no-any-return]
        except Exception as exc:
            log.engine.warning(
                "[commands] tier._read_tier: store.get failed — falling back to memory",
                exc_info=exc,
            )
    return _fallback_prefs.get(owner_key)


async def _write_tier(store: object, owner_key: str, tier: str) -> None:
    if store is not None:
        try:
            await store.set(owner_key, _PREF_KEY, tier)  # type: ignore[attr-defined]
            # Also mirror to in-memory so synchronous get_session_tier() callers
            # don't have to await; the persistent store remains authoritative.
            _fallback_prefs[owner_key] = tier
            return
        except Exception as exc:
            log.engine.warning(
                "[commands] tier._write_tier: store.set failed — using memory only",
                exc_info=exc,
            )
    _fallback_prefs[owner_key] = tier


def get_session_tier(owner_key: str) -> str | None:
    """Return the cached tier for ``owner_key`` or ``None`` if unset.

    ``owner_key`` must be the *resolved* preference key — i.e.
    ``state.identity_key or state.session_id`` — matching the key used by
    :func:`_write_tier` via :func:`_owner_key_for_state`.  When
    ``identity_key`` is set, the same tier is returned regardless of which
    channel (Telegram, Slack, CLI …) the lookup originates from.  When
    ``identity_key`` is empty, ``session_id`` is used as the key, which is
    byte-identical to the prior behaviour.

    Reads from the in-memory fallback dict only — synchronous callers like the
    router can't await the PreferenceStore. The fallback is populated whenever
    the persisted store is read or written, so for any session that has set a
    tier this turn or since boot, this returns the current value.

    For cross-restart preference recovery, callers should also call
    :func:`hydrate_from_store` at startup once per known identity/session key.
    """
    return _fallback_prefs.get(owner_key)


async def hydrate_from_store(store: object, owner_key: str) -> None:
    """Pre-populate the in-memory cache from the persistent store at startup."""
    if store is None:
        return
    try:
        value = await store.get(owner_key, _PREF_KEY)  # type: ignore[attr-defined]
    except Exception as exc:
        log.engine.warning(
            "[commands] tier.hydrate: store.get failed",
            exc_info=exc,
        )
        return
    if value is not None:
        _fallback_prefs[owner_key] = value


def reset_session_tiers() -> None:
    """Test helper — wipe the in-memory fallback cache."""
    _fallback_prefs.clear()


_CMD = register_command(TierCommand())
