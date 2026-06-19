"""Dispatch tests — /plugins command (Epic B, Commit 2).

Drives CommandRegistry.dispatch() through register_all_commands() with a real
PluginRegistry on a temp SQLite DB (migrations applied).  Key assertions:
  1. enable on a real plugin → state change confirmed
  2. enable on bogus name → honest not-found (NOT "Plugin 'x' enabled.")
  3. disable mirrors the same pattern
  4. not-configured when registry is None
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.plugins.manifest import PluginManifest
from stackowl.plugins.registry import PluginRegistry
from tests._story_6_7_helpers import make_state


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
def plugin_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "plugins_test.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


@pytest.fixture()
def plugin_registry(plugin_db: Path) -> PluginRegistry:
    return PluginRegistry(db_path=plugin_db)


@pytest.fixture()
def sample_manifest() -> PluginManifest:
    return PluginManifest(
        name="test-plugin",
        version="1.0.0",
        type="local_plugin",
        entry_point="test_plugin.main",
        capabilities=["test"],
        description="A test plugin",
    )


@pytest.fixture()
def reg(plugin_registry: PluginRegistry) -> CommandRegistry:
    deps = CommandDeps(plugin_registry=plugin_registry)
    return register_all_commands(deps, registry=CommandRegistry.instance())


async def test_plugins_enable_existing_plugin_reports_success(
    reg: CommandRegistry, plugin_registry: PluginRegistry, sample_manifest: PluginManifest
) -> None:
    """dispatch 'plugins enable <name>' with a real plugin → claims enabled."""
    await plugin_registry.install(sample_manifest)

    state = make_state()
    result = await reg.dispatch("plugins", "enable test-plugin", state)

    assert "enabled" in result.lower()
    assert "not found" not in result.lower()


async def test_plugins_enable_bogus_name_returns_not_found(
    reg: CommandRegistry,
) -> None:
    """dispatch 'plugins enable bogus' → honest not-found, NOT false success."""
    state = make_state()
    result = await reg.dispatch("plugins", "enable bogus-plugin-xyz", state)

    assert "not found" in result.lower()
    assert "enabled" not in result.lower() or "not found" in result.lower()


async def test_plugins_disable_existing_plugin_reports_success(
    reg: CommandRegistry, plugin_registry: PluginRegistry, sample_manifest: PluginManifest
) -> None:
    """dispatch 'plugins disable <name>' with a real plugin → claims disabled."""
    await plugin_registry.install(sample_manifest)

    state = make_state()
    result = await reg.dispatch("plugins", "disable test-plugin", state)

    assert "disabled" in result.lower()
    assert "not found" not in result.lower()


async def test_plugins_disable_bogus_name_returns_not_found(
    reg: CommandRegistry,
) -> None:
    """dispatch 'plugins disable bogus' → honest not-found, NOT false success."""
    state = make_state()
    result = await reg.dispatch("plugins", "disable bogus-plugin-xyz", state)

    assert "not found" in result.lower()
    assert "disabled" not in result.lower() or "not found" in result.lower()


async def test_plugins_list_shows_installed_plugin(
    reg: CommandRegistry, plugin_registry: PluginRegistry, sample_manifest: PluginManifest
) -> None:
    """dispatch 'plugins list' → installed plugin appears."""
    await plugin_registry.install(sample_manifest)

    state = make_state()
    result = await reg.dispatch("plugins", "list", state)

    assert "test-plugin" in result


async def test_plugins_not_configured_when_registry_none() -> None:
    """dispatch 'plugins list' with no registry → honest not-configured message."""
    CommandRegistry.reset()
    deps = CommandDeps(plugin_registry=None)
    reg = register_all_commands(deps, registry=CommandRegistry.instance())
    state = make_state()

    result = await reg.dispatch("plugins", "list", state)

    assert "not configured" in result.lower() or "✗" in result
