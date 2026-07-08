"""/plugins sub-command metadata — declared subs match the dispatch ladder."""

from __future__ import annotations

import pytest

from stackowl.commands.plugins_command import PluginsCommand
from stackowl.commands.response import CommandResponse
from stackowl.plugins.manifest import PluginManifest


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


@pytest.mark.asyncio
async def test_list_returns_command_response_with_row_actions() -> None:
    cmd = PluginsCommand(plugin_registry=_FakeRegistry(with_plugin=True))
    out = await cmd.handle("list", _state())
    assert isinstance(out, CommandResponse)
    assert "demo-plugin" in out.text
    assert any(
        a.label == "demo-plugin" and a.command == "/plugins menu demo-plugin"
        for a in out.actions
    )


@pytest.mark.asyncio
async def test_menu_shows_disable_and_info_actions() -> None:
    cmd = PluginsCommand(plugin_registry=_FakeRegistry(with_plugin=True))
    out = await cmd.handle("menu demo-plugin", _state())
    assert isinstance(out, CommandResponse)
    assert "demo-plugin" in out.text
    labels = {a.label: a.command for a in out.actions}
    assert labels["Disable"] == "/plugins disable demo-plugin"
    assert labels["Info"] == "/plugins info demo-plugin"
    assert all(not a.destructive for a in out.actions)


@pytest.mark.asyncio
async def test_menu_unknown_plugin_returns_not_found() -> None:
    cmd = PluginsCommand(plugin_registry=_FakeRegistry())
    out = await cmd.handle("menu nope", _state())
    assert isinstance(out, str)
    assert "not found" in out.lower()


class _FakeRegistry:
    def __init__(self, with_plugin: bool = False) -> None:
        self._plugins = (
            [
                PluginManifest(
                    name="demo-plugin",
                    version="1.0.0",
                    type="local_plugin",
                    entry_point="demo.main:Demo",
                    description="A demo plugin",
                )
            ]
            if with_plugin
            else []
        )

    def list(self):  # type: ignore[no-untyped-def]
        return self._plugins

    def exists(self, name: str) -> bool:  # pragma: no cover — unused here
        return False

    async def set_enabled(self, name: str, *, enabled: bool) -> None:  # pragma: no cover
        return None
