"""PluginContext — capability-filtered view of StackOwl registries for a plugin."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from stackowl.exceptions import PluginCapabilityDeniedError
from stackowl.plugins import capabilities as caps

if TYPE_CHECKING:
    from stackowl.channels.registry import ChannelRegistry
    from stackowl.commands.registry import CommandRegistry
    from stackowl.owls.registry import OwlRegistry
    from stackowl.scheduler.base import HandlerRegistry
    from stackowl.tools.registry import ToolRegistry

log = logging.getLogger("stackowl.plugins")


class PluginContext:
    """Capability-filtered view of StackOwl registries for a plugin."""

    def __init__(
        self,
        plugin_name: str,
        granted: list[str],
        tool_registry: ToolRegistry | None = None,
        command_registry: CommandRegistry | None = None,
        handler_registry: HandlerRegistry | None = None,
        channel_registry: ChannelRegistry | None = None,
        owl_registry: OwlRegistry | None = None,
        memory_bridge: Any = None,
        event_bus: Any = None,
        audit_logger: Any = None,
    ) -> None:
        log.debug(
            "plugins.context.__init__: entry",
            extra={"_fields": {"plugin": plugin_name, "granted": sorted(granted)}},
        )
        self._plugin_name = plugin_name
        self._granted = frozenset(granted)
        self._tool_registry = tool_registry
        self._command_registry = command_registry
        self._handler_registry = handler_registry
        self._channel_registry = channel_registry
        self._owl_registry = owl_registry
        self._memory_bridge = memory_bridge
        self._event_bus = event_bus
        self._audit_logger = audit_logger
        log.debug(
            "plugins.context.__init__: exit",
            extra={"_fields": {"plugin": plugin_name}},
        )

    def _require(self, capability: str, value: Any) -> Any:
        if capability not in self._granted:
            err = PluginCapabilityDeniedError(capability)
            log.critical(
                "plugins.context._require: capability denied",
                exc_info=err,
                extra={"_fields": {"plugin": self._plugin_name, "capability": capability}},
            )
            raise err
        return value

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._require(caps.TOOL_REGISTRY, self._tool_registry)

    @property
    def command_registry(self) -> CommandRegistry:
        return self._require(caps.COMMAND_REGISTRY, self._command_registry)

    @property
    def handler_registry(self) -> HandlerRegistry:
        return self._require(caps.HANDLER_REGISTRY, self._handler_registry)

    @property
    def channel_registry(self) -> ChannelRegistry:
        return self._require(caps.CHANNEL_REGISTRY, self._channel_registry)

    @property
    def owl_registry(self) -> OwlRegistry:
        return self._require(caps.OWL_REGISTRY, self._owl_registry)

    @property
    def memory_bridge(self) -> Any:
        return self._require(caps.MEMORY_BRIDGE, self._memory_bridge)

    @property
    def event_bus(self) -> Any:
        return self._require(caps.EVENT_BUS, self._event_bus)

    @property
    def audit_logger(self) -> Any:
        return self._require(caps.AUDIT_LOGGER, self._audit_logger)
