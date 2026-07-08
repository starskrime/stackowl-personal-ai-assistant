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
from typing import Any

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
    "Usage: /provider <list|add|remove|set-tier|edit|enable|disable> [args]\n"
    "  /provider list\n"
    "  /provider add <name> <protocol> <default_model> <tier> "
    "[base_url] [token=<RAW_TOKEN>]\n"
    "  /provider remove <name>\n"
    "  /provider set-tier <name> <tier>\n"
    "  /provider edit <name> <protocol|default_model|base_url> <value>\n"
    "  /provider enable <name>\n"
    "  /provider disable <name>\n"
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
                    choices=("protocol", "default_model", "base_url"),
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
    ),
)


class ProviderCommand(SlashCommand):
    """Implements /provider list|add|remove|set-tier."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._bus = event_bus

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
                result = self._add(rest)
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
            elif sub == "menu":
                result = self._menu(rest)
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
                f"enabled={enabled} | api_key={key_disp}"
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
        text = (
            f"{name} | {protocol} | {model} | {tier} | enabled={enabled}\n"
            f"To edit protocol/model/base_url: /provider edit {name} <field> <value>"
        )
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
            return "Usage: /provider edit <name> <protocol|default_model|base_url> <value>"
        name, field, value = bits
        if field not in ("protocol", "default_model", "base_url"):
            return (
                f"✗ Unknown field '{field}' — use protocol, default_model, or base_url "
                "(tier: /provider set-tier, enabled: /provider enable|disable)"
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
        # Validate via the real schema BEFORE storing the secret or writing, so a
        # bad value is rejected with a clear message — and we never leave an
        # orphan stored secret behind a failed add.
        try:
            ProviderConfig(**entry)
        except Exception as exc:
            log.config.warning(
                "[commands] provider.add: schema validation failed",
                extra={"_fields": {"name": name, "error": str(exc)}},
            )
            return f"✗ Invalid provider config: {exc}"

        # Only after the entry validates: store the secret (if any) and keep
        # only the resolver REF — never the raw token.
        api_key_ref: str | None = None
        if token:
            log.config.debug(
                "[commands] provider.add: storing secret",
                extra={"_fields": {"name": name}},  # token length/value never logged
            )
            _description, api_key_ref = store_secret(f"stackowl-provider-{name}", token)
            entry["api_key"] = api_key_ref

        providers.append(entry)
        save_yaml(path, data)
        # F-81: save_yaml is otherwise fire-and-forget. Re-read the file and
        # confirm the new provider actually persisted + parses before claiming
        # success — a partial/permission-failed write must not print the ✓.
        if not self._persisted(path, name):
            log.config.error(
                "[commands] provider.add: write did not persist",
                extra={"_fields": {"name": name}},
            )
            return (
                f"✗ Provider '{name}' was not saved — the config file did not "
                "reflect the change (check file permissions/disk). Nothing was "
                "added."
            )
        self._emit_reloaded(name)
        log.config.info(
            "[commands] provider.add: exit — added",
            extra={"_fields": {"name": name, "protocol": protocol, "tier": tier}},
        )
        key_note = f" (api_key ref: {api_key_ref})" if api_key_ref else ""
        return (
            f"✓ Provider '{name}' added{key_note} — applied immediately"
        )

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
