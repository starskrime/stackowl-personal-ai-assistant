"""SkillPackLoader — loads skill packs from a directory into StackOwl registries."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from stackowl.exceptions import PluginValidationError
from stackowl.plugins.manifest import PluginManifest
from stackowl.tools.base import Tool

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.tools.registry import ToolRegistry

log = logging.getLogger("stackowl.plugins")


class SkillPackLoader:
    """Loads a skill pack directory into the given registries."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        owl_registry: OwlRegistry | None = None,
    ) -> None:
        log.debug("plugins.skill_pack_loader.__init__: entry")
        self._tool_registry = tool_registry
        self._owl_registry = owl_registry
        log.debug("plugins.skill_pack_loader.__init__: exit")

    def load(self, skill_pack_dir: Path) -> PluginManifest:
        """Load skill pack from directory. Returns validated manifest.

        Raises PluginValidationError on any validation failure.
        """
        log.debug(
            "plugins.skill_pack_loader.load: entry",
            extra={"_fields": {"dir": str(skill_pack_dir)}},
        )
        skill_yaml = skill_pack_dir / "skill.yaml"
        if not skill_yaml.exists():
            raise PluginValidationError(str(skill_pack_dir), "missing skill.yaml")

        try:
            raw = yaml.safe_load(skill_yaml.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("plugins.skill_pack_loader.load: yaml parse failed", exc_info=exc)
            raise PluginValidationError(str(skill_pack_dir), f"invalid skill.yaml: {exc}") from exc

        try:
            manifest = PluginManifest(**raw)
        except Exception as exc:
            log.error(
                "plugins.skill_pack_loader.load: manifest validation failed", exc_info=exc
            )
            raise PluginValidationError(str(skill_pack_dir), f"manifest invalid: {exc}") from exc

        log.debug(
            "plugins.skill_pack_loader.load: decision — loading tools, prompts, owls",
            extra={"_fields": {"name": manifest.name}},
        )

        tools_dir = skill_pack_dir / "tools"
        if tools_dir.exists():
            self._load_tools(tools_dir, manifest.name)

        prompts_dir = skill_pack_dir / "prompts"
        if prompts_dir.exists():
            self._load_prompts(prompts_dir, manifest.name)

        owls_yaml = skill_pack_dir / "owls.yaml"
        if owls_yaml.exists() and self._owl_registry is not None:
            self._load_owls(owls_yaml, manifest.name)

        log.debug(
            "plugins.skill_pack_loader.load: exit",
            extra={"_fields": {"name": manifest.name}},
        )
        return manifest

    def _load_tools(self, tools_dir: Path, source_name: str) -> None:
        log.debug(
            "plugins.skill_pack_loader._load_tools: entry",
            extra={"_fields": {"dir": str(tools_dir)}},
        )
        count = 0
        for py_file in tools_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            if spec is None or spec.loader is None:
                log.warning(
                    "plugins.skill_pack_loader._load_tools: could not load spec for %s", py_file
                )
                continue
            try:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
            except Exception as exc:
                log.error(
                    "plugins.skill_pack_loader._load_tools: module load failed",
                    exc_info=exc,
                    extra={"_fields": {"file": py_file.name}},
                )
                raise PluginValidationError(
                    source_name, f"failed to import {py_file.name}: {exc}"
                ) from exc
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool:
                    try:
                        instance = obj()
                        self._tool_registry.register(instance, source_name=source_name)
                        count += 1
                        log.debug(
                            "plugins.skill_pack_loader._load_tools: step — registered tool %s",
                            instance.name,
                        )
                    except Exception as exc:
                        log.error(
                            "plugins.skill_pack_loader._load_tools: tool registration failed",
                            exc_info=exc,
                        )
                        raise PluginValidationError(
                            source_name, f"tool {attr_name} registration failed: {exc}"
                        ) from exc
        log.debug(
            "plugins.skill_pack_loader._load_tools: exit",
            extra={"_fields": {"count": count}},
        )

    def _load_prompts(self, prompts_dir: Path, source_name: str) -> None:
        log.debug(
            "plugins.skill_pack_loader._load_prompts: entry",
            extra={"_fields": {"dir": str(prompts_dir)}},
        )
        count = 0
        for j2_file in prompts_dir.glob("*.j2"):
            _template_text = j2_file.read_text(encoding="utf-8")
            name = f"{source_name}.{j2_file.stem}"
            log.debug(
                "plugins.skill_pack_loader._load_prompts: step — loaded template %s", name
            )
            count += 1
        log.debug(
            "plugins.skill_pack_loader._load_prompts: exit",
            extra={"_fields": {"count": count}},
        )

    def _load_owls(self, owls_yaml: Path, source_name: str) -> None:
        log.debug(
            "plugins.skill_pack_loader._load_owls: entry",
            extra={"_fields": {"file": str(owls_yaml)}},
        )
        try:
            raw_list = yaml.safe_load(owls_yaml.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("plugins.skill_pack_loader._load_owls: yaml parse failed", exc_info=exc)
            raise PluginValidationError(source_name, f"invalid owls.yaml: {exc}") from exc

        if not isinstance(raw_list, list):
            raise PluginValidationError(source_name, "owls.yaml must be a list of owl manifests")

        from stackowl.owls.manifest import OwlAgentManifest

        count = 0
        for item in raw_list:
            try:
                manifest = OwlAgentManifest(**item)
                if self._owl_registry is not None:
                    self._owl_registry.register(manifest, source_name=source_name)
                count += 1
            except Exception as exc:
                log.error(
                    "plugins.skill_pack_loader._load_owls: owl registration failed", exc_info=exc
                )
                raise PluginValidationError(
                    source_name, f"owl registration failed: {exc}"
                ) from exc
        log.debug(
            "plugins.skill_pack_loader._load_owls: exit",
            extra={"_fields": {"count": count}},
        )
