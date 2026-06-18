"""E2-S3 — recovery restores task_envelope; resume does NOT re-plan."""

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
# Fixtures — real DbPool + migrated DB (same pattern as test_recovery_ceiling.py).
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "recovery_envelope.db"
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


def _running_task(task_id: str, envelope: BoundsSpec | None) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=_OWNER,
        goal="recover me",
        status="running",
        owl_name="o",
        channel="cli",
        task_envelope=envelope,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tests — no-checkpoint branch (simplest to exercise _reconstruct_state).
# ---------------------------------------------------------------------------


async def test_reconstruct_restores_envelope(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """_reconstruct_state threads the persisted task_envelope into the resumed state."""
    env = BoundsSpec(tools=frozenset({"a", "tool_search"}))
    task = _running_task("task-renv-1", env)
    await store.create(task)

    state = await recovery._reconstruct_state(await store.get("task-renv-1"))
    assert state.task_envelope == env


async def test_reconstruct_null_envelope_is_none(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """_reconstruct_state with task_envelope=None results in state.task_envelope=None."""
    task = _running_task("task-renv-2", None)
    await store.create(task)

    state = await recovery._reconstruct_state(await store.get("task-renv-2"))
    assert state.task_envelope is None


# ---------------------------------------------------------------------------
# Tests — checkpoint-loaded branch (the common mid-run resume case).
# ---------------------------------------------------------------------------


async def test_reconstruct_restores_envelope_on_checkpoint_branch(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """The task_envelope must survive the checkpoint-loaded reconstruction branch too.

    A resumed task without its envelope would run un-enveloped — a security-critical
    invariant that is distinct from the no-checkpoint branch.
    """
    env = BoundsSpec(tools=frozenset({"a", "tool_search"}))
    task = _running_task("task-renv-cp-1", env)
    await store.create(task)

    # Save a checkpoint blob so _reconstruct_state takes the mid-transcript branch.
    cp = ReActCheckpoint(
        iteration=2,
        messages=[{"role": "user", "content": "turn 2"}],
        tool_call_records=[{"id": "c1", "name": "fake_tool", "args": {}, "result": "ok", "failed": False}],
    )
    await store.save_checkpoint("task-renv-cp-1", serialize(cp))

    state = await recovery._reconstruct_state(await store.get("task-renv-cp-1"))

    # Envelope must not be silently dropped on checkpoint branch.
    assert state.task_envelope == env

    # Resume markers must be populated — proves the checkpoint branch ran.
    assert state.durable_resume_messages is not None
    assert state.durable_resume_messages == cp.messages
    assert state.durable_resume_tool_calls == cp.tool_call_records
    assert state.durable_resume_iteration == cp.iteration + 1
