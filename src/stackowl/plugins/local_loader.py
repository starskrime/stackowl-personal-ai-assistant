"""LocalPluginLoader — loads a local Python plugin into StackOwl registries."""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from stackowl.exceptions import PluginValidationError
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
    ) -> None:
        log.debug("plugins.local_loader.__init__: entry")
        self._registries = {
            "Tool": tool_registry,
            "JobHandler": handler_registry,
            "SlashCommand": command_registry,
            "ChannelAdapter": channel_registry,
            "OwlSource": owl_registry,
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
                    registry = self._registries.get(abc_name)
                    if registry is None:
                        continue
                    try:
                        instance = obj()
                        registry.register(instance, source_name=manifest.name)
                        log.debug(
                            "plugins.local_loader._register_classes: step — registered %s",
                            attr_name,
                        )
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
