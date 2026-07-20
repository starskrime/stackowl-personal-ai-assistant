"""ProviderCommand — /provider slash command for AI-provider management.

Subcommands: ``list``, ``add``, ``remove``, ``set-tier``. Providers live as a
list under the top-level ``providers:`` key in ``stackowl.yaml`` and are
mutated via :mod:`ruamel.yaml` (comment-preserving) through the shared
``config_helpers`` I/O functions.

SECURITY: a supplied auth token is NEVER written in plaintext and NEVER logged
or echoed. It is persisted via the shared :func:`store_secret` writer (OS
keyring → mode-0600 file fallback); only the resulting SecretResolver *ref*
(``keychain:…`` / ``file:…``) is stored in the YAML ``api_key`` field.

NOTE: changes are applied immediately via an in-process settings_reloaded
emit — see stackowl/startup/provider_reload.py for the consumer.
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, Any

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.response import Action, CommandResponse
from stackowl.config.provider import ProviderConfig
from stackowl.config.secret_writer import store_secret
from stackowl.config.settings import Settings
from stackowl.events.bus import EventBus
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only; no runtime import cycle
    from stackowl.providers.registry import ProviderRegistry

_TOKEN_PREFIX = "token="

# Derive valid protocol/tier values from the schema Literals — single source of
# truth, so a schema change never drifts from this command's validation.
_VALID_PROTOCOLS: tuple[str, ...] = typing.get_args(
    ProviderConfig.model_fields["protocol"].annotation
)
_VALID_TIERS: tuple[str, ...] = typing.get_args(
    ProviderConfig.model_fields["tier"].annotation
)

_USAGE = (
    "Usage: /provider <list|add|remove|set-tier|edit|enable|disable|set-token|rename|status> [args]\n"
    "  /provider list\n"
    "  /provider add <name> <protocol> <default_model> <tier> "
    "[base_url] [token=<RAW_TOKEN>]\n"
    "  /provider remove <name>\n"
    "  /provider set-tier <name> <tier>\n"
    "  /provider edit <name> <protocol|default_model|base_url|cooldown_hours> <value>\n"
    "  /provider enable <name>\n"
    "  /provider disable <name>\n"
    "  /provider set-token <name> <RAW_TOKEN>\n"
    "  /provider rename <old_name> <new_name>\n"
    "  /provider status <tier>\n"
    f"  protocols: {', '.join(_VALID_PROTOCOLS)}\n"
    f"  tiers: {', '.join(_VALID_TIERS)}"
)

_NO_FILE = "No stackowl.yaml found — run stackowl setup --minimal first"

_PROVIDER_META = CommandMeta(
    grammar="verb",
    group="Providers & Routing",
    subcommands=(
        SubCommand(
            name="list",
            summary="Show every configured AI provider",
            description=(
                "You see each provider's protocol, default model, tier, enabled "
                "flag, and the api_key reference (never the secret itself)."
            ),
            examples=(Example(invocation="/provider list"),),
        ),
        SubCommand(
            name="add",
            summary="Register a new AI provider",
            description=(
                "You add a provider entry. A raw token is stored as a secret "
                "reference (keyring or mode-0600 file) — never in plaintext. "
                "The change applies immediately."
            ),
            args=(
                Arg(name="name", summary="unique provider name"),
                Arg(name="protocol", summary="backend protocol", choices=_VALID_PROTOCOLS),
                Arg(name="default_model", summary="model id to use by default"),
                Arg(name="tier", summary="routing tier", choices=_VALID_TIERS),
                Arg(name="base_url", required=False, summary="API base URL"),
                Arg(name="token=<RAW>", required=False, summary="auth token (stored as a secret ref)"),
            ),
            examples=(
                Example(
                    invocation="/provider add openai openai gpt-4o powerful token=sk-...",
                    note="Add an OpenAI provider with a token",
                ),
            ),
        ),
        SubCommand(
            name="remove",
            summary="Delete a configured provider",
            description=(
                "You remove the named provider. Any stored secret is left in "
                "place. The change applies immediately."
            ),
            args=(Arg(name="name", summary="provider to remove"),),
            examples=(Example(invocation="/provider remove openai"),),
        ),
        SubCommand(
            name="set-tier",
            summary="Change a provider's routing tier",
            description=(
                "You re-tier an existing provider so the model router selects it "
                "differently. The change applies immediately."
            ),
            args=(
                Arg(name="name", summary="provider to re-tier"),
                Arg(name="tier", summary="new routing tier", choices=_VALID_TIERS),
            ),
            examples=(Example(invocation="/provider set-tier openai powerful"),),
        ),
        SubCommand(
            name="edit",
            summary="Change a provider's protocol, model, or base URL",
            description=(
                "You update one field on an existing provider (not its tier or "
                "enabled flag — use set-tier / enable / disable for those). The "
                "change applies immediately."
            ),
            args=(
                Arg(name="name", summary="provider to edit"),
                Arg(
                    name="field",
                    summary="field to change",
                    choices=("protocol", "default_model", "base_url", "cooldown_hours"),
                ),
                Arg(name="value", summary="new value"),
            ),
            examples=(Example(invocation="/provider edit openai default_model gpt-4o"),),
        ),
        SubCommand(
            name="enable",
            summary="Re-enable a disabled provider",
            args=(Arg(name="name", summary="provider to enable"),),
            examples=(Example(invocation="/provider enable openai"),),
        ),
        SubCommand(
            name="disable",
            summary="Disable a provider without deleting it",
            args=(Arg(name="name", summary="provider to disable"),),
            examples=(Example(invocation="/provider disable openai"),),
        ),
        SubCommand(
            name="set-token",
            summary="Rotate a provider's auth token",
            description=(
                "You replace the stored secret reference for an existing provider. "
                "The raw token is never written in plaintext or logged."
            ),
            args=(
                Arg(name="name", summary="provider to update"),
                Arg(name="token", summary="new raw auth token"),
            ),
            examples=(Example(invocation="/provider set-token openai sk-..."),),
        ),
        SubCommand(
            name="rename",
            summary="Rename a provider",
            args=(
                Arg(name="old_name", summary="current provider name"),
                Arg(name="new_name", summary="new provider name"),
            ),
            examples=(Example(invocation="/provider rename openai openai-primary"),),
        ),
        SubCommand(
            name="status",
            summary="Show live circuit-breaker state for every provider in a tier",
            description=(
                "You see, per provider in the given tier, whether its circuit "
                "breaker is closed, half-open, or open (with retry countdown). "
                "Requires the live provider registry to be wired."
            ),
            args=(Arg(name="tier", summary="routing tier", choices=_VALID_TIERS),),
            examples=(Example(invocation="/provider status fast"),),
        ),
    ),
)


class ProviderCommand(SlashCommand):
    """Implements /provider list|add|remove|set-tier."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._bus = event_bus
        self._registry = registry

    @property
    def command(self) -> str:
        return "provider"

    @property
    def description(self) -> str:
        return "List, add, remove, or re-tier AI providers."

    @property
    def meta(self) -> CommandMeta:
        return _PROVIDER_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        log.config.debug(
            "[commands] provider.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "list":
                result = self._list()
            elif sub == "add":
                add_tokens = rest.split()
                result = self._add(rest) if len(add_tokens) >= 4 else self._add_browse(rest.strip())
            elif sub == "add-pick":
                result = await self._add_pick(rest)
            elif sub == "add-token":
                result = await self._add_token(rest)
            elif sub == "add-model":
                result = self._add_model(rest)
            elif sub == "add-tier":
                result = self._add_tier(rest)
            elif sub == "remove":
                result = self._remove(rest)
            elif sub == "set-tier":
                result = self._set_tier(rest)
            elif sub == "enable":
                result = self._set_enabled(rest, True)
            elif sub == "disable":
                result = self._set_enabled(rest, False)
            elif sub == "edit":
                result = self._edit(rest)
            elif sub == "edit-menu":
                result = self._edit_menu(rest)
            elif sub == "edit-field":
                result = await self._edit_field(rest)
            elif sub == "set-token":
                result = self._set_token(rest)
            elif sub == "rename":
                result = self._rename(rest)
            elif sub == "menu":
                result = self._menu(rest)
            elif sub == "status":
                result = self._status(rest)
            else:
                log.config.debug(
                    "[commands] provider.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return render_usage("provider", _PROVIDER_META)
        except Exception as exc:
            log.config.error(
                "[commands] provider.handle: subcommand failed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /provider {sub}: {exc}"
        log.config.debug("[commands] provider.handle: exit", extra={"_fields": {"sub": sub}})
        return result

    # -- helpers ---------------------------------------------------------------

    def _providers(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the (live) providers list, normalising a missing/odd value."""
        raw = data.get("providers")
        if not isinstance(raw, list):
            raw = []
            data["providers"] = raw
        return raw

    def _emit_reloaded(self, name: str) -> None:
        if self._bus is None:
            return
        try:
            new_settings = Settings()
        except Exception as exc:
            log.config.error(
                "[commands] provider._emit_reloaded: immediate reload failed — "
                "falling back to background ConfigWatcher poll",
                exc_info=exc,
                extra={"_fields": {"name": name}},
            )
            return
        self._bus.emit("settings_reloaded", new_settings)

    def _persisted(self, path: Any, name: str) -> bool:
        """Re-read the YAML and confirm provider *name* is present + parses.

        ``load_yaml`` returns ``{}`` on a parse failure, so a corrupt/partial
        write also fails this check (the provider will be absent).
        """
        reloaded = load_yaml(path)
        return any(p.get("name") == name for p in self._providers(reloaded))

    # -- live status (circuit-breaker state) ------------------------------------

    def _live_status_badge(self, name: str) -> str:
        """Return a trailing ` [state]` badge for *name*, or "" when no live
        registry is wired (degrades gracefully — never crashes list/menu)."""
        log.config.debug(
            "[commands] provider.live_status_badge: entry", extra={"_fields": {"name": name}}
        )
        if self._registry is None:
            return ""
        breaker = self._registry.get_circuit_breaker(name)
        if breaker is None:
            log.config.debug(
                "[commands] provider.live_status_badge: exit — no breaker",
                extra={"_fields": {"name": name}},
            )
            return " [no breaker]"
        from stackowl.providers.circuit_breaker import CircuitState

        state = breaker.state
        if state is CircuitState.CLOSED:
            badge = " [closed]"
        elif state is CircuitState.HALF_OPEN:
            badge = " [half-open]"
        else:
            badge = f" [open, retry in {breaker.retry_after_seconds:.0f}s]"
        log.config.debug(
            "[commands] provider.live_status_badge: exit",
            extra={"_fields": {"name": name, "state": state.value}},
        )
        return badge

    def _status(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.status: entry", extra={"_fields": {"raw_len": len(raw)}})
        if self._registry is None:
            log.config.debug("[commands] provider.status: exit — no registry wired")
            return "✗ Provider registry not wired for this command instance."
        tier = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not tier or tier not in _VALID_TIERS:
            log.config.debug(
                "[commands] provider.status: exit — usage", extra={"_fields": {"tier": tier}}
            )
            return f"Usage: /provider status <tier>\n  tiers: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        names = [
            str(p.get("name"))
            for p in self._providers(data)
            if p.get("tier") == tier and p.get("name")
        ]
        if not names:
            log.config.debug(
                "[commands] provider.status: exit — no providers for tier",
                extra={"_fields": {"tier": tier}},
            )
            return f"No providers configured for tier '{tier}'."
        lines = [f"{name}{self._live_status_badge(name)}" for name in names]
        log.config.debug(
            "[commands] provider.status: exit", extra={"_fields": {"tier": tier, "count": len(names)}}
        )
        return f"Tier '{tier}':\n" + "\n".join(lines)

    # -- list ------------------------------------------------------------------

    def _list(self) -> str | CommandResponse:
        log.config.debug("[commands] provider.list: entry")
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        if not providers:
            return CommandResponse(
                text="No providers configured.",
                actions=(Action(label="+ Add provider", command="/provider add", destructive=False),),
            )
        lines: list[str] = []
        actions: list[Action] = [
            Action(label="+ Add provider", command="/provider add", destructive=False)
        ]
        for p in providers:
            name = p.get("name", "?")
            protocol = p.get("protocol", "?")
            model = p.get("default_model", "?")
            tier = p.get("tier", "?")
            enabled = p.get("enabled", True)
            # Show ONLY the ref string — never resolve/print the actual secret.
            key_ref = p.get("api_key")
            key_disp = key_ref if key_ref else "(none)"
            lines.append(
                f"{name} | {protocol} | {model} | {tier} | "
                f"enabled={enabled} | api_key={key_disp}{self._live_status_badge(name)}"
            )
            actions.append(
                Action(label=name, command=f"/provider menu {name}", destructive=False)
            )
        log.config.debug("[commands] provider.list: exit", extra={"_fields": {"count": len(lines)}})
        return CommandResponse(text="\n".join(lines), actions=tuple(actions))

    # -- menu (per-provider drill-down: set-tier + remove) ----------------------

    def _menu(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.menu: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return "Usage: /provider menu <name>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.menu: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"
        protocol = target.get("protocol", "?")
        model = target.get("default_model", "?")
        tier = target.get("tier", "?")
        enabled = target.get("enabled", True)
        text = f"{name} | {protocol} | {model} | {tier} | enabled={enabled}{self._live_status_badge(name)}"
        toggle_verb = "disable" if enabled else "enable"
        actions = (
            tuple(
                Action(
                    label=f"Set tier: {t}",
                    command=f"/provider set-tier {name} {t}",
                    destructive=False,
                )
                for t in _VALID_TIERS
                if t != tier
            )
            + (
                Action(label="Edit", command=f"/provider edit-menu {name}", destructive=False),
                Action(
                    label=toggle_verb.capitalize(),
                    command=f"/provider {toggle_verb} {name}",
                    destructive=False,
                ),
                Action(label=f"Remove {name}", command=f"/provider remove {name}", destructive=True),
            )
        )
        log.config.debug(
            "[commands] provider.menu: exit", extra={"_fields": {"name": name, "n_actions": len(actions)}}
        )
        return CommandResponse(text=text, actions=actions)

    # -- edit-menu / edit-field (drill-down for Edit button) ---------------------

    _EDIT_FIELDS: typing.ClassVar[tuple[str, ...]] = (
        "protocol",
        "default_model",
        "base_url",
        "cooldown_hours",
    )
    _EDIT_FIELD_LABELS: typing.ClassVar[dict[str, str]] = {
        "protocol": "Edit protocol",
        "default_model": "Edit default_model",
        "base_url": "Edit base_url",
        "cooldown_hours": "Edit cooldown_hours",
        "api_key": "Set token",
        "name": "Rename",
    }

    def _edit_menu(self, raw: str) -> str | CommandResponse:
        log.config.debug("[commands] provider.edit_menu: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return "Usage: /provider edit-menu <name>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        providers = self._providers(load_yaml(path))
        if not any(p.get("name") == name for p in providers):
            return f"✗ Provider '{name}' not found"
        actions = tuple(
            Action(
                label=self._EDIT_FIELD_LABELS[field],
                command=f"/provider edit-field {name} {field}",
                destructive=False,
            )
            for field in (*self._EDIT_FIELDS, "api_key", "name")
        ) + (Action(label="Back", command=f"/provider menu {name}", destructive=False),)
        return CommandResponse(text=f"Edit which field on '{name}'?", actions=actions)

    async def _edit_field(self, raw: str) -> str | CommandResponse:
        log.config.debug(
            "[commands] provider.edit_field: entry", extra={"_fields": {"raw_len": len(raw)}}
        )
        bits = raw.split(maxsplit=1)
        if len(bits) < 2:
            return "Usage: /provider edit-field <name> <field>"
        name, field = bits[0], bits[1]
        path = config_path()
        if not path.exists():
            return _NO_FILE
        providers = self._providers(load_yaml(path))
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            return f"✗ Provider '{name}' not found"
        back = (Action(label="Back", command=f"/provider menu {name}", destructive=False),)
        if field == "api_key":
            key_ref = target.get("api_key")
            text = (
                f"Current token ref: {key_ref if key_ref else '(none)'}\n"
                f"Reply with: /provider set-token {name} <NEW_RAW_TOKEN>"
            )
            return CommandResponse(text=text, actions=back)
        if field == "name":
            text = f"Reply with: /provider rename {name} <new_name>"
            return CommandResponse(text=text, actions=back)
        if field == "default_model":
            models = await self._discover_models_for_edit(name, target)
            if models:
                actions = tuple(
                    Action(
                        label=m,
                        command=f"/provider edit {name} default_model {m}",
                        destructive=False,
                    )
                    for m in models[:30]
                ) + back
                return CommandResponse(
                    text=f"Current default_model: {target.get('default_model', '?')}\n"
                    "Pick a live model:",
                    actions=actions,
                )
        current = target.get(field, "?")
        text = (
            f"Current {field}: {current}\n"
            f"Reply with: /provider edit {name} {field} <new value>"
        )
        return CommandResponse(
            text=text,
            actions=(Action(label="Back", command=f"/provider menu {name}", destructive=False),),
        )

    async def _discover_models_for_edit(self, name: str, target: dict[str, Any]) -> list[str]:
        """Best-effort live model list for the ``default_model`` edit-field
        picker. Resolves the provider's stored ``api_key`` ref first; on ANY
        failure (missing/bad key, unreachable, resolver error) this degrades
        to an empty list so the caller falls back to the plain text-hint
        prompt — it must never raise or crash the edit-field flow."""
        # 1. ENTRY
        log.config.debug(
            "[commands] provider.discover_models_for_edit: entry",
            extra={"_fields": {"name": name}},
        )
        from stackowl.exceptions import ModelDiscoveryError
        from stackowl.providers.model_discovery import list_models

        protocol = target.get("protocol", "openai")
        base_url = target.get("base_url") or None
        api_key_ref = target.get("api_key")
        resolved_key = ""
        try:
            if api_key_ref:
                from stackowl.config.secret_resolver import SecretResolver

                resolved_key = SecretResolver.resolve(api_key_ref)
            # 2. STEP — live call; also doubles as token validation
            models = await list_models(protocol, base_url, resolved_key)
        except ModelDiscoveryError as exc:
            log.config.debug(
                "[commands] provider.discover_models_for_edit: discovery failed — "
                "falling back to text hint",
                extra={"_fields": {"name": name, "protocol": protocol, "reason": exc.reason}},
            )
            return []
        except Exception as exc:
            log.config.warning(
                "[commands] provider.discover_models_for_edit: unexpected failure — "
                "falling back to text hint",
                exc_info=exc,
                extra={"_fields": {"name": name, "protocol": protocol}},
            )
            return []
        # 3. EXIT
        log.config.debug(
            "[commands] provider.discover_models_for_edit: exit",
            extra={"_fields": {"name": name, "model_count": len(models)}},
        )
        return models

    # -- set-token -----------------------------------------------------------------

    def _set_token(self, raw: str) -> str:
        log.config.debug("[commands] provider.set_token: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split(maxsplit=1)
        if len(bits) < 2:
            return "Usage: /provider set-token <name> <RAW_TOKEN>"
        name, token = bits[0], bits[1]
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.set_token: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"
        # token length/value never logged — mirrors _add's secret-storage path.
        _description, api_key_ref = store_secret(f"stackowl-provider-{name}", token)
        target["api_key"] = api_key_ref
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.set_token: exit — updated", extra={"_fields": {"name": name}}
        )
        return f"✓ Provider '{name}' token updated (ref: {api_key_ref}) — applied immediately"

    # -- rename ----------------------------------------------------------------

    def _rename(self, raw: str) -> str:
        log.config.debug("[commands] provider.rename: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) != 2:
            return "Usage: /provider rename <old_name> <new_name>"
        old_name, new_name = bits
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        if any(p.get("name") == new_name for p in providers):
            return f"✗ Provider '{new_name}' already exists — pick another name"
        target = next((p for p in providers if p.get("name") == old_name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.rename: not found", extra={"_fields": {"name": old_name}}
            )
            return f"✗ Provider '{old_name}' not found"
        target["name"] = new_name
        save_yaml(path, data)
        self._emit_reloaded(new_name)
        log.config.info(
            "[commands] provider.rename: exit — renamed",
            extra={"_fields": {"old": old_name, "new": new_name}},
        )
        return f"✓ Provider '{old_name}' renamed to '{new_name}' — applied immediately"

    # -- enable/disable ----------------------------------------------------------

    def _set_enabled(self, raw: str, enabled: bool) -> str:
        verb = "enable" if enabled else "disable"
        log.config.debug(
            f"[commands] provider.{verb}: entry", extra={"_fields": {"raw_len": len(raw)}}
        )
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return f"Usage: /provider {verb} <name>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                f"[commands] provider.{verb}: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"
        target["enabled"] = enabled
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            f"[commands] provider.{verb}: exit — updated",
            extra={"_fields": {"name": name, "enabled": enabled}},
        )
        return f"✓ Provider '{name}' {verb}d — applied immediately"

    # -- edit (protocol/default_model/base_url) ----------------------------------

    def _edit(self, raw: str) -> str:
        log.config.debug("[commands] provider.edit: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split(maxsplit=2)
        if len(bits) < 3:
            return "Usage: /provider edit <name> <protocol|default_model|base_url|cooldown_hours> <value>"
        name, field, value = bits
        if field not in ("protocol", "default_model", "base_url", "cooldown_hours"):
            return (
                f"✗ Unknown field '{field}' — use protocol, default_model, base_url, "
                "or cooldown_hours (tier: /provider set-tier, enabled: /provider enable|disable)"
            )
        if field == "protocol" and value not in _VALID_PROTOCOLS:
            return f"✗ Invalid protocol '{value}' — valid: {', '.join(_VALID_PROTOCOLS)}"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.edit: not found", extra={"_fields": {"name": name}}
            )
            return f"✗ Provider '{name}' not found"
        target[field] = value
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.edit: exit — updated",
            extra={"_fields": {"name": name, "field": field}},
        )
        return f"✓ Provider '{name}' {field} set to '{value}' — applied immediately"

    # -- add-browse / add-pick (guided catalog flow) ----------------------------

    def _add_browse(self, query: str) -> CommandResponse:
        """Catalog search (query given) or full browse (empty query).

        Reached from ``handle()`` when ``/provider add`` is called with fewer
        than 4 whitespace-separated tokens — i.e. not the full positional
        ``<name> <protocol> <default_model> <tier>`` form, which still goes
        straight to :meth:`_add` unchanged.
        """
        # 1. ENTRY
        log.config.debug(
            "[commands] provider.add_browse: entry",
            extra={"_fields": {"query_len": len(query)}},
        )
        from stackowl.setup.provider_catalog import ProviderCatalog

        # 2. DECISION — search vs full browse
        entries = ProviderCatalog.search(query) if query else ProviderCatalog.browse()
        if not entries:
            log.config.debug(
                "[commands] provider.add_browse: exit — no matches",
                extra={"_fields": {"query_len": len(query)}},
            )
            return CommandResponse(
                text=f"No catalog providers match '{query}'." if query else "Catalog is empty."
            )

        # 3. STEP — build one action per catalog entry (capped at 30)
        shown = entries[:30]
        actions = tuple(
            Action(label=entry.label, command=f"/provider add-pick {entry.name}", destructive=False)
            for entry in shown
        )
        text = (
            f"Found {len(entries)} provider(s) matching '{query}':"
            if query
            else f"Browse {len(entries)} catalog providers:"
        )
        if len(entries) > 30:
            text += "\n(showing first 30 — refine with /provider add <search term>)"

        # 4. EXIT
        log.config.debug(
            "[commands] provider.add_browse: exit",
            extra={"_fields": {"matches": len(entries), "shown": len(shown)}},
        )
        return CommandResponse(text=text, actions=actions)

    def _catalog_entry(self, catalog_name: str) -> Any:
        """Look up one catalog entry by name, or ``None`` if unknown."""
        from stackowl.setup.provider_catalog import ProviderCatalog

        return next((e for e in ProviderCatalog.load() if e.name == catalog_name), None)

    async def _add_pick(self, raw: str) -> str | CommandResponse:
        """Picked a catalog entry: prompt for a token, or skip straight to
        live discovery when the entry is keyless/local."""
        # 1. ENTRY
        log.config.debug(
            "[commands] provider.add_pick: entry", extra={"_fields": {"raw_len": len(raw)}}
        )
        catalog_name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        entry = self._catalog_entry(catalog_name)
        # 2. DECISION — unknown entry, keyless/local, or needs a token
        if entry is None:
            log.config.debug(
                "[commands] provider.add_pick: exit — unknown catalog entry",
                extra={"_fields": {"catalog": catalog_name}},
            )
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"
        if not entry.needs_api_key or entry.is_local:
            log.config.debug(
                "[commands] provider.add_pick: keyless/local — going straight to discovery",
                extra={"_fields": {"catalog": catalog_name}},
            )
            return await self._add_discover(catalog_name, api_key="")
        key_hint = f"Get a key at: {entry.key_url}\n" if entry.key_url else ""
        # 4. EXIT
        log.config.debug(
            "[commands] provider.add_pick: exit — awaiting token",
            extra={"_fields": {"catalog": catalog_name}},
        )
        return f"{key_hint}Reply with: /provider add-token {catalog_name} <RAW_TOKEN>"

    async def _add_token(self, raw: str) -> str | CommandResponse:
        """Received a raw token for a catalog entry: hand off to live discovery,
        which both validates the token and lists real models in one call."""
        # 1. ENTRY — token value/length never logged, only whether one was given
        log.config.debug(
            "[commands] provider.add_token: entry",
            extra={"_fields": {"raw_len": len(raw), "has_token": bool(raw.strip())}},
        )
        bits = raw.split(maxsplit=1)
        if len(bits) < 2:
            return "Usage: /provider add-token <catalog_name> <RAW_TOKEN>"
        catalog_name, token = bits
        return await self._add_discover(catalog_name, api_key=token)

    async def _add_discover(self, catalog_name: str, *, api_key: str) -> str | CommandResponse:
        """Live-query real models for *catalog_name* — this single call ALSO
        validates the token (a bad key makes the call fail), so this is the
        one place both add-pick (keyless) and add-token (keyed) funnel into.

        SECURITY: *api_key* (the raw token) is never logged, never placed in
        the returned text, and never placed in a button command. On success
        it is persisted via ``store_secret`` immediately and only the
        resulting ref is threaded into the model-pick button commands.
        """
        # 1. ENTRY — token value/length never logged, only whether one was given
        log.config.debug(
            "[commands] provider.add_discover: entry",
            extra={"_fields": {"catalog": catalog_name, "has_token": bool(api_key)}},
        )
        from stackowl.exceptions import ModelDiscoveryError
        from stackowl.providers.model_discovery import list_models

        entry = self._catalog_entry(catalog_name)
        if entry is None:
            log.config.debug(
                "[commands] provider.add_discover: exit — unknown catalog entry",
                extra={"_fields": {"catalog": catalog_name}},
            )
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"

        # 2. STEP — the live call: lists models AND validates the token/base_url
        try:
            models = await list_models(entry.protocol, entry.base_url or None, api_key)
        except ModelDiscoveryError as exc:
            log.config.warning(
                "[commands] provider.add_discover: validation failed",
                extra={"_fields": {"catalog": catalog_name, "reason": exc.reason}},
            )
            # Retry hint preserves catalog_name context so the user doesn't
            # have to re-browse — it never carries the (already-rejected) token.
            retry = f"\nReply with: /provider add-token {catalog_name} <NEW_TOKEN>" if api_key else ""
            return f"✗ Could not connect to {entry.label}: {exc.reason}{retry}"

        # 3. STEP — only after success: persist the secret, keep only the ref
        api_key_ref = "-"
        if api_key:
            _description, api_key_ref = store_secret(f"stackowl-provider-{catalog_name}", api_key)

        if not models:
            log.config.debug(
                "[commands] provider.add_discover: exit — connected, no models reported",
                extra={"_fields": {"catalog": catalog_name}},
            )
            return (
                f"✓ Connected to {entry.label}, but it reported no models.\n"
                f"Reply with: /provider add {catalog_name} {entry.protocol} <model_id> <tier>"
            )
        actions = tuple(
            Action(
                label=model,
                command=f"/provider add-model {catalog_name} {model} {api_key_ref}",
                destructive=False,
            )
            for model in models[:30]
        )
        # 4. EXIT
        log.config.debug(
            "[commands] provider.add_discover: exit — models found",
            extra={"_fields": {"catalog": catalog_name, "model_count": len(models)}},
        )
        return CommandResponse(text=f"{entry.label} — pick a model:", actions=actions)

    # -- add-model / add-tier (final two steps of the guided catalog flow) ------

    def _add_model(self, raw: str) -> CommandResponse:
        """Model was picked (via ``add-model`` from :meth:`_add_discover`'s
        action, or a manual reply): show one tier-pick button per valid tier."""
        # 1. ENTRY
        log.config.debug(
            "[commands] provider.add_model: entry", extra={"_fields": {"raw_len": len(raw)}}
        )
        bits = raw.split(maxsplit=2)
        if len(bits) < 3:
            log.config.debug(
                "[commands] provider.add_model: exit — usage",
                extra={"_fields": {"raw_len": len(raw)}},
            )
            return CommandResponse(
                text="Usage: /provider add-model <catalog_name> <model> <api_key_ref_or_dash>"
            )
        catalog_name, model, api_key_ref = bits
        # 3. STEP — one action per valid tier
        actions = tuple(
            Action(
                label=tier,
                command=f"/provider add-tier {catalog_name} {model} {api_key_ref} {tier}",
                destructive=False,
            )
            for tier in _VALID_TIERS
        )
        # 4. EXIT
        log.config.debug(
            "[commands] provider.add_model: exit",
            extra={"_fields": {"catalog": catalog_name, "model": model}},
        )
        return CommandResponse(text=f"Pick a tier for {catalog_name} / {model}:", actions=actions)

    def _add_tier(self, raw: str) -> str:
        """Tier was picked: this is the LAST step of the guided flow — build
        the provider entry and hand off to the shared persist helper."""
        # 1. ENTRY
        log.config.debug(
            "[commands] provider.add_tier: entry", extra={"_fields": {"raw_len": len(raw)}}
        )
        bits = raw.split()
        if len(bits) != 4:
            log.config.debug(
                "[commands] provider.add_tier: exit — usage",
                extra={"_fields": {"raw_len": len(raw)}},
            )
            return "Usage: /provider add-tier <catalog_name> <model> <api_key_ref_or_dash> <tier>"
        catalog_name, model, api_key_ref, tier = bits
        # 2. DECISION — validate tier and resolve the catalog entry
        if tier not in _VALID_TIERS:
            log.config.warning(
                "[commands] provider.add_tier: invalid tier",
                extra={"_fields": {"tier": tier}},
            )
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        entry = self._catalog_entry(catalog_name)
        if entry is None:
            log.config.debug(
                "[commands] provider.add_tier: exit — unknown catalog entry",
                extra={"_fields": {"catalog": catalog_name}},
            )
            return f"✗ Unknown catalog provider '{catalog_name}' — run /provider add to browse"

        # 3. STEP — auto-suffix the name (groq, groq-2, ...) instead of
        # rejecting: adding the SAME catalog provider twice (e.g. two
        # free-tier keys for round-robin) is the actual point of this flow.
        path = config_path()
        data = load_yaml(path)
        existing_names = [p.get("name") for p in self._providers(data)]
        name = self._unique_provider_name(catalog_name, existing_names)

        provider_entry: dict[str, Any] = {
            "name": name,
            "protocol": entry.protocol,
            "enabled": True,
            "api_key": None if api_key_ref == "-" else api_key_ref,
            "base_url": entry.base_url or None,
            "default_model": model,
            "tier": tier,
        }
        result = self._persist_new_provider(provider_entry)
        # 4. EXIT
        log.config.debug(
            "[commands] provider.add_tier: exit", extra={"_fields": {"name": name}}
        )
        return result

    @staticmethod
    def _unique_provider_name(base: str, existing: list[Any]) -> str:
        """Auto-suffix (groq, groq-2, groq-3, ...) so adding the SAME catalog
        provider twice — e.g. two free-tier keys for round-robin — never
        collides on name."""
        if base not in existing:
            return base
        suffix = 2
        while f"{base}-{suffix}" in existing:
            suffix += 1
        return f"{base}-{suffix}"

    def _persist_new_provider(self, entry: dict[str, Any]) -> str:
        """Validate + save a new provider entry. Shared by the positional
        ``_add`` and the guided add-flow's final ``_add_tier`` step (DRY) —
        both need the same schema-validation/write/persisted-check/reload
        sequence, only how the entry dict gets built differs."""
        # 1. ENTRY
        name = entry["name"]
        log.config.debug(
            "[commands] provider.persist_new_provider: entry", extra={"_fields": {"name": name}}
        )
        # 2. DECISION — validate via the real schema BEFORE writing, so a bad
        # value is rejected with a clear message and we never leave an orphan
        # write behind a failed add.
        try:
            ProviderConfig(**entry)
        except Exception as exc:
            log.config.warning(
                "[commands] provider.persist_new_provider: schema validation failed",
                extra={"_fields": {"name": name, "error": str(exc)}},
            )
            return f"✗ Invalid provider config: {exc}"

        # 3. STEP — write, then re-read to confirm the mutation actually
        # persisted (F-81: save_yaml is otherwise fire-and-forget).
        path = config_path()
        data = load_yaml(path)
        providers = self._providers(data)
        providers.append(entry)
        save_yaml(path, data)
        if not self._persisted(path, name):
            log.config.error(
                "[commands] provider.persist_new_provider: write did not persist",
                extra={"_fields": {"name": name}},
            )
            return (
                f"✗ Provider '{name}' was not saved — the config file did not "
                "reflect the change (check file permissions/disk). Nothing was "
                "added."
            )
        self._emit_reloaded(name)
        # 4. EXIT
        log.config.info(
            "[commands] provider.persist_new_provider: exit — added",
            extra={"_fields": {"name": name, "protocol": entry.get("protocol"), "tier": entry.get("tier")}},
        )
        key_note = f" (api_key ref: {entry['api_key']})" if entry.get("api_key") else ""
        return f"✓ Provider '{name}' added{key_note} — applied immediately"

    # -- add -------------------------------------------------------------------

    def _add(self, raw: str) -> str:
        log.config.debug("[commands] provider.add: entry", extra={"_fields": {"raw_len": len(raw)}})
        tokens = raw.split()
        if len(tokens) < 4:
            return _USAGE
        name, protocol, default_model, tier = tokens[0], tokens[1], tokens[2], tokens[3]

        # Parse optional trailing args: a base_url and/or token=<RAW>.
        base_url: str | None = None
        token: str | None = None
        for extra in tokens[4:]:
            if extra.startswith(_TOKEN_PREFIX):
                token = extra[len(_TOKEN_PREFIX) :]
            elif base_url is None:
                base_url = extra
            else:
                return f"✗ Unexpected argument: {extra}\n{_USAGE}"

        if protocol not in _VALID_PROTOCOLS:
            log.config.warning(
                "[commands] provider.add: invalid protocol",
                extra={"_fields": {"protocol": protocol}},
            )
            return f"✗ Invalid protocol '{protocol}' — valid: {', '.join(_VALID_PROTOCOLS)}"
        if tier not in _VALID_TIERS:
            log.config.warning(
                "[commands] provider.add: invalid tier",
                extra={"_fields": {"tier": tier}},
            )
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"

        path = config_path()
        data = load_yaml(path)
        providers = self._providers(data)
        if any(p.get("name") == name for p in providers):
            log.config.warning(
                "[commands] provider.add: duplicate name",
                extra={"_fields": {"name": name}},
            )
            return f"✗ Provider '{name}' already exists — remove it first or pick another name"

        entry: dict[str, Any] = {
            "name": name,
            "protocol": protocol,
            "enabled": True,
            "api_key": None,
            "base_url": base_url,
            "default_model": default_model,
            "tier": tier,
        }

        # Validate the entry BEFORE storing any secret — we never leave an
        # orphan stored secret (keyring/file) behind a failed add. protocol/
        # tier are already checked above against the same Literal choices
        # ProviderConfig enforces, so today no reachable input trips this —
        # but that's coincidental to the current schema, not guaranteed by
        # it, so this pre-check (not the Literal checks above) is the actual
        # gate on store_secret below. _persist_new_provider re-validates the
        # complete entry (incl. the api_key ref) again further down; that
        # second check is cheap and idempotent, it just isn't early enough
        # to protect store_secret on its own.
        try:
            ProviderConfig(**entry)
        except Exception as exc:
            log.config.warning(
                "[commands] provider.add: schema validation failed — no secret stored",
                extra={"_fields": {"name": name, "error": str(exc)}},
            )
            return f"✗ Invalid provider config: {exc}"

        # Store the secret (if any) and keep only the resolver REF — never the
        # raw token.
        if token:
            log.config.debug(
                "[commands] provider.add: storing secret",
                extra={"_fields": {"name": name}},  # token length/value never logged
            )
            _description, api_key_ref = store_secret(f"stackowl-provider-{name}", token)
            entry["api_key"] = api_key_ref

        log.config.debug(
            "[commands] provider.add: exit — handing off to persist",
            extra={"_fields": {"name": name, "protocol": protocol, "tier": tier}},
        )
        return self._persist_new_provider(entry)

    # -- remove ----------------------------------------------------------------

    def _remove(self, raw: str) -> str:
        log.config.debug("[commands] provider.remove: entry", extra={"_fields": {"raw_len": len(raw)}})
        name = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not name:
            return "Usage: /provider remove <name>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        remaining = [p for p in providers if p.get("name") != name]
        if len(remaining) == len(providers):
            log.config.warning(
                "[commands] provider.remove: not found",
                extra={"_fields": {"name": name}},
            )
            return f"✗ Provider '{name}' not found"
        data["providers"] = remaining
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.remove: exit — removed",
            extra={"_fields": {"name": name}},
        )
        return (
            f"✓ Provider '{name}' removed — applied immediately. "
            "Its stored secret (if any) was left in place."
        )

    # -- set-tier --------------------------------------------------------------

    def _set_tier(self, raw: str) -> str:
        log.config.debug("[commands] provider.set_tier: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split()
        if len(bits) < 2:
            return "Usage: /provider set-tier <name> <tier>"
        name, tier = bits[0], bits[1]
        if tier not in _VALID_TIERS:
            log.config.warning(
                "[commands] provider.set_tier: invalid tier",
                extra={"_fields": {"tier": tier}},
            )
            return f"✗ Invalid tier '{tier}' — valid: {', '.join(_VALID_TIERS)}"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        providers = self._providers(data)
        target = next((p for p in providers if p.get("name") == name), None)
        if target is None:
            log.config.warning(
                "[commands] provider.set_tier: not found",
                extra={"_fields": {"name": name}},
            )
            return f"✗ Provider '{name}' not found"
        target["tier"] = tier
        save_yaml(path, data)
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.set_tier: exit — updated",
            extra={"_fields": {"name": name, "tier": tier}},
        )
        return f"✓ Provider '{name}' tier set to {tier} — applied immediately"


# Pattern-A self-registration removed (Epic C1): ProviderCommand is now a DI
# command wired with the live event_bus via assembly._register_di_commands.
# A module-level _CMD = register_command(ProviderCommand()) would permanently
# fix event_bus=None on the registered instance, silencing all _emit_* calls.
