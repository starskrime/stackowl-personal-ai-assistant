"""Tests for commands/assembly.py — register_all_commands behavior.

Asserts that calling register_all_commands on a fresh registry with fake/None
deps yields exactly the 15 currently-live command names.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.notifications.router import NotificationRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry() -> Any:
    """Snapshot+restore registry so tests don't bleed registrations."""
    snapshot = list(CommandRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _fresh_registry() -> CommandRegistry:
    CommandRegistry.reset()
    return CommandRegistry.instance()


def _fake_router() -> NotificationRouter:
    """Minimal NotificationRouter — no real DB needed for registration."""
    from unittest.mock import AsyncMock, MagicMock
    router = MagicMock(spec=NotificationRouter)
    router.deliver = AsyncMock()
    router.get_focus_mode = MagicMock(return_value="off")
    router.set_focus_mode = MagicMock()
    return router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_all_commands_returns_registry() -> None:
    reg = _fresh_registry()
    result = register_all_commands(CommandDeps(), registry=reg)
    assert result is reg


def test_register_all_commands_all_none_deps_yields_pattern_a_only() -> None:
    """With all-None deps, only the 8 dependency-free commands register."""
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    names = {c.command for c in reg.list()}
    # Pattern-A commands must always be present
    pattern_a = {"help", "config", "settings", "cost", "tools", "provider", "tier", "browser"}
    assert pattern_a.issubset(names), f"Missing pattern-A commands: {pattern_a - names}"


def test_register_all_commands_with_full_deps_yields_15_live_commands(
    tmp_path: Any,
) -> None:
    """Providing all deps for the 7 DI commands yields exactly the 15 live names."""
    import asyncio
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from stackowl.commands.assembly import CommandDeps, register_all_commands
    from stackowl.commands.registry import CommandRegistry

    # Build minimal fake deps
    fake_db = MagicMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_bus = MagicMock()
    fake_bus.emit = MagicMock()
    fake_settings = MagicMock()
    fake_bridge = MagicMock()
    fake_router = _fake_router()
    fake_store = MagicMock()
    fake_loader = MagicMock()
    fake_skills_root = tmp_path / "skills"
    fake_skills_root.mkdir(parents=True, exist_ok=True)
    fake_embedding = MagicMock()

    deps = CommandDeps(
        db=fake_db,
        event_bus=fake_bus,
        settings=fake_settings,
        bridge=fake_bridge,
        router=fake_router,
        skills_store=fake_store,
        skills_loader=fake_loader,
        skills_root=fake_skills_root,
        embedding_registry=fake_embedding,
    )

    reg = _fresh_registry()
    register_all_commands(deps, registry=reg)
    names = {c.command for c in reg.list()}

    expected_live: set[str] = {
        # Pattern A
        "help", "config", "settings", "cost", "tools", "provider", "tier", "browser",
        # Pattern B (DI)
        "skill", "memory", "owls", "focus", "urgent", "quiet", "notifications",
    }
    assert names == expected_live, (
        f"Registry mismatch.\n  extra={names - expected_live}\n  missing={expected_live - names}"
    )


def test_register_all_commands_idempotent_on_second_call() -> None:
    """Calling register_all_commands twice does not duplicate commands."""
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    count_1 = len(reg.list())
    register_all_commands(CommandDeps(), registry=reg)
    count_2 = len(reg.list())
    assert count_1 == count_2


def test_owls_command_registers_with_none_deps() -> None:
    """/owls always registers even when owl_registry/db/event_bus are None."""
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    names = {c.command for c in reg.list()}
    assert "owls" in names


def test_focus_quiet_notifications_absent_without_router_and_db() -> None:
    """focus/urgent/quiet/notifications do NOT register when their deps are None."""
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    names = {c.command for c in reg.list()}
    assert "focus" not in names
    assert "urgent" not in names
    assert "quiet" not in names
    assert "notifications" not in names


def test_focus_and_urgent_register_with_router() -> None:
    """focus and urgent register when router is provided."""
    from stackowl.events.bus import EventBus

    reg = _fresh_registry()
    fake_router = _fake_router()
    fake_bus = EventBus()
    deps = CommandDeps(router=fake_router, event_bus=fake_bus)
    register_all_commands(deps, registry=reg)
    names = {c.command for c in reg.list()}
    assert "focus" in names
    assert "urgent" in names


def test_registered_commands_are_correct_types(
    tmp_path: Any,
) -> None:
    """Each registry slot must hold the correct concrete type — catches swap regressions."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from stackowl.commands.focus_command import FocusCommand
    from stackowl.commands.memory_command import MemoryCommand
    from stackowl.commands.notifications_command import NotificationsMissedCommand
    from stackowl.commands.owls_command import OwlsCommand
    from stackowl.commands.quiet_command import QuietHoursCommand
    from stackowl.commands.skill_command import SkillCommand
    from stackowl.commands.urgent_command import UrgentCommand

    fake_db = MagicMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_bus = MagicMock()
    fake_bus.emit = MagicMock()
    fake_settings = MagicMock()
    fake_bridge = MagicMock()
    fake_router = _fake_router()
    fake_store = MagicMock()
    fake_loader = MagicMock()
    fake_skills_root = tmp_path / "skills"
    fake_skills_root.mkdir(parents=True, exist_ok=True)
    fake_embedding = MagicMock()

    deps = CommandDeps(
        db=fake_db,
        event_bus=fake_bus,
        settings=fake_settings,
        bridge=fake_bridge,
        router=fake_router,
        skills_store=fake_store,
        skills_loader=fake_loader,
        skills_root=fake_skills_root,
        embedding_registry=fake_embedding,
    )

    reg = _fresh_registry()
    register_all_commands(deps, registry=reg)
    by_name = {c.command: c for c in reg.list()}

    assert isinstance(by_name["focus"], FocusCommand), "focus slot holds wrong type"
    assert isinstance(by_name["urgent"], UrgentCommand), "urgent slot holds wrong type"
    assert isinstance(by_name["quiet"], QuietHoursCommand), "quiet slot holds wrong type"
    assert isinstance(by_name["notifications"], NotificationsMissedCommand), (
        "notifications slot holds wrong type"
    )
    assert isinstance(by_name["memory"], MemoryCommand), "memory slot holds wrong type"
    assert isinstance(by_name["owls"], OwlsCommand), "owls slot holds wrong type"
    assert isinstance(by_name["skill"], SkillCommand), "skill slot holds wrong type"
