"""D16.1 invariants I1 and I2 — the two the design asserted and nothing tested.

DOC_STANDARD.md: "Every invariant here should map to a test. If it cannot be tested,
it is a wish, not an invariant." I3, I4, I5 and I6 each had one from the start; these
two were left as prose, and they are the two that carry the platform's LAWS.

  I1 — a hook never changes what the model sees. LAW 1: the presented tool array is
       frozen per incarnation, and a hook that could add or remove a tool
       mid-conversation would invalidate the prompt cache on every turn.
  I2 — a hook costs zero tokens. LAW 2: capability grows at the EDGES, never at the
       narrow waist. A hook is not a model tool and never enters the tool array.

Both are testable at the seam that decides them — plugin LOAD — because that is the
only moment a hook could reach the tool registry, and the loader walks every class a
plugin defines against every extension point in ``_ABC_NAMES``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from stackowl.plugins.hooks import HookRegistry
from stackowl.plugins.local_loader import LocalPluginLoader
from stackowl.tools.base import Tool
from stackowl.tools.registry import ToolRegistry


def _install_hook_plugin(root: Path, name: str = "watcher") -> Path:
    """A plugin whose ONLY class is a LifecycleHook, granted the capability."""
    plugin_dir = root / name
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        textwrap.dedent(f"""
            name: {name}
            version: 1.0.0
            type: local_plugin
            entry_point: {name}_entry
            description: An observer that must never appear in the model's tool array.
            capabilities: [lifecycle_hooks]
        """).strip(),
        encoding="utf-8",
    )
    (plugin_dir / f"{name}_entry.py").write_text(
        textwrap.dedent("""
            from stackowl.plugins.hooks import LifecycleHook

            class WatcherHook(LifecycleHook):
                async def pre_tool_call(self, event):
                    return None

                async def post_llm_call(self, event):
                    return None
        """),
        encoding="utf-8",
    )
    return plugin_dir


class _ProbeTool(Tool):
    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "A tool that exists so the presented array is not empty."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> object:  # pragma: no cover — never run
        raise AssertionError("this tool exists to be listed, not called")


class TestI1TheModelSeesExactlyWhatItSawBefore:
    def test_loading_a_hook_plugin_leaves_the_tool_array_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """LAW 1 is the reason this matters rather than tidiness: the tool array is
        frozen per incarnation, so anything that changed it mid-conversation would
        invalidate the cached prefix on every turn of every long conversation."""
        tools = ToolRegistry()
        tools.register(_ProbeTool())
        before = [(t.name, t.description, t.parameters) for t in tools.all()]

        LocalPluginLoader(
            tool_registry=tools, hook_registry=HookRegistry(),
        ).load(_install_hook_plugin(tmp_path))

        after = [(t.name, t.description, t.parameters) for t in tools.all()]
        assert after == before, (
            "a hook plugin changed the presented tool array — Law 1 says the array is "
            "frozen per incarnation, so this would re-cost the prompt cache every turn"
        )


class TestI2AHookCostsZeroTokens:
    def test_a_hook_is_not_a_model_tool(self) -> None:
        """LAW 2 — capability at the edges, never at the waist. Stated as a type fact
        because that is what makes it true by construction: the tool array is built
        from Tool instances, and a hook can never be one."""
        from stackowl.plugins.hooks import LifecycleHook

        assert not issubclass(LifecycleHook, Tool)

    def test_a_hook_plugin_registers_nothing_into_the_tool_registry(
        self, tmp_path: Path
    ) -> None:
        """The empty-registry case, which the byte-identical test above cannot see: a
        hook arriving as the FIRST thing a deployment installs must still add nothing
        to the waist."""
        tools = ToolRegistry()
        hooks = HookRegistry()

        LocalPluginLoader(
            tool_registry=tools, hook_registry=hooks,
        ).load(_install_hook_plugin(tmp_path))

        assert tools.all() == [], "a hook reached the model's tool array"
        assert hooks.has("pre_tool_call"), "...and it did not even arm as a hook"
