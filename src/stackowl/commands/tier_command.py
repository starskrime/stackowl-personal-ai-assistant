"""TierCommand — /tier slash command: session preference AND provider-tier
membership admin, merged into one command by design.

Two distinct, cross-functional jobs share this command because they operate
on the same "tier" concept from two different angles, and their token
vocabularies never collide (tier names vs. subcommand names are disjoint):

1. **Session preference** (``/tier <fast|standard|powerful|local>``) — sets
   which tier THIS session/identity prefers to be routed to. Persisted via
   :class:`PreferenceStore` so it survives ``stackowl serve`` restarts. Keyed
   by the resolved identity: when ``state.identity_key`` is set (cross-channel
   identity threading is active) the preference follows the user across
   channels. Falls back to ``state.session_id`` otherwise (byte-identical to
   the original, pre-merge behaviour). Falls back to an in-memory dict when
   the store is unavailable (e.g. unit tests).

2. **Provider-tier membership admin** (``/tier list|menu|add|remove``) — a
   tier-centric view on the SAME ``providers:`` list in ``stackowl.yaml``
   that ``/provider`` already owns. Adding/removing a provider from a tier is
   the same underlying operation as ``/provider set-tier``/``enable``/
   ``disable`` — this command just presents it from the tier's point of view.
   "Remove" disables the provider rather than deleting it (reversible via
   ``/provider enable``), matching this codebase's existing non-destructive
   enable/disable convention. Button-driven on Telegram and TUI for free —
   every method here returns the same channel-agnostic
   ``CommandResponse``/``Action`` shapes ``/provider`` already uses.
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand
from stackowl.commands.provider_shared import NO_STACKOWL_YAML, live_status_badge, providers_list
from stackowl.commands.response import Action, CommandResponse
from stackowl.config.provider import ProviderConfig
from stackowl.config.settings import Settings
from stackowl.events.bus import EventBus
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only; no runtime import cycle
    from stackowl.providers.registry import ProviderRegistry

_PREF_KEY = "provider_tier"
# Single source of truth for valid tiers — derived from the schema, so a
# schema change never drifts between /tier and /provider.
_VALID_TIERS: tuple[str, ...] = typing.get_args(
    typing.get_args(ProviderConfig.model_fields["tiers"].annotation)[0]
)
# Admin subcommand names — disjoint from _VALID_TIERS by construction, which
# is what makes the merge unambiguous (see module docstring).
_ADMIN_SUBCOMMANDS: frozenset[str] = frozenset({"list", "menu", "add", "remove"})

_USAGE = (
    "Usage: /tier [<tier>] — show or set YOUR session's preferred tier\n"
    "   or: /tier <list|menu|add|remove> [args] — manage tier membership\n"
    "  /tier fast                 (set your preference)\n"
    "  /tier                      (show your current preference)\n"
    "  /tier list\n"
    "  /tier menu <tier>\n"
    "  /tier add <tier> [name] [model_name]\n"
    "  /tier remove <tier> [name] [model_name]\n"
    f"  tiers: {', '.join(_VALID_TIERS)}"
)

_TIER_META = CommandMeta(
    grammar="verb",
    group="Providers & Routing",
    subcommands=(
        SubCommand(
            name="list",
            summary="Show every tier and its configured providers",
            description=(
                "You see all four tiers, each with its member providers, "
                "enabled state, and live circuit status."
            ),
            examples=(Example(invocation="/tier list"),),
        ),
        SubCommand(
            name="menu",
            summary="Drill into one tier to add/remove providers",
            args=(Arg(name="tier", summary="tier to manage", choices=_VALID_TIERS),),
            examples=(Example(invocation="/tier menu fast"),),
        ),
        SubCommand(
            name="add",
            summary="Add an already-configured provider to a tier",
            description=(
                "You move an EXISTING configured provider into this tier "
                "(same effect as /provider set-tier). To add a brand-new "
                "provider from the catalog, use /provider add instead. "
                "With an optional 3rd argument (model_name), this targets "
                "one of the provider's models[] entries instead of the "
                "provider's own tiers."
            ),
            args=(
                Arg(name="tier", summary="target tier", choices=_VALID_TIERS),
                Arg(name="name", required=False, summary="configured provider name"),
                Arg(
                    name="model_name",
                    required=False,
                    summary="a specific models[] entry to target, instead of the provider itself",
                ),
            ),
            examples=(
                Example(invocation="/tier add fast groq"),
                Example(
                    invocation="/tier add fast groq groq-mini",
                    note="Add a specific model of groq's to the tier, not groq itself",
                ),
            ),
        ),
        SubCommand(
            name="remove",
            summary="Remove a provider from a tier (disables it)",
            description=(
                "You take a provider out of active routing for this tier by "
                "disabling it — its configuration and tier are kept, so "
                "/provider enable brings it right back. With an optional 3rd "
                "argument (model_name), this targets one of the provider's "
                "models[] entries instead: its tier membership is trimmed, "
                "or — if that was its last tier — the models[] entry itself "
                "is disabled (same non-destructive convention as the "
                "provider-level case, at model granularity)."
            ),
            args=(
                Arg(name="tier", summary="tier to remove from", choices=_VALID_TIERS),
                Arg(name="name", required=False, summary="configured provider name"),
                Arg(
                    name="model_name",
                    required=False,
                    summary="a specific models[] entry to target, instead of the provider itself",
                ),
            ),
            examples=(
                Example(invocation="/tier remove fast groq"),
                Example(
                    invocation="/tier remove fast groq groq-mini",
                    note="Remove a specific model of groq's from the tier, not groq itself",
                ),
            ),
        ),
    ),
    examples=(
        Example(invocation="/tier fast", note="Set YOUR session's preferred tier"),
        Example(invocation="/tier", note="Show your current tier preference"),
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
    """Implements /tier's two jobs: bare-tier-name session preference, and
    list|menu|add|remove provider-tier membership admin (see module docstring)."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._bus = event_bus
        self._registry = registry

    @property
    def command(self) -> str:
        return "tier"

    @property
    def description(self) -> str:
        return (
            "Set your session's preferred tier (follows identity across "
            "channels when configured), or manage which configured "
            "providers belong to each tier."
        )

    @property
    def meta(self) -> CommandMeta:
        return _TIER_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        log.engine.debug(
            "[commands] tier.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        parts = args.strip().split(maxsplit=1)
        first = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        # -- job 1: bare tier name (or empty) — session preference ------------
        if not first or first in _VALID_TIERS:
            result: str | CommandResponse = await self._handle_preference(first, state)
            log.engine.debug("[commands] tier.handle: exit — preference job", extra={"_fields": {}})
            return result

        # -- job 2: admin subcommand — provider-tier membership ---------------
        if first in _ADMIN_SUBCOMMANDS:
            try:
                if first == "list":
                    result = self._list()
                elif first == "menu":
                    result = self._menu(rest)
                elif first == "add":
                    add_tokens = rest.split()
                    result = (
                        self._add_execute(rest) if len(add_tokens) >= 2 else self._add_browse(rest.strip())
                    )
                else:  # "remove"
                    remove_tokens = rest.split()
                    result = (
                        self._remove_execute(rest)
                        if len(remove_tokens) >= 2
                        else self._remove_browse(rest.strip())
                    )
            except Exception as exc:
                log.engine.error(
                    "[commands] tier.handle: admin subcommand failed",
                    exc_info=exc,
                    extra={"_fields": {"sub": first}},
                )
                return f"✗ /tier {first}: {exc}"
            log.engine.debug("[commands] tier.handle: exit — admin job", extra={"_fields": {"sub": first}})
            return result

        # -- neither a valid tier nor a valid admin subcommand -----------------
        log.engine.warning(
            "[commands] tier.handle: rejected unknown tier/subcommand",
            extra={"_fields": {"session": state.session_id, "token": first[:40]}},
        )
        return f"✗ Unknown tier: {first} — valid tiers: {', '.join(_VALID_TIERS)}\n\n{_USAGE}"

    async def _handle_preference(self, tier: str, state: PipelineState) -> str | CommandResponse:
        """Show or set the session's preferred tier. Text wording is
        byte-identical to the original (pre-merge) /tier behaviour for both
        branches; the "show current" branch now also carries buttons (quick
        tier switch + a way into the admin dashboard) so the new list/menu/
        add/remove features are discoverable from the single most obvious
        command instead of being a dead end — the "set" branch is left as a
        plain string, unchanged, since that wasn't the reported gap."""
        owner_key = _owner_key_for_state(state)
        store = get_services().preference_store
        if not tier:
            current = await _read_tier(store, owner_key) or "default"
            log.engine.debug(
                "[commands] tier.handle_preference: decision — show current",
                extra={"_fields": {"session": state.session_id, "current": current}},
            )
            text = f"Current tier preference: {current}\nValid tiers: {', '.join(_VALID_TIERS)}"
            actions = tuple(
                Action(label=f"Use {t}", command=f"/tier {t}", destructive=False) for t in _VALID_TIERS
            ) + (Action(label="Manage tiers", command="/tier list", destructive=False),)
            return CommandResponse(text=text, actions=actions)
        await _write_tier(store, owner_key, tier)
        log.engine.info(
            "[commands] tier.handle_preference: exit — preference stored",
            extra={"_fields": {"session": state.session_id, "tier": tier}},
        )
        return f"Tier preference set to {tier} for this session"

    # -- admin: shared helpers ---------------------------------------------------

    def _emit_reloaded(self, name: str) -> None:
        if self._bus is None:
            return
        try:
            new_settings = Settings()
        except Exception as exc:
            log.engine.error(
                "[commands] tier._emit_reloaded: immediate reload failed — "
                "falling back to background ConfigWatcher poll",
                exc_info=exc,
                extra={"_fields": {"name": name}},
            )
            return
        self._bus.emit("settings_reloaded", new_settings)

    # -- admin: list (dashboard) --------------------------------------------------

    def _list(self) -> str | CommandResponse:
        log.engine.debug("[commands] tier.list: entry")
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        lines: list[str] = ["Tiers:"]
        actions: list[Action] = []
        for tier in _VALID_TIERS:
            # A disabled provider is excluded from the live registry's tier pools
            # (ProviderRegistry only builds _tiers for enabled configs) — display
            # must agree, or a provider whose only tier was just removed (which
            # disables it rather than emptying `tiers`) still shows here and reads
            # as "remove didn't work". Re-enabling stays available via
            # /provider menu, which lists ALL providers regardless of `enabled`.
            members = [
                p for p in providers if tier in (p.get("tiers") or []) and p.get("enabled", True)
            ]
            lines.append(f"\n{tier}:")
            if not members:
                lines.append("  (none)")
            for p in members:
                name = p.get("name", "?")
                lines.append(f"  {name}{live_status_badge(self._registry, name)}")
            actions.append(Action(label=f"Manage {tier}", command=f"/tier menu {tier}", destructive=False))
        log.engine.debug(
            "[commands] tier.list: exit", extra={"_fields": {"provider_count": len(providers)}}
        )
        return CommandResponse(text="\n".join(lines), actions=tuple(actions))

    # -- admin: menu (per-tier drill-down: add/remove) ----------------------------

    def _menu(self, raw: str) -> str | CommandResponse:
        log.engine.debug("[commands] tier.menu: entry", extra={"_fields": {"raw_len": len(raw)}})
        tier = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not tier or tier not in _VALID_TIERS:
            log.engine.debug("[commands] tier.menu: exit — usage", extra={"_fields": {"tier": tier}})
            return f"Usage: /tier menu <tier>\n  tiers: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        # See _list's comment: membership display must agree with the live
        # registry, which excludes disabled providers from every tier pool.
        members = [
            p for p in providers if tier in (p.get("tiers") or []) and p.get("enabled", True)
        ]
        lines = [f"Tier '{tier}':"]
        actions: list[Action] = []
        if not members:
            lines.append("(no providers)")
        for p in members:
            name = p.get("name", "?")
            lines.append(f"  {name}{live_status_badge(self._registry, name)}")
            actions.append(
                Action(label=f"Remove {name}", command=f"/tier remove {tier} {name}", destructive=False)
            )
        actions.append(Action(label="+ Add provider", command=f"/tier add {tier}", destructive=False))
        actions.append(Action(label="Back", command="/tier list", destructive=False))
        log.engine.debug(
            "[commands] tier.menu: exit", extra={"_fields": {"tier": tier, "member_count": len(members)}}
        )
        return CommandResponse(text="\n".join(lines), actions=tuple(actions))

    # -- admin: add (browse + execute) --------------------------------------------

    def _add_browse(self, raw: str) -> str | CommandResponse:
        log.engine.debug("[commands] tier.add_browse: entry", extra={"_fields": {"raw_len": len(raw)}})
        tier = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not tier or tier not in _VALID_TIERS:
            return f"Usage: /tier add <tier> [name]\n  tiers: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        candidates = [p for p in providers if tier not in (p.get("tiers") or [])]
        if not candidates:
            log.engine.debug(
                "[commands] tier.add_browse: exit — no candidates", extra={"_fields": {"tier": tier}}
            )
            return CommandResponse(
                text=f"Every configured provider is already in tier '{tier}' (or none are configured).",
                actions=(Action(label="+ Add new provider", command="/provider add", destructive=False),),
            )
        actions = tuple(
            Action(
                label=f"{p.get('name', '?')} (currently: {', '.join(p.get('tiers') or [])})",
                command=f"/tier add {tier} {p.get('name', '?')}",
                destructive=False,
            )
            for p in candidates
        )
        log.engine.debug(
            "[commands] tier.add_browse: exit",
            extra={"_fields": {"tier": tier, "candidates": len(candidates)}},
        )
        return CommandResponse(text=f"Add which configured provider to tier '{tier}'?", actions=actions)

    def _add_execute(self, raw: str) -> str:
        log.engine.debug("[commands] tier.add_execute: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) < 2:
            return "Usage: /tier add <tier> <name> [model_name]"
        tier, name = bits[0], bits[1]
        if tier not in _VALID_TIERS:
            log.engine.warning(
                "[commands] tier.add_execute: invalid tier", extra={"_fields": {"tier": tier}}
            )
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.engine.warning("[commands] tier.add_execute: not found", extra={"_fields": {"name": name}})
            return f"✗ Provider '{name}' not found"

        if len(bits) >= 3:
            # 3-arg form — same add logic, scoped to one models[] entry
            # instead of the provider dict itself (see module/SubCommand docs).
            model_name = bits[2]
            existing_models = target.get("models") or []
            model_entry = next((m for m in existing_models if m.get("name") == model_name), None)
            if model_entry is None:
                log.engine.warning(
                    "[commands] tier.add_execute: model not found",
                    extra={"_fields": {"name": name, "model_name": model_name}},
                )
                return f"✗ Model '{model_name}' not found under provider '{name}'"
            model_tiers = model_entry.get("tiers") or []
            if tier in model_tiers:
                log.engine.debug(
                    "[commands] tier.add_execute: exit — model already in tier",
                    extra={"_fields": {"name": name, "model_name": model_name, "tier": tier}},
                )
                return f"✓ Model '{model_name}' is already in tier '{tier}'"
            model_entry["tiers"] = [*model_tiers, tier]
            save_yaml(path, data)
            self._emit_reloaded(name)
            log.engine.info(
                "[commands] tier.add_execute: exit — model updated",
                extra={"_fields": {"name": name, "model_name": model_name, "tier": tier}},
            )
            return f"✓ Model '{model_name}' added to tier '{tier}' — applied immediately"

        current_tiers = target.get("tiers") or []
        if tier in current_tiers:
            log.engine.debug(
                "[commands] tier.add_execute: exit — already in tier",
                extra={"_fields": {"name": name, "tier": tier}},
            )
            return f"✓ Provider '{name}' is already in tier '{tier}'"
        target["tiers"] = [*current_tiers, tier]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.engine.info(
            "[commands] tier.add_execute: exit — updated", extra={"_fields": {"name": name, "tier": tier}}
        )
        return f"✓ Provider '{name}' added to tier '{tier}' — applied immediately"

    # -- admin: remove (browse + execute) -----------------------------------------

    def _remove_browse(self, raw: str) -> str | CommandResponse:
        log.engine.debug("[commands] tier.remove_browse: entry", extra={"_fields": {"raw_len": len(raw)}})
        tier = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not tier or tier not in _VALID_TIERS:
            return f"Usage: /tier remove <tier> [name]\n  tiers: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        candidates = [
            p for p in providers
            if tier in (p.get("tiers") or []) and p.get("enabled", True)
        ]
        if not candidates:
            log.engine.debug(
                "[commands] tier.remove_browse: exit — no candidates", extra={"_fields": {"tier": tier}}
            )
            return f"No active providers in tier '{tier}' to remove."
        actions = tuple(
            Action(
                label=p.get("name", "?"),
                command=f"/tier remove {tier} {p.get('name', '?')}",
                destructive=False,
            )
            for p in candidates
        )
        log.engine.debug(
            "[commands] tier.remove_browse: exit",
            extra={"_fields": {"tier": tier, "candidates": len(candidates)}},
        )
        return CommandResponse(text=f"Remove which provider from tier '{tier}'? (disables it)", actions=actions)

    def _remove_execute(self, raw: str) -> str:
        log.engine.debug("[commands] tier.remove_execute: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) < 2:
            return "Usage: /tier remove <tier> <name> [model_name]"
        tier, name = bits[0], bits[1]
        if tier not in _VALID_TIERS:
            log.engine.warning(
                "[commands] tier.remove_execute: invalid tier", extra={"_fields": {"tier": tier}}
            )
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return NO_STACKOWL_YAML
        data = load_yaml(path)
        providers = providers_list(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.engine.warning(
                "[commands] tier.remove_execute: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"

        if len(bits) >= 3:
            # 3-arg form — same subtract-or-disable logic as the provider
            # case below, scoped to one models[] entry instead of the
            # provider dict itself. ModelOverride now carries its own
            # `enabled` flag (mirroring ProviderConfig.enabled) precisely so
            # that removing a model's LAST tier here can DISABLE the models[]
            # entry (tiers left intact) instead of deleting it — the same
            # "always routable-or-explicitly-off" invariant a disabled
            # provider already gets, at model granularity. This is distinct
            # from /provider remove-model (task 25), which is an intentional,
            # explicit full-deletion operation and is untouched by this path.
            model_name = bits[2]
            existing_models = target.get("models") or []
            model_entry = next((m for m in existing_models if m.get("name") == model_name), None)
            if model_entry is None:
                log.engine.warning(
                    "[commands] tier.remove_execute: model not found",
                    extra={"_fields": {"name": name, "model_name": model_name}},
                )
                return f"✗ Model '{model_name}' not found under provider '{name}'"
            model_tiers = model_entry.get("tiers") or []
            if tier not in model_tiers:
                log.engine.debug(
                    "[commands] tier.remove_execute: exit — model not in this tier",
                    extra={
                        "_fields": {
                            "name": name,
                            "model_name": model_name,
                            "tier": tier,
                            "current_tiers": model_tiers,
                        }
                    },
                )
                return f"✗ Model '{model_name}' is not in tier '{tier}' (it's in {model_tiers})"
            was_only_model_tier = len(model_tiers) == 1
            if was_only_model_tier:
                # This was the model's ONLY tier — disable rather than write
                # an empty tiers list, mirroring the provider-level case
                # below: `tiers` stays intact so `enabled=True` (or removing
                # this branch's disable) brings it right back with no loss
                # of configuration.
                model_entry["enabled"] = False
            else:
                model_entry["tiers"] = [t for t in model_tiers if t != tier]
            save_yaml(path, data)
            self._emit_reloaded(name)
            log.engine.info(
                "[commands] tier.remove_execute: exit — model updated",
                extra={
                    "_fields": {
                        "name": name,
                        "model_name": model_name,
                        "tier": tier,
                        "disabled": was_only_model_tier,
                    }
                },
            )
            suffix = " (disabled)" if was_only_model_tier else ""
            return f"✓ Model '{model_name}' removed from tier '{tier}'{suffix} — applied immediately"

        current_tiers = target.get("tiers") or []
        if tier not in current_tiers:
            log.engine.debug(
                "[commands] tier.remove_execute: exit — not in this tier",
                extra={"_fields": {"name": name, "tier": tier, "current_tiers": current_tiers}},
            )
            return f"✗ Provider '{name}' is not in tier '{tier}' (it's in {current_tiers})"
        was_only_tier = len(current_tiers) == 1
        if was_only_tier:
            # This is the provider's ONLY tier — disable rather than write an
            # empty tiers list (the schema requires at least one entry, and
            # this preserves the "always routable-or-explicitly-off" invariant).
            target["enabled"] = False
        else:
            target["tiers"] = [t for t in current_tiers if t != tier]
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.engine.info(
            "[commands] tier.remove_execute: exit — updated",
            extra={"_fields": {"name": name, "tier": tier, "disabled": was_only_tier}},
        )
        suffix = " (disabled)" if was_only_tier else ""
        return f"✓ Provider '{name}' removed from tier '{tier}'{suffix} — applied immediately"


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


# TierCommand is now a DI command (mirrors ProviderCommand — see
# assembly.py's _register_di_commands) so the admin subcommands get the live
# event_bus + ProviderRegistry. A module-level Pattern-A
# `_CMD = register_command(TierCommand())` would permanently fix
# event_bus=None/registry=None on the registered instance, silencing
# _emit_reloaded and disabling every live status badge — same reasoning as
# ProviderCommand's own Pattern-A -> DI migration.
