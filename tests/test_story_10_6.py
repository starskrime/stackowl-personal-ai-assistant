"""Story 10.6 — Community Plugin CLI & /config Plugin Integration tests."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from stackowl.plugins.manifest import PluginManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a minimal plugins SQLite table for registry tests."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE plugins (
        name TEXT PRIMARY KEY, version TEXT NOT NULL, type TEXT NOT NULL,
        entry_point TEXT NOT NULL, capabilities TEXT NOT NULL DEFAULT '[]',
        config_schema TEXT, description TEXT NOT NULL DEFAULT '',
        author TEXT, license TEXT, installed_at REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        sha256 TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()
    conn.close()
    return path


def _make_manifest(name: str = "test-plugin") -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        type="local_plugin",
        entry_point="test_plugin.main:TestPlugin",
        description="A test plugin",
        capabilities=["tool"],
        config_schema=None,
        author="Test Author",
        license="MIT",
    )


def _text(out: object) -> str:
    """Unwrap a CommandResponse to its text, or pass through a plain str."""
    from stackowl.commands.response import CommandResponse

    return out.text if isinstance(out, CommandResponse) else out  # type: ignore[return-value]


def _make_state() -> object:
    """Return a minimal PipelineState-like object for command tests."""
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="trace-test",
        session_id="session-test",
        input_text="/plugins list",
        channel="cli",
        owl_name="default",
        pipeline_step="command",
    )


# ---------------------------------------------------------------------------
# Group 1: PluginIndex (4 tests)
# ---------------------------------------------------------------------------


class TestPluginIndex:
    def test_plugin_index_empty_on_missing_file(self, tmp_path: Path) -> None:
        """PluginIndex returns empty list when the index file does not exist."""
        from stackowl.plugins.index import PluginIndex

        index = PluginIndex(tmp_path / "nope.yaml")
        assert index.all() == []

    def test_plugin_index_lookup_by_name(self, tmp_path: Path) -> None:
        """PluginIndex.lookup returns a PluginIndexEntry for a known name."""
        from stackowl.plugins.index import PluginIndex, PluginIndexEntry

        index_file = tmp_path / "index.yaml"
        index_file.write_text(
            yaml.dump({
                "my-plugin": {
                    "url": "https://example.com/my-plugin.zip",
                    "version": "2.1.0",
                    "description": "My test plugin",
                    "type": "mcp_server",
                }
            }),
            encoding="utf-8",
        )
        index = PluginIndex(index_file)
        entry = index.lookup("my-plugin")
        assert entry is not None
        assert isinstance(entry, PluginIndexEntry)
        assert entry.name == "my-plugin"
        assert entry.version == "2.1.0"
        assert entry.url == "https://example.com/my-plugin.zip"
        assert entry.type == "mcp_server"

    def test_plugin_index_lookup_missing_returns_none(self, tmp_path: Path) -> None:
        """PluginIndex.lookup returns None for an unknown plugin name."""
        from stackowl.plugins.index import PluginIndex

        index_file = tmp_path / "index.yaml"
        index_file.write_text(
            yaml.dump({
                "known-plugin": {
                    "url": "https://example.com/known.zip",
                    "version": "1.0.0",
                    "description": "Known plugin",
                    "type": "local_plugin",
                }
            }),
            encoding="utf-8",
        )
        index = PluginIndex(index_file)
        result = index.lookup("unknown")
        assert result is None

    def test_plugin_index_all_returns_all_entries(self, tmp_path: Path) -> None:
        """PluginIndex.all returns all entries from a multi-entry YAML."""
        from stackowl.plugins.index import PluginIndex

        index_file = tmp_path / "index.yaml"
        data = {
            "plugin-a": {
                "url": "https://example.com/a.zip",
                "version": "1.0.0",
                "description": "Plugin A",
                "type": "local_plugin",
            },
            "plugin-b": {
                "url": "https://example.com/b.zip",
                "version": "0.9.0",
                "description": "Plugin B",
                "type": "skill_pack",
            },
            "plugin-c": {
                "url": "https://example.com/c.zip",
                "version": "3.0.0",
                "description": "Plugin C",
                "type": "mcp_server",
            },
        }
        index_file.write_text(yaml.dump(data), encoding="utf-8")
        index = PluginIndex(index_file)
        entries = index.all()
        assert len(entries) == 3
        names = {e.name for e in entries}
        assert names == {"plugin-a", "plugin-b", "plugin-c"}


# ---------------------------------------------------------------------------
# Group 2: PluginsCommand (5 tests)
# ---------------------------------------------------------------------------


class TestPluginsCommand:
    def test_plugins_command_list_empty(self) -> None:
        """PluginsCommand list subcommand returns 'No plugins installed' when registry is empty."""
        from stackowl.commands.plugins_command import PluginsCommand

        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        cmd = PluginsCommand(mock_registry)

        result = asyncio.run(cmd.handle("list", _make_state()))
        assert "No plugins installed" in _text(result)

    def test_plugins_command_list_with_plugins(self) -> None:
        """PluginsCommand list subcommand includes installed plugin names."""
        from stackowl.commands.plugins_command import PluginsCommand

        mock_registry = MagicMock()
        mock_registry.list.return_value = [
            _make_manifest("plugin-alpha"),
            _make_manifest("plugin-beta"),
        ]
        cmd = PluginsCommand(mock_registry)

        result = asyncio.run(cmd.handle("list", _make_state()))
        assert "plugin-alpha" in _text(result)
        assert "plugin-beta" in _text(result)

    def test_plugins_command_info_found(self) -> None:
        """PluginsCommand info subcommand returns plugin details when found."""
        from stackowl.commands.plugins_command import PluginsCommand

        manifest = _make_manifest("my-plugin")
        mock_registry = MagicMock()
        mock_registry.list.return_value = [manifest]
        cmd = PluginsCommand(mock_registry)

        result = asyncio.run(cmd.handle("info my-plugin", _make_state()))
        assert "my-plugin" in result
        assert "1.0.0" in result
        assert "local_plugin" in result

    def test_plugins_command_info_not_found(self) -> None:
        """PluginsCommand info subcommand returns 'not found' when plugin absent."""
        from stackowl.commands.plugins_command import PluginsCommand

        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        cmd = PluginsCommand(mock_registry)

        result = asyncio.run(cmd.handle("info nonexistent", _make_state()))
        assert "not found" in result.lower()

    def test_plugins_command_unknown_subcommand(self) -> None:
        """PluginsCommand returns usage text for unrecognised subcommands."""
        from stackowl.commands.plugins_command import PluginsCommand

        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        cmd = PluginsCommand(mock_registry)

        result = asyncio.run(cmd.handle("unknown", _make_state()))
        assert "Usage" in result or "usage" in result.lower()


# ---------------------------------------------------------------------------
# Group 3: PluginRegistry.set_enabled (3 tests)
# ---------------------------------------------------------------------------


class TestPluginRegistrySetEnabled:
    def test_plugin_registry_set_enabled_false(self, db_path: Path) -> None:
        """set_enabled(False) stores 0 in the enabled column for the given plugin."""
        from stackowl.plugins.registry import PluginRegistry

        registry = PluginRegistry(db_path)
        manifest = _make_manifest("toggle-plugin")
        asyncio.run(registry.install(manifest))

        asyncio.run(registry.set_enabled("toggle-plugin", enabled=False))

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT enabled FROM plugins WHERE name = ?", ("toggle-plugin",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 0

    def test_plugin_registry_set_enabled_true(self, db_path: Path) -> None:
        """set_enabled(True) stores 1 in the enabled column for the given plugin."""
        from stackowl.plugins.registry import PluginRegistry

        registry = PluginRegistry(db_path)
        manifest = _make_manifest("re-enable-plugin")
        asyncio.run(registry.install(manifest))

        # First disable, then re-enable
        asyncio.run(registry.set_enabled("re-enable-plugin", enabled=False))
        asyncio.run(registry.set_enabled("re-enable-plugin", enabled=True))

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT enabled FROM plugins WHERE name = ?", ("re-enable-plugin",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 1

    def test_plugin_registry_set_enabled_missing_is_noop(self, db_path: Path) -> None:
        """set_enabled on a non-existent plugin name raises no exception."""
        from stackowl.plugins.registry import PluginRegistry

        registry = PluginRegistry(db_path)
        # Should not raise
        asyncio.run(registry.set_enabled("does-not-exist", enabled=False))
