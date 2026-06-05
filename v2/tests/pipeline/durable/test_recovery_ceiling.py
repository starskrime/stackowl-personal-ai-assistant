"""E2-S2 — recovery threads the persisted creation_ceiling into the resumed state."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.react_checkpoint import ReActCheckpoint, serialize
from stackowl.pipeline.durable.recovery import DurableTaskRecoverer
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.state import PipelineState

# ---------------------------------------------------------------------------
# Minimal backend double — never actually called during _reconstruct_state.
# ---------------------------------------------------------------------------

_OWNER = "principal-default"


class _NullBackend:
    """OrchestratorBackend stub; _reconstruct_state never drives the backend."""

    async def run(self, state: PipelineState) -> PipelineState:  # noqa: D102  # pragma: no cover
        return state


# ---------------------------------------------------------------------------
# Fixtures — real DbPool + migrated DB (same pattern as test_task_store.py).
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "recovery_ceiling.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture()
def store(pool: DbPool) -> DurableTaskStore:
    return DurableTaskStore(pool, _OWNER)


@pytest.fixture()
def recovery(pool: DbPool) -> DurableTaskRecoverer:
    return DurableTaskRecoverer(pool, _NullBackend(), owner_id=_OWNER)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _running_task(task_id: str, ceiling: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=_OWNER,
        goal="recover me",
        status="running",
        owl_name="o",
        channel="cli",
        creation_ceiling=ceiling,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tests — no-checkpoint branch (simplest to exercise _reconstruct_state).
# ---------------------------------------------------------------------------


async def test_reconstruct_threads_ceiling(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """_reconstruct_state threads the persisted creation_ceiling into the resumed state."""
    ceiling = BoundsSpec(tools=frozenset({"a"}))
    task = _running_task("task-rec-1", ceiling)
    await store.create(task)

    state = await recovery._reconstruct_state(await store.get("task-rec-1"))
    assert state.creation_ceiling == ceiling


async def test_reconstruct_null_ceiling_is_none(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """_reconstruct_state with a None ceiling results in state.creation_ceiling=None."""
    task = _running_task("task-rec-2", None)
    await store.create(task)

    state = await recovery._reconstruct_state(await store.get("task-rec-2"))
    assert state.creation_ceiling is None


async def test_reconstruct_deny_all_ceiling_roundtrips(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """A deny-all ceiling (empty tools frozenset) is distinct from None after recovery."""
    ceiling = BoundsSpec(tools=frozenset())
    task = _running_task("task-rec-3", ceiling)
    await store.create(task)

    state = await recovery._reconstruct_state(await store.get("task-rec-3"))
    assert state.creation_ceiling is not None
    assert state.creation_ceiling.tools == frozenset()


# ---------------------------------------------------------------------------
# Tests — checkpoint-loaded branch (COMMON resume case: task killed mid-run).
# ---------------------------------------------------------------------------


async def test_reconstruct_threads_ceiling_on_checkpoint_branch(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """The COMMON resume case: a task killed mid-run HAS a checkpoint.

    The ceiling must survive the checkpoint-loaded reconstruction branch too
    (no fail-open).  A resumed task without its ceiling would run unbounded —
    a security-critical invariant that is distinct from the no-checkpoint branch.
    """
    ceiling = BoundsSpec(tools=frozenset({"a"}))
    # 1. Seed a 'running' task carrying the ceiling.
    task = _running_task("task-rec-cp-1", ceiling)
    await store.create(task)

    # 2. Save a checkpoint blob so _reconstruct_state takes the mid-transcript branch.
    cp = ReActCheckpoint(
        iteration=2,
        messages=[{"role": "user", "content": "turn 2"}],
        tool_call_records=[{"id": "c1", "name": "read_file", "args": {}, "result": "ok", "failed": False}],
    )
    await store.save_checkpoint("task-rec-cp-1", serialize(cp))

    # 3. Reconstruct and assert BOTH the resume markers AND the ceiling survive.
    state = await recovery._reconstruct_state(await store.get("task-rec-cp-1"))

    # Ceiling must not be silently dropped — fail-open would let resumed tasks
    # run unbounded.
    assert state.creation_ceiling == ceiling

    # Resume markers must be populated — this proves the mid-transcript branch
    # ran (not the no-checkpoint branch which leaves them None).
    assert state.durable_resume_messages is not None
    assert state.durable_resume_messages == cp.messages
    assert state.durable_resume_tool_calls == cp.tool_call_records
    assert state.durable_resume_iteration == cp.iteration + 1
