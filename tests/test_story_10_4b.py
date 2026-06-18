"""Story 10.4b — PluginManifest and PluginRegistry tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from stackowl.plugins.manifest import PluginManifest
from stackowl.plugins.registry import PluginRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_MANIFEST_DATA: dict[str, object] = {
    "name": "my-plugin",
    "version": "1.2.3",
    "type": "local_plugin",
    "entry_point": "my_plugin:main",
    "description": "A test plugin",
}


def _make_manifest(**overrides: object) -> PluginManifest:
    data = {**_VALID_MANIFEST_DATA, **overrides}
    return PluginManifest(**data)  # type: ignore[arg-type]


@pytest.fixture()
def plugins_db(tmp_path: Path) -> Path:
    """Temp SQLite db with the plugins table pre-created."""
    p = tmp_path / "plugins_test.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plugins (
            name         TEXT PRIMARY KEY,
            version      TEXT NOT NULL,
            type         TEXT NOT NULL,
            entry_point  TEXT NOT NULL,
            capabilities TEXT NOT NULL DEFAULT '[]',
            config_schema TEXT,
            description  TEXT NOT NULL DEFAULT '',
            author       TEXT,
            license      TEXT,
            installed_at REAL NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            sha256       TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def registry(plugins_db: Path) -> PluginRegistry:
    return PluginRegistry(plugins_db)


# ---------------------------------------------------------------------------
# PluginManifest tests
# ---------------------------------------------------------------------------


def test_plugin_manifest_valid() -> None:
    """A fully-valid PluginManifest can be constructed without error."""
    m = _make_manifest()
    assert m.name == "my-plugin"
    assert m.version == "1.2.3"
    assert m.type == "local_plugin"


def test_plugin_manifest_name_pattern_rejected() -> None:
    """Names with uppercase letters are rejected by the name pattern."""
    with pytest.raises(ValidationError):
        _make_manifest(name="My-Plugin")


def test_plugin_manifest_version_semver_required() -> None:
    """Versions without a patch segment (e.g. '1.0') are rejected."""
    with pytest.raises(ValidationError):
        _make_manifest(version="1.0")


def test_plugin_manifest_extra_fields_forbidden() -> None:
    """Extra fields are forbidden by ConfigDict(extra='forbid')."""
    with pytest.raises(ValidationError):
        PluginManifest(
            name="my-plugin",
            version="1.0.0",
            type="local_plugin",
            entry_point="entry:main",
            description="ok",
            unknown_field="oops",  # type: ignore[call-arg]
        )


def test_plugin_manifest_is_frozen() -> None:
    """Setting an attribute on a frozen PluginManifest raises TypeError."""
    m = _make_manifest()
    with pytest.raises((TypeError, ValidationError)):
        m.name = "new-name"  # type: ignore[misc]


def test_plugin_manifest_type_literal() -> None:
    """An invalid type literal raises ValidationError."""
    with pytest.raises(ValidationError):
        _make_manifest(type="invalid_type")


# ---------------------------------------------------------------------------
# PluginRegistry tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_registry_install_and_list(registry: PluginRegistry) -> None:
    """install() persists a plugin that list() then returns."""
    m = _make_manifest()
    await registry.install(m)
    installed = registry.list()
    assert len(installed) == 1
    assert installed[0].name == "my-plugin"
    assert installed[0].version == "1.2.3"


@pytest.mark.asyncio
async def test_plugin_registry_install_duplicate_is_idempotent(registry: PluginRegistry) -> None:
    """Installing the same plugin name twice results in one entry (INSERT OR REPLACE)."""
    m1 = _make_manifest(version="1.0.0")
    m2 = _make_manifest(version="1.0.1")
    await registry.install(m1)
    await registry.install(m2)
    installed = registry.list()
    assert len(installed) == 1
    assert installed[0].version == "1.0.1"


@pytest.mark.asyncio
async def test_plugin_registry_uninstall_removes_entry(registry: PluginRegistry) -> None:
    """uninstall() removes the plugin so list() returns empty."""
    m = _make_manifest()
    await registry.install(m)
    await registry.uninstall("my-plugin")
    installed = registry.list()
    assert installed == []


def test_plugin_registry_list_empty(registry: PluginRegistry) -> None:
    """list() on a fresh db returns an empty list."""
    assert registry.list() == []
