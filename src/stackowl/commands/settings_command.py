"""SettingsCommand — /settings slash command for high-level user preferences.

Currently exposes a single subcommand — ``autonomy <low|medium|high>`` — which
maps directly to :attr:`Settings.autonomy_level`.  Persisted via the same
YAML config used by :class:`ConfigCommand`, and an event is emitted so live
subsystems can pick the change up without a restart.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml, set_nested
from stackowl.events.bus import EventBus
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_VALID_AUTONOMY: frozenset[str] = frozenset({"low", "medium", "high"})

_USAGE = (
    "Usage: /settings autonomy <low|medium|high>\n"
    "  Sets how much autonomy owls have when invoking tools."
)


class SettingsCommand(SlashCommand):
    """Implements ``/settings autonomy <level>``."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._bus = event_bus

    @property
    def command(self) -> str:
        return "settings"

    @property
    def description(self) -> str:
        return "Manage high-level user settings (autonomy level, …)."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] settings.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split(maxsplit=1)
        if not parts:
            log.gateway.debug("[commands] settings.handle: no subcommand — returning usage")
            return _USAGE
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub != "autonomy":
            log.gateway.debug(
                "[commands] settings.handle: unknown subcommand",
                extra={"_fields": {"sub": sub}},
            )
            return _USAGE
        try:
            result = self._set_autonomy(rest)
        except Exception as exc:
            log.gateway.error(
                "[commands] settings.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /settings {sub}: {exc}"
        log.gateway.debug("[commands] settings.handle: exit", extra={"_fields": {"sub": sub}})
        return result

    def _set_autonomy(self, value: str) -> str:
        log.gateway.debug(
            "[commands] settings.set_autonomy: entry",
            extra={"_fields": {"value": value[:40]}},
        )
        level = value.lower()
        if not level:
            return _USAGE
        if level not in _VALID_AUTONOMY:
            log.gateway.warning(
                "[commands] settings.set_autonomy: rejected invalid level",
                extra={"_fields": {"value": value[:40]}},
            )
            return f"✗ Invalid autonomy level: {value!r} — valid: {sorted(_VALID_AUTONOMY)}"
        path = config_path()
        data = load_yaml(path)
        set_nested(data, ["autonomy_level"], level)
        save_yaml(path, data)
        if self._bus is not None:
            self._bus.emit(
                "settings_changed",
                {"key": "autonomy_level", "value": level},
            )
        log.gateway.info(
            "[commands] settings.set_autonomy: exit",
            extra={"_fields": {"level": level}},
        )
        return f"✓ autonomy_level = {level}"


# Pattern-A self-registration removed (Epic C1): SettingsCommand is now a DI
# command wired with the live event_bus via assembly._register_di_commands.
# A module-level _CMD = register_command(SettingsCommand()) would permanently
# fix event_bus=None on the registered instance, silencing all _emit_* calls.
