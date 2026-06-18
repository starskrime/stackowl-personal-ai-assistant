"""Story 10.5 — Plugin loading infrastructure tests."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from pathlib import Path
from typing import Any

import pytest

from collections.abc import AsyncIterator

from stackowl.channels.base import ChannelAdapter
from stackowl.gateway.scanner import IngressMessage
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.registry import CommandRegistry
from stackowl.exceptions import PluginCapabilityDeniedError, PluginValidationError
from stackowl.plugins import capabilities as caps
from stackowl.plugins.capabilities import ALL_CAPABILITIES
from stackowl.plugins.context import PluginContext
from stackowl.plugins.local_loader import LocalPluginLoader
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _FakeTool(Tool):
    @property
    def name(self) -> str:
        return "fake-tool"

    @property
    def description(self) -> str:
        return "A fake tool for testing"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)


class _FakeTool2(Tool):
    @property
    def name(self) -> str:
        return "fake-tool-2"

    @property
    def description(self) -> str:
        return "Another fake tool"

    @property
    def parameters(self) -> dict[str, object]:
        return {}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok2", duration_ms=0.0)


class _FakeHandler(JobHandler):
    @property
    def handler_name(self) -> str:
        return "fake-handler"

    async def execute(self, job: Job) -> JobResult:
        return JobResult(success=True, output="done")


class _FakeAdapter(ChannelAdapter):
    @property
    def channel_name(self) -> str:
        return "fake-channel"

    async def receive(self) -> IngressMessage:
        raise NotImplementedError

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        pass

    async def send_text(self, text: str) -> None:
        pass


# NOTE: SkillPackLoader has been removed in Learning Commit 3 sub-phase 3a.
# Its responsibilities are now owned by stackowl.skills.SkillLoader (uses the
# Anthropic-standard SKILL.md format instead of skill.yaml). Equivalent test
# coverage lives under tests/skills/test_skill_loader.py.

_VALID_PLUGIN_YAML: dict[str, object] = {
    "name": "my-plugin",
    "version": "1.0.0",
    "type": "local_plugin",
    "entry_point": "my_plugin_mod",
    "description": "A test local plugin",
}


# ===========================================================================
# Group 1: Registry source tracking
# ===========================================================================


class TestToolRegistryUnregisterBySource:
    def test_removes_tools_and_returns_count(self) -> None:
        """Register 2 tools under the same source; unregister_by_source returns 2."""
        reg = ToolRegistry()
        reg.register(_FakeTool(), source_name="myplugin")
        reg.register(_FakeTool2(), source_name="myplugin")

        result = reg.unregister_by_source("myplugin")

        assert result == 2
        assert reg.get("fake-tool") is None
        assert reg.get("fake-tool-2") is None

    def test_unknown_source_returns_zero(self) -> None:
        """unregister_by_source on an unknown source returns 0 without error."""
        reg = ToolRegistry()
        result = reg.unregister_by_source("unknown")
        assert result == 0


class TestCommandRegistryUnregisterBySource:
    def test_removes_command_and_returns_count(self) -> None:
        """Register a command with source_name; unregister_by_source removes it."""
        from stackowl.commands.base import SlashCommand
        from stackowl.pipeline.state import PipelineState

        class _FakeCmd(SlashCommand):
            @property
            def command(self) -> str:
                return "testcmd"

            @property
            def description(self) -> str:
                return "test"

            async def handle(self, args: str, state: PipelineState) -> str:
                return "ok"

        reg = CommandRegistry()
        cmd = _FakeCmd()
        reg.register(cmd, source_name="test-plugin")

        assert reg.list()  # command is present

        result = reg.unregister_by_source("test-plugin")
        assert result == 1
        assert all(c.command != "testcmd" for c in reg.list())


class TestHandlerRegistryUnregisterBySource:
    def test_removes_handler_and_returns_count(self) -> None:
        """Register handler with source_name; unregister_by_source removes it."""
        reg = HandlerRegistry()
        handler = _FakeHandler()
        reg.register(handler, source_name="test-pack")

        assert reg.get("fake-handler") is not None

        result = reg.unregister_by_source("test-pack")
        assert result == 1
        assert reg.get("fake-handler") is None


class TestChannelRegistryUnregisterBySource:
    def test_removes_adapter_and_returns_count(self) -> None:
        """Register adapter with source_name; unregister_by_source removes it."""
        reg = ChannelRegistry()
        adapter = _FakeAdapter()
        reg.register(adapter, source_name="chan-plugin")

        assert reg.get("fake-channel") is not None

        result = reg.unregister_by_source("chan-plugin")
        assert result == 1
        with pytest.raises(Exception):  # ChannelNotFoundError
            reg.get("fake-channel")


# ===========================================================================
# Group 2: PluginContext
# ===========================================================================


class TestPluginContextGrantsToolRegistry:
    def test_granted_capability_returns_registry(self) -> None:
        """PluginContext with tool_registry granted returns the registry on access."""
        tool_reg = ToolRegistry()
        ctx = PluginContext(
            plugin_name="myplugin",
            granted=[caps.TOOL_REGISTRY],
            tool_registry=tool_reg,
        )
        assert ctx.tool_registry is tool_reg


class TestPluginContextDeniesDeniedCapability:
    def test_ungranted_raises_error(self) -> None:
        """PluginContext without tool_registry grant raises PluginCapabilityDeniedError."""
        tool_reg = ToolRegistry()
        ctx = PluginContext(
            plugin_name="myplugin",
            granted=[],
            tool_registry=tool_reg,
        )
        with pytest.raises(PluginCapabilityDeniedError) as exc_info:
            _ = ctx.tool_registry
        assert exc_info.value.capability == caps.TOOL_REGISTRY


class TestAllCapabilitiesConstant:
    def test_expected_strings_present(self) -> None:
        """ALL_CAPABILITIES contains all expected capability names."""
        expected = {
            "tool_registry",
            "command_registry",
            "handler_registry",
            "channel_registry",
            "owl_registry",
            "memory_bridge",
            "event_bus",
            "audit_logger",
            "browser_runtime",
        }
        assert ALL_CAPABILITIES == expected


class TestPluginCapabilityDeniedErrorMessage:
    def test_capability_attribute(self) -> None:
        """PluginCapabilityDeniedError stores the capability name."""
        err = PluginCapabilityDeniedError("foo")
        assert err.capability == "foo"
        assert "foo" in str(err)


class TestPluginContextMultipleCapabilities:
    def test_two_granted_third_denied(self) -> None:
        """Grant 2 capabilities; both accessible, third raises error."""
        tool_reg = ToolRegistry()
        cmd_reg = CommandRegistry()
        ctx = PluginContext(
            plugin_name="myplugin",
            granted=[caps.TOOL_REGISTRY, caps.COMMAND_REGISTRY],
            tool_registry=tool_reg,
            command_registry=cmd_reg,
        )
        assert ctx.tool_registry is tool_reg
        assert ctx.command_registry is cmd_reg
        with pytest.raises(PluginCapabilityDeniedError) as exc_info:
            _ = ctx.owl_registry
        assert exc_info.value.capability == caps.OWL_REGISTRY


# ===========================================================================
# Group 3: LocalPluginLoader (SkillPackLoader removed — see new tests/skills/)
# ===========================================================================


class TestLocalLoaderMissingPluginYaml:
    def test_raises_plugin_validation_error(self, tmp_path: Path) -> None:
        """LocalPluginLoader.load() without plugin.yaml raises PluginValidationError."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        loader = LocalPluginLoader()

        with pytest.raises(PluginValidationError) as exc_info:
            loader.load(plugin_dir)
        assert "missing plugin.yaml" in exc_info.value.reason
