"""``scheduling.set_objective`` -- persists an objective + its (already
decomposed, upstream of this command per AD-26) sub-goals exactly once per
``command_id``, records ``objective.set``, and (for an EPIC) creates the
integration branch BEFORE persisting anything (Story 4.7).

Modelled on ``tests/commands/spec/test_authority_commands_registered.py``'s
own end-to-end style: a :class:`DurableTask` with ``gate_verdict="approved"``
skips the action-policy gate entirely (``scheduling.set_objective`` is
declared IRREVERSIBLE -- Design Notes -- so an UNGATED call would always
park) and reaches the real handler through :func:`execute_command_task`,
the correct unit boundary for testing a handler's own mutation (mirrors
``tests/scheduler/test_pause_resume_are_commands.py``'s "call the mutator
directly with a constructed CommandContext" convention, one layer up).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

import stackowl.objectives.commands  # noqa: F401 -- registration side effect
from stackowl.commands.spec.execute import execute_command_task
from stackowl.db.pool import DbPool
from stackowl.objectives.commands import SET_OBJECTIVE
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "set_objective.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _payload(objective_id: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "objective_id": objective_id,
        "intent": "watch X and handle it",
        "channel": "telegram",
        "session_key": "sess-1",
        "target_channels": ["telegram"],
        "target_addresses": {"telegram": 12345},
        "repo": None,
        "integration_branch": None,
        "base_branch": None,
        "subgoals": [{"description": "step one"}, {"description": "step two"}],
    }
    base.update(overrides)
    return base


def _task(
    command_id: str, payload: dict[str, object], *, gate_verdict: str | None = "approved",
) -> DurableTask:
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{SET_OBJECTIVE}", status="running",
        kind="command", command_type=SET_OBJECTIVE,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="owner", gate_verdict=gate_verdict,
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


# --------------------------------------------------------------------------- persistence


async def test_set_objective_persists_objective_subgoals_and_events(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_task("cmd-set-1", _payload("obj-1")))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["objective_id"] == "obj-1"
    assert outcome.result["created"] is True
    assert outcome.result["step_count"] == 2

    store = ObjectiveStore(tmp_db)
    obj = await store.get("obj-1")
    assert obj.intent == "watch X and handle it"
    assert obj.channel == "telegram"
    subs = await store.list_subgoals("obj-1")
    assert [s.description for s in subs] == ["step one", "step two"]
    kinds = [e.kind for e in await store.list_events("obj-1")]
    assert "created" in kinds
    assert "decomposed" in kinds


async def test_set_objective_records_objective_set_journal_event(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        await execute_command_task(_task("cmd-set-2", _payload("obj-2")))
    finally:
        reset_services(token)

    rows = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'objective.set' AND target_id = ?",
        ("obj-2",),
    )
    assert len(rows) == 1


# --------------------------------------------------------------------------- idempotency


async def test_set_objective_re_run_with_the_same_command_id_is_a_no_op(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        first = await execute_command_task(_task("cmd-set-reclaim", _payload("obj-3")))
        second = await execute_command_task(_task("cmd-set-reclaim", _payload("obj-3")))
    finally:
        reset_services(token)

    assert first.success is True
    assert second.success is True
    assert second.result["created"] is False

    store = ObjectiveStore(tmp_db)
    subs = await store.list_subgoals("obj-3")
    assert len(subs) == 2, "a lease-reclaim re-run must not insert the sub-goals twice"
    rows = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'objective.set' AND target_id = ?",
        ("obj-3",),
    )
    assert len(rows) == 1


async def test_no_db_pool_fails_cleanly() -> None:
    token = set_services(StepServices())
    try:
        outcome = await execute_command_task(_task("cmd-set-no-db", _payload("obj-4")))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert outcome.error == "objectives unavailable (no database configured)"


# --------------------------------------------------------------------------- git-branch (repo)


async def test_set_objective_creates_the_integration_branch_for_an_epic(
    tmp_path: Path, tmp_db: DbPool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    payload = _payload(
        "obj-epic-1", repo=str(repo), integration_branch="stackowl/epic-obj-epic-1",
        base_branch="main",
    )
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_task("cmd-set-epic-1", payload))
    finally:
        reset_services(token)

    assert outcome.success is True
    store = ObjectiveStore(tmp_db)
    obj = await store.get("obj-epic-1")
    assert obj.repo == str(repo)
    assert obj.integration_branch == "stackowl/epic-obj-epic-1"

    branches = subprocess.run(
        ["git", "branch"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "stackowl/epic-obj-epic-1" in branches


async def test_set_objective_git_branch_failure_refuses_before_persisting(
    tmp_path: Path, tmp_db: DbPool,
) -> None:
    """A ``repo`` path that is NOT a real git repo -- ``git branch`` fails,
    and (Design Notes: "git-branch runs BEFORE the idempotency receipt")
    NOTHING is persisted: no objective row, no receipt, no journal event."""
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    payload = _payload(
        "obj-epic-fail", repo=str(not_a_repo),
        integration_branch="stackowl/epic-obj-epic-fail", base_branch="main",
    )
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_task("cmd-set-epic-fail", payload))
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.error is not None
    assert "integration branch" in outcome.error

    store = ObjectiveStore(tmp_db)
    assert await store.list_objectives() == []
    receipts = await tmp_db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", ("cmd-set-epic-fail",),
    )
    assert receipts == []
