"""Enumeration of all grantable plugin capabilities."""

from __future__ import annotations

from typing import Final

TOOL_REGISTRY: Final = "tool_registry"
COMMAND_REGISTRY: Final = "command_registry"
HANDLER_REGISTRY: Final = "handler_registry"
CHANNEL_REGISTRY: Final = "channel_registry"
OWL_REGISTRY: Final = "owl_registry"
MEMORY_BRIDGE: Final = "memory_bridge"
EVENT_BUS: Final = "event_bus"
AUDIT_LOGGER: Final = "audit_logger"

ALL_CAPABILITIES: Final = frozenset({
    TOOL_REGISTRY,
    COMMAND_REGISTRY,
    HANDLER_REGISTRY,
    CHANNEL_REGISTRY,
    OWL_REGISTRY,
    MEMORY_BRIDGE,
    EVENT_BUS,
    AUDIT_LOGGER,
})
