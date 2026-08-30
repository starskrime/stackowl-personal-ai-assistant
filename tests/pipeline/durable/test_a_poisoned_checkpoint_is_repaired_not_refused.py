"""D01.5's closer, at the seam — a poisoned checkpoint RESUMES instead of dying.

The unit tests in tests/providers/test_resume_validation.py prove the repair is
CORRECT. They cannot prove it is REACHED, and this programme has shipped a
correct-but-unreached mechanism four times. This file drives the real
``_reconstruct_state`` — the single point where a checkpoint becomes a resume
transcript — so unwiring the closer there fails a test even though every unit
test still passes.

The incident it closes: on 2026-08-30 at 03:20:22 trace ``recover-4e6044f0cde9``
was refused by the resume validator, surfaced as "critical step failed with no
response", and died having delivered nothing.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.react_checkpoint import ReActCheckpoint, serialize
from stackowl.pipeline.durable.recovery import DurableTaskRecoverer
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.state import PipelineState
from stackowl.providers.resume_validation import (
    CLOSING_TURN_TEXT,
    validate_resume_transcript,
)

_OWNER = "principal-default"


class _NullBackend:
    """OrchestratorBackend stub; _reconstruct_state never drives the backend."""

    async def run(self, state: PipelineState) -> PipelineState:  # pragma: no cover
        return state


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "poisoned_checkpoint.db"
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


def _running_task(task_id: str) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=_OWNER,
        goal="recover me",
        status="running",
        owl_name="o",
        channel="cli",
        created_at=now,
        updated_at=now,
    )


# An Anthropic-shaped transcript whose user turn lands on an unclosed tool run:
# the tool_result rides IN a user turn, so the directive that follows it makes
# two consecutive user turns and the model continues it instead of answering.
_POISONED = [
    {"role": "user", "content": "find the film"},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu1", "name": "browse", "input": {}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "HTTP 404"}]},
    {"role": "user", "content": "You have not yet delivered the requested outcome"},
]


async def _seed(store: DurableTaskStore, task_id: str) -> None:
    await store.create(_running_task(task_id))
    await store.save_checkpoint(
        task_id,
        serialize(ReActCheckpoint(
            iteration=3, messages=_POISONED, tool_call_records=[],
        )),
    )


async def test_a_poisoned_checkpoint_comes_back_resumable(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """The reconstructed state's transcript passes the validator that used to kill it.

    This is the assertion the mutation targets: unwire the closer from
    ``_reconstruct_state`` and the transcript arrives poisoned, so the validator
    raises here — exactly as it did in production.
    """
    await _seed(store, "task-poison-1")

    state = await recovery._reconstruct_state(await store.get("task-poison-1"))

    assert state.durable_resume_messages is not None
    validate_resume_transcript(state.durable_resume_messages, provider_kind="anthropic")
    assert state.durable_resume_messages[3] == {
        "role": "assistant", "content": CLOSING_TURN_TEXT,
    }


async def test_the_repair_does_not_write_back_over_the_checkpoint(
    store: DurableTaskStore, recovery: DurableTaskRecoverer
) -> None:
    """Repair the resume COPY, never the stored record of what actually happened.

    A checkpoint is evidence. Rewriting it in place would make the transcript we
    resume from and the transcript we recorded diverge silently, and the next
    audit of this table would be reading a transcript nobody ever sent.
    """
    await _seed(store, "task-poison-2")

    state = await recovery._reconstruct_state(await store.get("task-poison-2"))
    assert state.durable_resume_messages is not None
    assert len(state.durable_resume_messages) == len(_POISONED) + 1

    blob = await store.load_checkpoint("task-poison-2")
    assert blob is not None
    from stackowl.pipeline.durable.react_checkpoint import deserialize
    assert list(deserialize(blob).messages) == _POISONED
