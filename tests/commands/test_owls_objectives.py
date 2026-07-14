"""`/owls objectives|objective|objective-cancel|objective-merge` — the manage surface (1D/Task 10)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

from stackowl.commands.owls_command import OwlsCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

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


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


async def test_objective_merge_full_completion(tmp_path: Path, migrated_db: DbPool) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    integration_branch = "stackowl/epic-obj-m1"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)

    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await store.create(
        Objective(
            objective_id="obj-m1", owner_id=DEFAULT_PRINCIPAL_ID, intent="epic",
            repo=str(repo), integration_branch=integration_branch, base_branch="main",
            status="blocked", blocker="awaiting merge confirm", blocker_kind="decision",
        )
    )
    [sg] = await store.add_subgoals("obj-m1", ["a"])
    await store.update_subgoal(sg.subgoal_id, "done")

    cmd = OwlsCommand(db=migrated_db)
    result = await cmd.handle("objective-merge obj-m1 YES", make_state())
    assert "merged" in result.lower()
    reloaded = await store.get("obj-m1")
    assert reloaded.status == "done"
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert current == "main"


async def test_objective_merge_refuses_when_not_ready(migrated_db: DbPool) -> None:
    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await store.create(
        Objective(
            objective_id="obj-m2", owner_id=DEFAULT_PRINCIPAL_ID, intent="epic",
            repo="/tmp/x", integration_branch="stackowl/epic-obj-m2", base_branch="main",
            status="active",
        )
    )
    cmd = OwlsCommand(db=migrated_db)
    result = await cmd.handle("objective-merge obj-m2 YES", make_state())
    assert "not ready" in result.lower() or "✗" in result


async def test_objective_merge_requires_yes_confirmation(migrated_db: DbPool) -> None:
    store = ObjectiveStore(migrated_db, DEFAULT_PRINCIPAL_ID)
    await store.create(
        Objective(
            objective_id="obj-m3", owner_id=DEFAULT_PRINCIPAL_ID, intent="epic",
            repo="/tmp/x", integration_branch="stackowl/epic-obj-m3", base_branch="main",
            status="blocked", blocker="awaiting merge confirm", blocker_kind="decision",
        )
    )
    [sg] = await store.add_subgoals("obj-m3", ["a"])
    await store.update_subgoal(sg.subgoal_id, "done")
    cmd = OwlsCommand(db=migrated_db)
    result = await cmd.handle("objective-merge obj-m3", make_state())
    assert "YES" in result
