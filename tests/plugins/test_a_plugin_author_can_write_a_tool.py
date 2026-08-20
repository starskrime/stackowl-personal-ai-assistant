"""D16.1 — the friction found by being the FIRST user of the plugin surface.

Zero plugins are installed on this box, so on 2026-08-16 a throwaway plugin was
driven through the real loader end to end. It worked — and it was needlessly hard
to write. Two of the three things it hit are fixed here; the third
(``ToolResult.duration_ms`` being required with no default) shipped in b6f5e9c5.

1. ``Tool`` requires ``name``, ``description`` and ``parameters`` as three separate
   abstract properties, while ``ToolManifest`` exists and every built-in tool has
   one. Implementing ``manifest`` alone is the obvious guess and fails with
   "Can't instantiate abstract class ... without an implementation for abstract
   methods 'description', 'name', 'parameters'". It is the first thing an author
   writes and the first thing that breaks.

2. ``_register_classes`` calls ``obj()`` with NO arguments, so an extension-point
   class needing constructor arguments cannot be a plugin at all — and the author
   sees a raw ``TypeError`` about a missing positional argument, wrapped in a
   registration failure, with nothing saying that no-argument construction is the
   contract.

WHAT IS NOT FIXED, AND WHY IT IS NOT A GAP. Nothing PASSES configuration to a
plugin's constructor, because there is nothing to pass: ``PluginManifest`` carries
``config_schema`` (a shape) and no config VALUES, and no store holds any. Inventing
a config channel for a population of zero plugins is the over-architecting this
item already ruled out once. So the contract is stated plainly instead of being
discovered through a TypeError.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from stackowl.exceptions import PluginValidationError
from stackowl.plugins.local_loader import LocalPluginLoader
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class _ManifestOnlyTool(Tool):
    """The obvious guess: declare the manifest and nothing else."""

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="manifest_only",
            description="A tool that declares itself once, through its manifest.",
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok")


class TestOneDeclarationIsEnough:
    def test_a_manifest_only_tool_can_be_instantiated(self) -> None:
        tool = _ManifestOnlyTool()

        assert tool.name == "manifest_only"
        assert tool.description.startswith("A tool that declares itself")
        assert tool.parameters == {"type": "object", "properties": {}}

    def test_a_tool_that_declares_NEITHER_still_fails_loudly(self) -> None:
        """The safety property is kept. A class that defines no manifest and no
        fields is still abstract, and still fails at instantiation naming exactly
        what is missing — early and by name, rather than as a recursion or an
        attribute error at first use."""

        class _Nothing(Tool):
            async def execute(self, **kwargs: object) -> ToolResult:
                return ToolResult(success=True, output="")

        with pytest.raises(TypeError, match="name"):
            _Nothing()  # type: ignore[abstract]

    def test_a_subclass_of_a_concrete_tool_keeps_its_parent_fields(self) -> None:
        """The derivation fills ABSTRACT slots only. A tool that overrides its
        manifest to change one thing must not have its inherited name silently
        replaced by a derived one — that would rewrite behaviour to fix ergonomics.
        """

        class _Child(_ManifestOnlyTool):
            @property
            def manifest(self) -> ToolManifest:
                return ToolManifest(
                    name="child",
                    description="A child that renames itself through the manifest.",
                    parameters={"type": "object", "properties": {}},
                )

        assert _Child().name == "child"

        class _Explicit(Tool):
            @property
            def name(self) -> str:
                return "explicit"

            @property
            def description(self) -> str:
                return "A tool that spells out its own name and description."

            @property
            def parameters(self) -> dict[str, object]:
                return {"type": "object", "properties": {}}

            @property
            def manifest(self) -> ToolManifest:
                return ToolManifest(
                    name="manifest-says-otherwise",
                    description=self.description,
                    parameters=self.parameters,
                )

            async def execute(self, **kwargs: object) -> ToolResult:
                return ToolResult(success=True, output="")

        assert _Explicit().name == "explicit", "an explicit property must always win"


class TestTheNoArgumentContractIsStated:
    def test_a_class_needing_constructor_arguments_says_so(self, tmp_path: Path) -> None:
        """Not a TypeError about a missing positional argument. The loader
        constructs extension points with no arguments and that is the contract —
        an author should be told it, not made to infer it."""
        plugin_dir = tmp_path / "needsconfig"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text(
            textwrap.dedent("""
                name: needsconfig
                version: 1.0.0
                type: local_plugin
                entry_point: needsconfig_entry
                description: A plugin whose tool wants constructor arguments.
                capabilities: [tool_registry]
            """).strip(),
            encoding="utf-8",
        )
        (plugin_dir / "needsconfig_entry.py").write_text(
            textwrap.dedent("""
                from stackowl.tools.base import Tool, ToolResult

                class ConfiguredTool(Tool):
                    def __init__(self, endpoint):
                        self._endpoint = endpoint

                    @property
                    def name(self):
                        return "configured"

                    @property
                    def description(self):
                        return "A tool that cannot be built without an endpoint."

                    @property
                    def parameters(self):
                        return {"type": "object", "properties": {}}

                    async def execute(self, **kwargs):
                        return ToolResult(success=True, output="")
            """),
            encoding="utf-8",
        )

        class _Registry:
            def register(self, instance: object, source_name: str = "") -> None:
                raise AssertionError("nothing should have registered")

        with pytest.raises(PluginValidationError) as excinfo:
            LocalPluginLoader(tool_registry=_Registry()).load(plugin_dir)

        reason = str(excinfo.value.reason)
        assert "no arguments" in reason
        assert "ConfiguredTool" in reason
