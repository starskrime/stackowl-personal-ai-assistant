"""Dispatch test — /permissions is wired through CommandRegistry."""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _FakeIntegrationRegistry:
    def list_all(self) -> list:
        return []


class _FakePluginRegistry:
    def list(self) -> list:
        return []


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_permissions_header_present() -> None:
    """Wired /permissions returns the header line."""
    deps = CommandDeps(
        integration_registry=_FakeIntegrationRegistry(),
        plugin_registry=_FakePluginRegistry(),
    )
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("permissions", "", make_state())
    assert "=== Permissions ===" in result


async def test_permissions_degrades_with_none_deps() -> None:
    """All-None deps still registers and returns the header (no crash)."""
    deps = CommandDeps()
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch("permissions", "", make_state())
    assert "=== Permissions ===" in result


async def test_permissions_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("permissions", "", make_state())
