"""D16.1 — a plugin's declared capabilities decide what it may register.

FOUND 2026-08-19, building the LifecycleHook gate the design specifies. The design
says an ungranted hook plugin "fails to load, loudly, through the existing
``PluginContext._require`` path that already raises ``PluginCapabilityDeniedError``"
(invariant I6). MEASURED: that path does not run. ``PluginContext`` has ZERO
construction sites anywhere in ``src/`` — only tests build one — and
``manifest.capabilities`` is parsed by the manifest model and read by NOTHING. So
every capability in ``capabilities.py`` was declared and enforced nowhere: a plugin
with ``capabilities: []`` still had its Tool registered into the live tool registry.

That is the shape this programme keeps finding — a write with no reader, an
actuator wired on no path at all — sitting underneath the permission model the
whole plugin surface claims to have. A gate added for hooks alone would have been
the same defect with one more exception: six extension points ungated and one
gated. So the gate is the general one, and this test is written against the TABLE
rather than against hooks.

WHY THIS IS SAFE TO ENFORCE NOW. Zero plugins are installed (the directory is
empty and the ``plugins`` table has 0 rows), so nothing can break by starting to
honour a field that has always been part of the manifest contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from stackowl.exceptions import PluginCapabilityDeniedError
from stackowl.plugins.capabilities import ALL_CAPABILITIES, CAPABILITY_FOR_EXTENSION_POINT
from stackowl.plugins.hooks import HookRegistry
from stackowl.plugins.local_loader import _ABC_NAMES, LocalPluginLoader


class _CollectingRegistry:
    def __init__(self) -> None:
        self.registered: list[object] = []

    def register(self, instance: object, source_name: str = "") -> None:
        self.registered.append(instance)


def _write_plugin(root: Path, name: str, capabilities: list[str], body: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir()
    caps_yaml = "[" + ", ".join(capabilities) + "]"
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(f"""
            name: {name}
            version: 1.0.0
            type: local_plugin
            entry_point: {name}_entry
            description: A plugin written by a test to prove the gate is real.
            capabilities: {caps_yaml}
        """).strip(),
        encoding="utf-8",
    )
    (plugin_dir / f"{name}_entry.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return plugin_dir


_HOOK_PLUGIN = """
    from stackowl.plugins.hooks import LifecycleHook

    class WatcherHook(LifecycleHook):
        async def pre_tool_call(self, event):
            return None
"""


class TestTheTableCoversEveryExtensionPoint:
    def test_no_extension_point_can_be_registered_without_a_capability(self) -> None:
        """The general invariant. An extension point missing from this table would
        be registrable by any plugin, whatever it declared — which is exactly the
        state the whole surface was in before this test existed."""
        missing = set(_ABC_NAMES) - set(CAPABILITY_FOR_EXTENSION_POINT)

        assert not missing, (
            f"extension points with no required capability: {sorted(missing)} — a "
            f"plugin could register one of these while declaring nothing"
        )

    def test_every_required_capability_is_a_real_one(self) -> None:
        """The other direction: a typo'd capability name would gate a point against
        a grant no operator can ever give."""
        unknown = set(CAPABILITY_FOR_EXTENSION_POINT.values()) - set(ALL_CAPABILITIES)

        assert not unknown, f"required capabilities that do not exist: {sorted(unknown)}"


class TestAnUngrantedPluginDoesNotRegister:
    def test_a_hook_without_the_grant_fails_to_load(self, tmp_path: Path) -> None:
        """I6 — enforced at LOAD, not at dispatch, so a denied plugin never reaches
        a call site."""
        hooks = HookRegistry()
        loader = LocalPluginLoader(hook_registry=hooks)
        plugin_dir = _write_plugin(tmp_path, "ungranted", [], _HOOK_PLUGIN)

        with pytest.raises(PluginCapabilityDeniedError) as excinfo:
            loader.load(plugin_dir)

        assert "lifecycle_hooks" in str(excinfo.value)
        assert not hooks.has("pre_tool_call"), "the hook registered despite being denied"

    def test_a_tool_without_the_grant_fails_to_load(self, tmp_path: Path) -> None:
        """The same gate, on the extension point that has existed all along."""
        tools = _CollectingRegistry()
        loader = LocalPluginLoader(tool_registry=tools)
        plugin_dir = _write_plugin(
            tmp_path,
            "ungrantedtool",
            [],
            """
                from stackowl.tools.base import Tool, ToolResult

                class ProbeTool(Tool):
                    @property
                    def name(self):
                        return "probe"

                    @property
                    def description(self):
                        return "A probe tool that exists only to be refused."

                    @property
                    def parameters(self):
                        return {"type": "object", "properties": {}}

                    async def execute(self, **kwargs):
                        return ToolResult(success=True, output="ok")
            """,
        )

        with pytest.raises(PluginCapabilityDeniedError):
            loader.load(plugin_dir)

        assert tools.registered == []


class TestAGrantedPluginRegisters:
    def test_a_granted_hook_is_armed(self, tmp_path: Path) -> None:
        hooks = HookRegistry()
        loader = LocalPluginLoader(hook_registry=hooks)
        plugin_dir = _write_plugin(tmp_path, "granted", ["lifecycle_hooks"], _HOOK_PLUGIN)

        manifest = loader.load(plugin_dir)

        assert manifest.name == "granted"
        assert hooks.has("pre_tool_call")
