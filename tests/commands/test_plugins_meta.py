"""/plugins sub-command metadata — declared subs match the dispatch ladder."""

from __future__ import annotations

import pytest

from stackowl.commands.plugins_command import PluginsCommand


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_plugins_declares_real_subcommands() -> None:
    cmd = PluginsCommand(plugin_registry=None)
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {"list", "info", "enable", "disable"}
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Plugins"
    for sub in cmd.meta.subcommands:
        assert sub.summary  # non-empty one-liner


def test_arg_bearing_subcommands_declare_name_arg() -> None:
    cmd = PluginsCommand(plugin_registry=None)
    by_name = {s.name: s for s in cmd.meta.subcommands}
    for sub in ("info", "enable", "disable"):
        assert by_name[sub].args, f"{sub} must declare an arg"
        assert by_name[sub].args[0].name == "name"
    # list takes no arg
    assert by_name["list"].args == ()


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    cmd = PluginsCommand(plugin_registry=_FakeRegistry())
    out = await cmd.handle("bogus", _state())
    assert "Usage: /plugins" in out
    assert "list" in out and "enable" in out


class _FakeRegistry:
    def list(self):  # type: ignore[no-untyped-def]
        return []

    def exists(self, name: str) -> bool:  # pragma: no cover — unused here
        return False

    async def set_enabled(self, name: str, *, enabled: bool) -> None:  # pragma: no cover
        return None
