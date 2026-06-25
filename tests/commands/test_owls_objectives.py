"""`/owls objectives|objective|objective-cancel` — the manage surface (1D)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.commands.owls_command import OwlsCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from tests._story_6_7_helpers import make_state

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "owls_obj.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _seed(store: ObjectiveStore, objective_id: str, intent: str, *, subgoals: list[str]) -> None:
    await store.create(Objective(objective_id=objective_id, owner_id=DEFAULT_PRINCIPAL_ID, intent=intent))
    await store.add_subgoals(objective_id, subgoals)
    await store.append_event(objective_id, "created", intent)


async def test_objectives_lists_active(migrated_db: DbPool) -> None:
    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await _seed(store, "obj-1", "watch the build", subgoals=["a", "b"])
    cmd = OwlsCommand(db=migrated_db)
    out = await cmd.handle("objectives", make_state())
    assert "obj-1" in out
    assert "watch the build" in out
    assert "active" in out.lower()


async def test_objective_shows_subgoals_and_log(migrated_db: DbPool) -> None:
    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await _seed(store, "obj-2", "summarize daily", subgoals=["fetch", "summarize"])
    subs = await store.list_subgoals("obj-2")
    await store.update_subgoal(subs[0].subgoal_id, "done", result="fetched")
    await store.append_event("obj-2", "subgoal_done", "fetch")

    cmd = OwlsCommand(db=migrated_db)
    out = await cmd.handle("objective obj-2", make_state())
    assert "summarize daily" in out
    assert "fetch" in out and "summarize" in out
    assert "done" in out.lower()


async def test_objective_cancel_requires_confirmation(migrated_db: DbPool) -> None:
    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await _seed(store, "obj-3", "cancellable", subgoals=["x"])
    cmd = OwlsCommand(db=migrated_db)

    prompt = await cmd.handle("objective-cancel obj-3", make_state())
    assert "YES" in prompt  # asks to confirm
    assert (await store.get("obj-3")).status == "active"  # not yet cancelled

    done = await cmd.handle("objective-cancel obj-3 YES", make_state())
    assert "obj-3" in done
    assert (await store.get("obj-3")).status == "abandoned"


async def test_objectives_no_db_is_friendly_note() -> None:
    cmd = OwlsCommand(db=None)
    out = await cmd.handle("objectives", make_state())
    assert "objective" in out.lower()  # a friendly note, not a crash
