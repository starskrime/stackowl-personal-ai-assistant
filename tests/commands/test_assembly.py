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


def test_safe_register_isolates_a_failing_construction() -> None:
    """One command whose construction raises must NOT abort the others.

    Reinforces the core invariant: a future eager-I/O command that throws at
    __init__ is skipped + logged, the rest still register, and the reachability
    guard (== SHIPPED) flags the one that went missing — instead of a single
    failure silently vanishing a whole swath of the surface.
    """
    from unittest.mock import patch

    reg = _fresh_registry()
    real_skill = __import__("stackowl.commands.skill_command", fromlist=["SkillCommand"]).SkillCommand

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("simulated eager-I/O failure at construction")

    # Make ONLY /skill construction blow up; everything else must still register.
    with patch.object(
        __import__("stackowl.commands.skill_command", fromlist=["SkillCommand"]),
        "SkillCommand",
        side_effect=_boom,
    ):
        register_all_commands(CommandDeps(), registry=reg)

    names = {c.command for c in reg.list()}
    assert "skill" not in names, "the failing command must be skipped"
    # The rest of the core set still registered despite the failure.
    assert (_CORE_15 - {"skill"}) <= names
    assert real_skill is not None  # sanity: symbol exists


# The 15 commands that were live before the Epic B wiring campaign began.
# Registration is dep-INDEPENDENT, so these must register even with empty deps.
# (The EXACT full-set contract — all 29 — is owned by the reachability guard in
# tests/journeys/commands/; this test only locks the core invariant that the
# original 15 never regress as Epic B adds more.)
_CORE_15 = {
    "help", "config", "settings", "cost", "tools", "provider", "tier", "browser",
    "skill", "memory", "owls", "focus", "urgent", "quiet", "notifications",
}


def test_register_all_commands_core_15_register_with_none_deps() -> None:
    """Dep-INDEPENDENT registration: with all-None deps, the original 15 live
    commands still register (8 Pattern-A + 7 DI). "shipped ⟺ registered" must not
    depend on runtime wiring, so the reachability guard (empty deps) is a true
    proxy for production reachability."""
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    names = {c.command for c in reg.list()}
    assert names >= _CORE_15, f"missing core commands: {_CORE_15 - names}"


def test_register_all_commands_with_full_deps_includes_core_15(
    tmp_path: Any,
) -> None:
    """Providing real deps registers (at least) the core 15 with no duplicates."""
    from unittest.mock import AsyncMock, MagicMock

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
    all_cmds = [c.command for c in reg.list()]
    names = set(all_cmds)

    assert names >= _CORE_15, f"missing core commands: {_CORE_15 - names}"
    assert len(all_cmds) == len(names), "duplicate command registrations detected"


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


def test_di_commands_register_unconditionally_even_with_none_deps() -> None:
    """focus/urgent/quiet/notifications register even when their deps are None.

    Deliberate: a command that fails to register because the orchestrator forgot
    to populate a dep would silently vanish into "Unknown slash command" — the
    exact "looks-wired-but-never-fires" bug this overhaul kills. Instead they
    always register and emit an honest "not configured" message at dispatch time.
    """
    reg = _fresh_registry()
    register_all_commands(CommandDeps(), registry=reg)
    names = {c.command for c in reg.list()}
    assert "focus" in names
    assert "urgent" in names
    assert "quiet" in names
    assert "notifications" in names


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
