"""LocalPluginLoader — loads a local Python plugin into StackOwl registries."""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from stackowl.exceptions import PluginValidationError
from stackowl.plugins import capabilities as caps
from stackowl.plugins.manifest import PluginManifest

log = logging.getLogger("stackowl.plugins")

_ABC_NAMES = {
    "Tool": "stackowl.tools.base",
    "JobHandler": "stackowl.scheduler.base",
    "SlashCommand": "stackowl.commands.base",
    "ChannelAdapter": "stackowl.channels.base",
    "OwlSource": "stackowl.owls.base",
    # D08.2 slice C. One entry — the loader, verifier, index and remote-install
    # path all apply unchanged, which is why a separate memory-provider registry
    # was rejected as duplicated machinery.
    "MemoryProvider": "stackowl.memory.providers",
    # D16.1 / ESC-16 (Bakir, 2026-08-17). The seventh point, and the first that
    # OBSERVES the agent rather than adding to it. Capability-gated and
    # observe-only — see plugins/hooks.py for why a veto is a v1 non-goal.
    "LifecycleHook": "stackowl.plugins.hooks",
}


class LocalPluginLoader:
    """Loads a local Python plugin from a directory."""

    def __init__(
        self,
        tool_registry: Any = None,
        command_registry: Any = None,
        handler_registry: Any = None,
        channel_registry: Any = None,
        owl_registry: Any = None,
        memory_provider_registry: Any = None,
        hook_registry: Any = None,
    ) -> None:
        log.debug("plugins.local_loader.__init__: entry")
        # EVERY key in _ABC_NAMES must appear here. D08.2 slice C added
        # MemoryProvider to the ABC table and not to this one, so
        # `_registries.get("MemoryProvider")` returned None and _register_classes
        # hit `continue` — SILENTLY. A memory-provider plugin would have loaded,
        # been discovered by issubclass, and registered nowhere, with the platform
        # reporting a successful install of a plugin that does nothing.
        # tests/plugins/test_every_declared_extension_point_can_register.py asserts
        # the two tables agree, in BOTH directions, so the seventh extension point
        # (LifecycleHook, designed in designs/D16.1.md) cannot repeat this.
        self._registries = {
            "Tool": tool_registry,
            "JobHandler": handler_registry,
            "SlashCommand": command_registry,
            "ChannelAdapter": channel_registry,
            "OwlSource": owl_registry,
            "MemoryProvider": memory_provider_registry,
            # Injected like every other slot rather than defaulted to the
            # process-wide singleton: a slot that fills itself in cannot be
            # reported as unwired, and "which extension points is this deployment
            # missing?" is a question the loader is supposed to be able to answer.
            "LifecycleHook": hook_registry,
        }
        log.debug("plugins.local_loader.__init__: exit")

    def load(self, plugin_dir: Path) -> PluginManifest:
        """Load local plugin from directory. Returns validated manifest."""
        log.debug(
            "plugins.local_loader.load: entry",
            extra={"_fields": {"dir": str(plugin_dir)}},
        )
        plugin_yaml = plugin_dir / "plugin.yaml"
        if not plugin_yaml.exists():
            raise PluginValidationError(str(plugin_dir), "missing plugin.yaml")

        try:
            raw = yaml.safe_load(plugin_yaml.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("plugins.local_loader.load: yaml parse failed", exc_info=exc)
            raise PluginValidationError(str(plugin_dir), f"invalid plugin.yaml: {exc}") from exc

        try:
            manifest = PluginManifest(**raw)
        except Exception as exc:
            log.error("plugins.local_loader.load: manifest validation failed", exc_info=exc)
            raise PluginValidationError(str(plugin_dir), f"manifest invalid: {exc}") from exc

        log.debug(
            "plugins.local_loader.load: decision — appending to sys.path",
            extra={"_fields": {"path": str(plugin_dir)}},
        )
        path_str = str(plugin_dir)
        if path_str not in sys.path:
            sys.path.append(path_str)

        try:
            module = importlib.import_module(manifest.entry_point)
        except Exception as exc:
            log.error(
                "plugins.local_loader.load: import failed",
                exc_info=exc,
                extra={"_fields": {"entry_point": manifest.entry_point}},
            )
            if path_str in sys.path:
                sys.path.remove(path_str)
            raise PluginValidationError(manifest.name, f"import failed: {exc}") from exc

        log.debug(
            "plugins.local_loader.load: step — module imported",
            extra={"_fields": {"entry_point": manifest.entry_point}},
        )
        self._register_classes(module, manifest)
        log.debug(
            "plugins.local_loader.load: exit",
            extra={"_fields": {"name": manifest.name}},
        )
        return manifest

    @staticmethod
    def _defined_here(obj: type, module: Any) -> bool:
        """True when ``obj`` is defined by ``module`` rather than imported into it.

        Compares ``__module__`` against the module's own name, and accepts a
        submodule of a package plugin (``acme.tools`` inside ``acme``) so a plugin
        may organise itself across files. Anything unreadable is treated as NOT
        defined here — the safe direction is to under-register a plugin's own class
        (a visible missing tool) rather than over-register someone else's (a silent
        duplicate nobody asked for).
        """
        try:
            owner = str(obj.__module__ or "")
            here = str(getattr(module, "__name__", "") or "")
        except Exception:  # pragma: no cover — defensive; a class with no __module__
            return False
        if not owner or not here:
            return False
        return owner == here or owner.startswith(f"{here}.")

    def _register_classes(self, module: Any, manifest: PluginManifest) -> None:
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if not isinstance(obj, type):
                continue
            # Only classes this plugin DEFINES. dir(module) returns imported names
            # too, so without this a plugin that merely does
            # `from stackowl.tools.web import WebSearchTool` — or imports another
            # plugin's Tool — registers that class as its own, twice over if both
            # plugins load. Found by D16.1's brainstorm, before any real plugin
            # existed to be bitten by it: zero are installed today, so this is a
            # trap set for the FIRST one.
            if not self._defined_here(obj, module):
                continue
            for abc_name, mod_path in _ABC_NAMES.items():
                try:
                    abc_mod = importlib.import_module(mod_path)
                    abc_cls = getattr(abc_mod, abc_name)
                except Exception:
                    log.warning(
                        "[plugins] local_loader: extension-point ABC unresolvable — skipping",
                        exc_info=True,
                        extra={"_fields": {"abc_name": abc_name, "mod_path": mod_path}},
                    )
                    continue
                if issubclass(obj, abc_cls) and obj is not abc_cls:
                    # THE GATE, and it is load-time on purpose: a denied plugin
                    # never reaches a call site (D16.1 invariant I6). Until
                    # 2026-08-19 `capabilities` was declared in every manifest and
                    # read by nothing — PluginContext, the only thing that checked
                    # it, had zero construction sites — so a plugin granted nothing
                    # registered whatever it liked. Raises, so the whole plugin is
                    # refused rather than half-loaded.
                    caps.require(
                        manifest.name,
                        manifest.capabilities,
                        caps.CAPABILITY_FOR_EXTENSION_POINT[abc_name],
                    )
                    registry = self._registries.get(abc_name)
                    if registry is None:
                        # NOT silent. This plugin defines a real extension point and
                        # it will not be active — a fact only visible here. The bare
                        # `continue` that used to stand alone is how MemoryProvider
                        # went unregistrable without anyone noticing. WARNING rather
                        # than raise: a loader constructed with only the registries a
                        # caller needs is legitimate, so this is a degrade to report,
                        # not a validation failure to abort on.
                        log.warning(
                            "[plugins] local_loader: extension point not wired in this "
                            "deployment — the class was discovered and will NOT be active",
                            extra={"_fields": {"plugin": manifest.name,
                                               "class": attr_name,
                                               "extension_point": abc_name}},
                        )
                        continue
                    try:
                        try:
                            instance = obj()
                        except TypeError as exc:
                            # STATE THE CONTRACT rather than let a raw TypeError
                            # about a missing positional argument reach the author.
                            # Extension points are constructed with NO arguments,
                            # and nothing passes plugin configuration to a
                            # constructor today: PluginManifest carries
                            # config_schema (a shape) and no config VALUES, and no
                            # store holds any. Found by being the first user of
                            # this surface (D16.1, 2026-08-16).
                            raise PluginValidationError(
                                manifest.name,
                                f"registration of {attr_name} failed: an extension "
                                f"point is constructed with no arguments, and "
                                f"{attr_name} requires some ({exc}). Read what it "
                                f"needs inside the class instead — plugin "
                                f"configuration is not passed to constructors.",
                            ) from exc
                        registry.register(instance, source_name=manifest.name)
                        log.debug(
                            "plugins.local_loader._register_classes: step — registered %s",
                            attr_name,
                        )
                    except PluginValidationError:
                        raise
                    except Exception as exc:
                        log.error(
                            "plugins.local_loader._register_classes: registration failed",
                            exc_info=exc,
                        )
                        raise PluginValidationError(
                            manifest.name, f"registration of {attr_name} failed: {exc}"
                        ) from exc

    def unload(self, plugin_dir: Path) -> None:
        """Remove plugin directory from sys.path."""
        log.debug(
            "plugins.local_loader.unload: entry",
            extra={"_fields": {"dir": str(plugin_dir)}},
        )
        path_str = str(plugin_dir)
        if path_str in sys.path:
            sys.path.remove(path_str)
            log.debug("plugins.local_loader.unload: step — removed from sys.path")
        log.debug("plugins.local_loader.unload: exit")
