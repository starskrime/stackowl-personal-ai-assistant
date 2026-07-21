"""ConfigCommand — /config slash command for runtime settings management.

Subcommands: ``list``, ``get <key>``, ``set <key> <value>``, ``reset <key>``,
``export``.  Writes use :mod:`ruamel.yaml` to preserve comments and key order.

SECURITY NFR33: any field marked ``sensitive=True`` in its Pydantic
``json_schema_extra`` cannot be set via ``/config set`` — the user is
redirected to :class:`SecretResolver` syntax.
"""

from __future__ import annotations

from typing import Any

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import (
    coerce_scalar,
    collect_sensitive,
    config_path,
    delete_nested,
    flatten,
    load_yaml,
    resolve_field,
    save_yaml,
    set_nested,
    stringify,
)
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.config.settings import Settings
from stackowl.events.bus import EventBus
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_NO_FILE = "No stackowl.yaml found — run stackowl init first"

_CONFIG_META = CommandMeta(
    grammar="verb",
    group="Configuration",
    subcommands=(
        SubCommand(
            name="list",
            summary="Show all settings in dot notation",
            description="You see every configured setting, sorted, with sensitive values masked.",
            examples=(Example(invocation="/config list"),),
        ),
        SubCommand(
            name="get",
            summary="Read a single setting",
            description="You read one setting by its dot-notation key.",
            args=(Arg(name="key", summary="dot-notation setting key"),),
            examples=(Example(invocation="/config get heartbeat.interval_minutes"),),
        ),
        SubCommand(
            name="set",
            summary="Write a single setting",
            description=(
                "You write one setting. Sensitive fields are refused — set those "
                "via SecretResolver syntax in stackowl.yaml instead."
            ),
            args=(
                Arg(name="key", summary="dot-notation setting key"),
                Arg(name="value", summary="new value (coerced to type)"),
            ),
            examples=(Example(invocation="/config set heartbeat.interval_minutes 30"),),
        ),
        SubCommand(
            name="reset",
            summary="Revert a setting to its default",
            description="You remove the override for a key so it falls back to its default.",
            args=(Arg(name="key", summary="dot-notation setting key"),),
            examples=(Example(invocation="/config reset heartbeat.interval_minutes"),),
        ),
        SubCommand(
            name="export",
            summary="Dump the full settings file as YAML",
            description="You print the raw stackowl.yaml contents.",
            examples=(Example(invocation="/config export"),),
        ),
        SubCommand(
            name="detect-timezone",
            summary="Auto-detect system.timezone from this network's public IP",
            description=(
                "You geolocate the box's public IP to an IANA timezone and write "
                "it to system.timezone — every daily@HH:MM scheduled job re-arms "
                "at that local time instead of UTC. Falls back to a manual /config "
                "set system.timezone <IANA name> suggestion if detection fails."
            ),
            examples=(Example(invocation="/config detect-timezone"),),
        ),
    ),
)


class ConfigCommand(SlashCommand):
    """Implements /config list|get|set|reset|export."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._bus = event_bus

    @property
    def command(self) -> str:
        return "config"

    @property
    def description(self) -> str:
        return "Read and write StackOwl settings at runtime."

    @property
    def meta(self) -> CommandMeta:
        return _CONFIG_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.config.debug(
            "[commands] config.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        if not parts:
            log.config.debug("[commands] config.handle: no subcommand — returning usage")
            return render_usage("config", _CONFIG_META)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if sub == "list":
                result = self._list()
            elif sub == "get":
                result = self._get(rest)
            elif sub == "set":
                result = self._set(rest)
            elif sub == "reset":
                result = self._reset(rest)
            elif sub == "export":
                result = self._export()
            elif sub == "detect-timezone":
                result = await self._detect_timezone()
            else:
                log.config.debug(
                    "[commands] config.handle: unknown subcommand",
                    extra={"_fields": {"sub": sub}},
                )
                return render_usage("config", _CONFIG_META)
        except Exception as exc:
            log.config.error(
                "[commands] config.handle: subcommand failed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /config {sub}: {exc}"
        log.config.debug("[commands] config.handle: exit", extra={"_fields": {"sub": sub}})
        return result

    @staticmethod
    def _lookup(data: dict[str, Any], key: str) -> tuple[bool, Any]:
        """Walk a dotted ``key`` through ``data``; return ``(found, value)``.

        Used to re-read the file after a write and verify the mutation landed.
        ``load_yaml`` returns ``{}`` on a parse failure, so a corrupt write
        surfaces here as ``found=False``.
        """
        cursor: Any = data
        for part in key.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return False, None
            cursor = cursor[part]
        return True, cursor

    def _list(self) -> str:
        log.config.debug("[commands] config.list: entry")
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        sensitive: set[str] = set()
        collect_sensitive(Settings, "", sensitive)
        pairs: list[tuple[str, str]] = []
        flatten("", data, sensitive, pairs)
        pairs.sort(key=lambda kv: kv[0])
        if not pairs:
            return "(no settings)"
        log.config.debug("[commands] config.list: exit", extra={"_fields": {"count": len(pairs)}})
        return "\n".join(f"{k}: {v}" for k, v in pairs)

    def _get(self, key: str) -> str:
        log.config.debug("[commands] config.get: entry", extra={"_fields": {"key": key}})
        if not key:
            return "Usage: /config get <key>"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        sensitive: set[str] = set()
        collect_sensitive(Settings, "", sensitive)
        cursor: Any = data
        for part in key.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return f"{key}: (not set)"
            cursor = cursor[part]
        rendered = "***" if key in sensitive else stringify(cursor)
        return f"{key}: {rendered}"

    def _set(self, raw: str) -> str:
        log.config.debug("[commands] config.set: entry", extra={"_fields": {"raw_len": len(raw)}})
        bits = raw.split(maxsplit=1)
        if len(bits) < 2:
            return "Usage: /config set <key> <value>"
        key, value_raw = bits[0], bits[1]
        owner, _leaf, _default, extra = resolve_field(Settings, key)
        if owner is None:
            return f"✗ Unknown setting: {key}"
        if extra.get("sensitive"):
            log.config.warning(
                "[commands] config.set: rejected sensitive field",
                extra={"_fields": {"key": key}},
            )
            return (
                f"✗ {key} is sensitive — set it via SecretResolver syntax in "
                "stackowl.yaml: 'keychain:<service>', 'file:<path>', or "
                "'<ENV_VAR_NAME>'."
            )
        coerced = coerce_scalar(value_raw)
        path = config_path()
        data = load_yaml(path)
        set_nested(data, key.split("."), coerced)
        try:
            Settings.model_validate(data)
        except Exception as exc:
            log.config.warning(
                "[commands] config.set: validation failed",
                extra={"_fields": {"key": key, "error": str(exc)}},
            )
            return f"✗ Validation failed: {exc}"
        save_yaml(path, data)
        # F-81: confirm the write actually persisted + parses before claiming ✓.
        found, persisted_val = self._lookup(load_yaml(path), key)
        if not found or stringify(persisted_val) != stringify(coerced):
            log.config.error(
                "[commands] config.set: write did not persist",
                extra={"_fields": {"key": key}},
            )
            return (
                f"✗ {key} was not saved — the config file did not reflect the "
                "change (check file permissions/disk)."
            )
        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.config.error(
                    "[commands] config.set: immediate reload failed — falling "
                    "back to background ConfigWatcher poll",
                    exc_info=exc,
                    extra={"_fields": {"key": key}},
                )
        hot = extra.get("hot_reload", True)
        suffix = "" if hot else " — restart required"
        log.config.info(
            "[commands] config.set: exit",
            extra={"_fields": {"key": key, "hot_reload": hot}},
        )
        return f"✓ {key} = {stringify(coerced)}{suffix}"

    def _reset(self, key: str) -> str:
        log.config.debug("[commands] config.reset: entry", extra={"_fields": {"key": key}})
        if not key:
            return "Usage: /config reset <key>"
        owner, _leaf, _default, _extra = resolve_field(Settings, key)
        if owner is None:
            return f"✗ Unknown setting: {key}"
        path = config_path()
        if not path.exists():
            return _NO_FILE
        data = load_yaml(path)
        removed = delete_nested(data, key.split("."))
        if not removed:
            return f"{key}: (already at default)"
        save_yaml(path, data)
        # F-81: confirm the key is actually gone on disk before claiming ✓.
        found, _ = self._lookup(load_yaml(path), key)
        if found:
            log.config.error(
                "[commands] config.reset: write did not persist",
                extra={"_fields": {"key": key}},
            )
            return (
                f"✗ {key} was not reset — the config file still contains the "
                "override (check file permissions/disk)."
            )
        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.config.error(
                    "[commands] config.reset: immediate reload failed — falling "
                    "back to background ConfigWatcher poll",
                    exc_info=exc,
                    extra={"_fields": {"key": key}},
                )
        log.config.info("[commands] config.reset: exit", extra={"_fields": {"key": key}})
        return f"✓ {key} reverted to default"

    async def _detect_timezone(self) -> str:
        log.config.debug("[commands] config.detect_timezone: entry")
        from stackowl.infra.net.timezone_detect import detect_timezone_from_ip

        detected = await detect_timezone_from_ip()
        if detected is None:
            log.config.info("[commands] config.detect_timezone: exit — detection failed")
            return (
                "✗ Could not auto-detect a timezone from this network's public IP. "
                "Set it manually: /config set system.timezone <IANA name> "
                "(e.g. Europe/Istanbul, America/New_York)."
            )
        # Reuse _set's own validated write + verify-persisted + hot-reload-emit
        # path rather than duplicating it — the detected value goes through the
        # exact same guarantees a manual /config set gets.
        result = self._set(f"system.timezone {detected}")
        log.config.info(
            "[commands] config.detect_timezone: exit",
            extra={"_fields": {"detected": detected}},
        )
        return f"Detected timezone: {detected}\n{result}"

    def _export(self) -> str:
        log.config.debug("[commands] config.export: entry")
        path = config_path()
        if not path.exists():
            return _NO_FILE
        with path.open("r", encoding="utf-8") as fh:
            text = fh.read()
        log.config.debug(
            "[commands] config.export: exit",
            extra={"_fields": {"bytes": len(text)}},
        )
        return text


# Pattern-A self-registration removed (Epic C1): ConfigCommand is now a DI
# command wired with the live event_bus via assembly._register_di_commands.
# A module-level _CMD = register_command(ConfigCommand()) would permanently fix
# event_bus=None on the registered instance, silencing all _emit_* calls.
