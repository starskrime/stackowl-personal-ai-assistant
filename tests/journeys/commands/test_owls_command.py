"""Dispatch tests — /owls add/edit surface no-DB note when _db is None."""
from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

_NO_DB_NOTE = "(DNA not persisted — no DB)"


class _MinimalOwlRegistry:
    """Minimal registry that accepts add/edit without a real DB."""

    def __init__(self) -> None:
        self._owls: dict[str, object] = {}

    def register(self, manifest: object) -> None:
        self._owls[manifest.name] = manifest  # type: ignore[union-attr]

    def replace(self, manifest: object) -> None:
        self._owls[manifest.name] = manifest  # type: ignore[union-attr]

    def get(self, name: str) -> object:
        from stackowl.exceptions import OwlNotFoundError
        if name not in self._owls:
            raise OwlNotFoundError(name)
        return self._owls[name]

    def list(self) -> list:
        return list(self._owls.values())

    async def health_check(self) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(status="healthy", message=None)

    def deregister(self, name: str) -> None:
        self._owls.pop(name, None)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_owls_add_no_db_shows_note(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """When db=None, /owls add returns success with the no-DB note."""
    monkeypatch.setattr(
        "stackowl.commands.owls_command.config_path",
        lambda: __import__("pathlib").Path(str(tmp_path)) / "stackowl.yaml",
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.save_yaml",
        lambda path, data: None,
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.load_yaml",
        lambda path: {},
    )
    registry = _MinimalOwlRegistry()
    deps = CommandDeps(owl_registry=registry, db=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "owls", "add testowl --role assistant --tier standard", make_state()
    )
    assert "✓" in result
    assert _NO_DB_NOTE in result


async def test_owls_add_with_db_no_note(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """When db is present, /owls add returns success without the no-DB note."""
    monkeypatch.setattr(
        "stackowl.commands.owls_command.config_path",
        lambda: __import__("pathlib").Path(str(tmp_path)) / "stackowl.yaml",
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.save_yaml",
        lambda path, data: None,
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.load_yaml",
        lambda path: {},
    )

    class _FakeDb:
        async def fetch_all(self, sql: str, params: tuple) -> list:
            return []

        async def execute(self, sql: str, params: tuple) -> None:
            pass

    monkeypatch.setattr(
        "stackowl.owls.dna_authored.capture_one_authored",
        lambda db, name, dna: __import__("asyncio").sleep(0),
    )
    registry = _MinimalOwlRegistry()
    deps = CommandDeps(owl_registry=registry, db=_FakeDb())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "owls", "add testowl2 --role assistant --tier standard", make_state()
    )
    assert "✓" in result
    assert _NO_DB_NOTE not in result


async def test_owls_edit_no_db_shows_note(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """When db=None, /owls edit returns success with the no-DB note."""
    monkeypatch.setattr(
        "stackowl.commands.owls_command.config_path",
        lambda: __import__("pathlib").Path(str(tmp_path)) / "stackowl.yaml",
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.save_yaml",
        lambda path, data: None,
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.load_yaml",
        lambda path: {},
    )
    # Pre-populate registry with an owl to edit
    from stackowl.owls.manifest import OwlAgentManifest
    manifest = OwlAgentManifest(name="editowl", role="assistant", model_tier="standard", system_prompt="test")
    registry = _MinimalOwlRegistry()
    registry.register(manifest)
    deps = CommandDeps(owl_registry=registry, db=None)
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "owls", "edit editowl --tier powerful", make_state()
    )
    assert "✓" in result
    assert _NO_DB_NOTE in result


async def test_owls_edit_with_db_no_note(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """When db is present, /owls edit returns success without the no-DB note."""
    monkeypatch.setattr(
        "stackowl.commands.owls_command.config_path",
        lambda: __import__("pathlib").Path(str(tmp_path)) / "stackowl.yaml",
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.save_yaml",
        lambda path, data: None,
    )
    monkeypatch.setattr(
        "stackowl.commands.owls_command.load_yaml",
        lambda path: {},
    )
    from stackowl.owls.manifest import OwlAgentManifest

    class _FakeDb:
        async def fetch_all(self, sql: str, params: tuple) -> list:
            return []

        async def execute(self, sql: str, params: tuple) -> None:
            pass

    manifest = OwlAgentManifest(name="editowl2", role="assistant", model_tier="standard", system_prompt="test")
    registry = _MinimalOwlRegistry()
    registry.register(manifest)
    deps = CommandDeps(owl_registry=registry, db=_FakeDb())
    register_all_commands(deps, registry=CommandRegistry.instance())
    result = await CommandRegistry.instance().dispatch(
        "owls", "edit editowl2 --tier powerful", make_state()
    )
    assert "✓" in result
    assert _NO_DB_NOTE not in result
