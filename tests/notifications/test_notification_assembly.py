"""Tests for NotificationAssembly — Commit C wire-up."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.commands.registry import CommandRegistry
from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.preferences import PreferenceStore
from stackowl.notifications.assembly import (
    NotificationAssembly,
    NotificationComponents,
)
from stackowl.scheduler.base import HandlerRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate_registries() -> Any:
    """Snapshot + restore CommandRegistry / HandlerRegistry so tests don't
    bleed registrations into the rest of the suite.

    We can't simply ``reset() + load_builtin_commands()`` because
    ``load_builtin_commands`` uses ``importlib.import_module`` — which is a
    no-op for already-cached modules, so the module-level
    ``register_command(...)`` lines do NOT re-execute. Snapshot-and-restore
    sidesteps this entirely.
    """
    cmd_snapshot = list(CommandRegistry.instance().list())
    handler_snapshot = list(HandlerRegistry.instance().list())
    yield
    CommandRegistry.reset()
    for cmd in cmd_snapshot:
        CommandRegistry.instance().register(cmd)
    HandlerRegistry.reset()
    for handler in handler_snapshot:
        HandlerRegistry.instance().register(handler)


async def _build(tmp_db: DbPool) -> NotificationComponents:
    return await NotificationAssembly.build(
        db=tmp_db,
        settings=Settings(),
        event_bus=EventBus(),
        preference_store=PreferenceStore(db=tmp_db),
    )


async def test_build_returns_frozen_components(tmp_db: DbPool) -> None:
    components = await _build(tmp_db)
    assert isinstance(components, NotificationComponents)
    with pytest.raises(Exception):
        components.router = None  # type: ignore[misc]


async def test_build_constructs_router_singleton(tmp_db: DbPool) -> None:
    components = await _build(tmp_db)
    assert components.router is not None
    assert components.router.get_focus_mode() == "off"


async def test_build_registers_digest_handler(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    handler = HandlerRegistry.instance().get("notification_digest")
    assert handler is not None
    assert handler.handler_name == "notification_digest"


async def test_build_seeds_digest_schedule(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule FROM jobs WHERE handler_name = ?",
        ("notification_digest",),
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "every 5m"


async def test_build_seed_is_idempotent(tmp_db: DbPool) -> None:
    """Second build call must not duplicate the seeded digest row.

    Registries support re-register-as-overwrite so we don't need to reset
    them between the two build calls — only the DB seed idempotency matters.
    """
    await _build(tmp_db)
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?",
        ("notification_digest",),
    )
    assert len(rows) == 1  # NOT 2


async def test_build_returns_four_router_dependent_command_objects(tmp_db: DbPool) -> None:
    """build() constructs all 4 notification commands and exposes them in components.

    Registration onto CommandRegistry is now done by register_all_commands
    (commands/assembly.py), not by NotificationAssembly.build itself.
    """
    components = await _build(tmp_db)
    assert components.focus_command.command == "focus"
    assert components.urgent_command.command == "urgent"
    assert components.quiet_command.command == "quiet"
    assert components.notifications_missed_command.command == "notifications"


async def test_focus_mode_hydrates_from_preference_store(tmp_db: DbPool) -> None:
    """If focus_mode was persisted before, the new router picks it up."""
    pref_store = PreferenceStore(db=tmp_db)
    await pref_store.set("global", "focus_mode", "hard")
    components = await NotificationAssembly.build(
        db=tmp_db, settings=Settings(),
        event_bus=EventBus(), preference_store=pref_store,
    )
    assert components.router.get_focus_mode() == "hard"


async def test_focus_mode_persists_on_change(tmp_db: DbPool) -> None:
    """set_focus_mode triggers async-persistence so the next boot sees it."""
    pref_store = PreferenceStore(db=tmp_db)
    components = await NotificationAssembly.build(
        db=tmp_db, settings=Settings(),
        event_bus=EventBus(), preference_store=pref_store,
    )
    components.router.set_focus_mode("soft")
    # Persistence is fire-and-forget; await one event-loop tick to let it commit.
    await asyncio.sleep(0.05)
    assert await pref_store.get("global", "focus_mode") == "soft"


async def test_invalid_persisted_focus_value_ignored(tmp_db: DbPool) -> None:
    """A junk persisted value doesn't crash hydration — router stays at default."""
    pref_store = PreferenceStore(db=tmp_db)
    await pref_store.set("global", "focus_mode", "bogus-value")
    components = await NotificationAssembly.build(
        db=tmp_db, settings=Settings(),
        event_bus=EventBus(), preference_store=pref_store,
    )
    assert components.router.get_focus_mode() == "off"
