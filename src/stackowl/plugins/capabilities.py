"""Enumeration of all grantable plugin capabilities, and the one place they are checked.

A capability is what an operator GRANTS a plugin in its ``plugin.yaml``. Until
2026-08-19 that grant was decorative: ``PluginContext`` — the capability-filtered
view that raises ``PluginCapabilityDeniedError`` — had ZERO construction sites in
``src/``, and ``manifest.capabilities`` was parsed by the manifest model and read by
nothing. A plugin declaring no capabilities still had its Tool registered into the
live tool registry. D16.1's LifecycleHook needed the gate to be real (its invariant
I6: an ungranted hook does not run, enforced at LOAD so a denied plugin never
reaches a call site), and gating hooks alone would have left six ungated siblings —
an actuator wired on one path out of seven.

So the check lives HERE, once, and both readers ask it: ``LocalPluginLoader`` at
registration time and ``PluginContext`` when a plugin reaches for a registry. Two
copies of one permission rule is how the two disagree, and the disagreement would
be silent.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from stackowl.exceptions import PluginCapabilityDeniedError
from stackowl.infra.observability import log

TOOL_REGISTRY: Final = "tool_registry"
COMMAND_REGISTRY: Final = "command_registry"
HANDLER_REGISTRY: Final = "handler_registry"
CHANNEL_REGISTRY: Final = "channel_registry"
OWL_REGISTRY: Final = "owl_registry"
MEMORY_BRIDGE: Final = "memory_bridge"
EVENT_BUS: Final = "event_bus"
AUDIT_LOGGER: Final = "audit_logger"
BROWSER_RUNTIME: Final = "browser_runtime"
#: D08.2 slice C made MemoryProvider an extension point and never gave it a
#: capability, the same omission as its missing registry slot (fixed in fed6968d).
MEMORY_PROVIDER: Final = "memory_provider"
#: D16.1 / ESC-16. ONE capability rather than one per hook point: the hook points a
#: plugin implements are derived from the methods it overrides, so six capabilities
#: would state the same fact twice.
LIFECYCLE_HOOKS: Final = "lifecycle_hooks"
#: D16.3 / E2 (Bakir, 2026-08-21: "everything, including prompt contributors").
#: THE HEAVIEST GRANT ON THIS LIST, and it is gated precisely because of that: a
#: contributor writes into the SYSTEM PROMPT, on every turn, uncovered by consent, and
#: it rides the frozen prefix for the life of an incarnation. A tool has to be called;
#: this simply speaks. The capability is what keeps "extensible" from meaning
#: "unguarded" — see designs/D16.3.md for the argument that was traded away.
PROMPT_CONTRIBUTOR: Final = "prompt_contributor"

ALL_CAPABILITIES: Final = frozenset({
    TOOL_REGISTRY,
    COMMAND_REGISTRY,
    HANDLER_REGISTRY,
    CHANNEL_REGISTRY,
    OWL_REGISTRY,
    MEMORY_BRIDGE,
    EVENT_BUS,
    AUDIT_LOGGER,
    BROWSER_RUNTIME,
    MEMORY_PROVIDER,
    LIFECYCLE_HOOKS,
    PROMPT_CONTRIBUTOR,
})

#: Extension point (``_ABC_NAMES`` key) -> the capability an operator must have
#: granted before a plugin may register one. Every declared point appears here;
#: tests/plugins/test_a_declared_capability_is_actually_enforced.py asserts both
#: directions, so a new extension point cannot arrive ungated.
CAPABILITY_FOR_EXTENSION_POINT: Final = {
    "Tool": TOOL_REGISTRY,
    "JobHandler": HANDLER_REGISTRY,
    "SlashCommand": COMMAND_REGISTRY,
    "ChannelAdapter": CHANNEL_REGISTRY,
    "OwlSource": OWL_REGISTRY,
    "MemoryProvider": MEMORY_PROVIDER,
    "LifecycleHook": LIFECYCLE_HOOKS,
    "PromptContributor": PROMPT_CONTRIBUTOR,
}


def require(plugin_name: str, granted: Iterable[str], capability: str) -> None:
    """Raise :class:`PluginCapabilityDeniedError` unless ``capability`` was granted.

    Fails CLOSED and loudly: an ungranted plugin does not load at all, rather than
    loading with the ungranted half quietly missing. A half-loaded plugin looks
    installed and behaves as if part of it were broken, which is the harder failure
    to diagnose of the two.
    """
    if capability in set(granted):
        return
    err = PluginCapabilityDeniedError(capability)
    log.plugins.error(
        "[plugins] capability DENIED — the plugin did not declare it",
        exc_info=err,
        extra={"_fields": {"plugin": plugin_name, "capability": capability}},
    )
    raise err
