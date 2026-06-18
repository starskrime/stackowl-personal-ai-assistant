"""make_checkpoint_callback — the per-iteration checkpoint callback factory (S4).

Drives the REAL durable primitives over a real SQLite DB (DbPool +
MigrationRunner, no mocks).  Proves:

* After each fired iteration the persisted checkpoint deserializes to a
  :class:`ReActCheckpoint` with the matching ``iteration`` + ``messages``, the
  task row's ``current_step`` advanced to ``iteration + 1``, and the alignment
  invariant holds: ``ctx.iteration == iteration + 1``.

* ALIGNMENT-WITH-LEDGER (the core S4 correctness proof): under ``activate(ctx)``,
  iteration 0 dispatches a side-effecting tool through the REAL ``ledger_guard``
  (records under ``step_index == 0``); the callback for ``state.iteration=0``
  advances ``ctx.iteration -> 1``; iteration 1 dispatches another side-effecting
  tool (records under ``step_index == 1``).  The two ledger rows land under the
  right step_index — proving the callback's alignment makes each iteration's
  side effects key correctly.

* save-failure path logs + re-raises (no silent swallow).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.checkpoint_callback import make_checkpoint_callback
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.ledger_guard import ledger_guard
from stackowl.pipeline.durable.react_checkpoint import deserialize
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.base import ToolResult

_OWNER = "principal-alice"
_TASK = "task-s4"


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "checkpoint_callback.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str, owner_id: str) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id,
        owner_id=owner_id,
        goal="durable react goal",
        status="running",
        current_step=0,
        created_at=now,
        updated_at=now,
    )


def _state(iteration: int) -> ReActIterationState:
    """A growing transcript snapshot for the given completed iteration."""
    messages = [
        {"role": "user", "content": "goal"},
        *[
            entry
            for n in range(iteration + 1)
            for entry in (
                {"role": "assistant", "content": f"round {n}"},
                {"role": "user", "content": f"observation {n}"},
            )
        ],
    ]
    records = [
        {"id": f"c{n}", "name": "do_thing", "args": {"n": n}, "result": "ok", "failed": False}
        for n in range(iteration + 1)
    ]
    return ReActIterationState(iteration=iteration, messages=messages, tool_call_records=records)


def _ok(output: str) -> ToolResult:
    return ToolResult(success=True, output=output, error=None, duration_ms=1.0)


def _run(output: str) -> Callable[[], Awaitable[ToolResult]]:
    """A zero-arg async execute_fn returning a successful ToolResult."""

    async def _fn() -> ToolResult:
        return _ok(output)

    return _fn


async def _ledger_rows(pool: DbPool) -> list[Any]:
    return await pool.fetch_all(
        "SELECT step_index, status, tool_name, result_blob FROM side_effect_ledger "
        "ORDER BY step_index",
        (),
    )


# ---------------------------------------------------------------------------
# Per-iteration checkpoint + alignment advance
# ---------------------------------------------------------------------------


async def test_each_iteration_checkpoints_and_advances_alignment(pool: DbPool) -> None:
    store = DurableTaskStore(pool, _OWNER)
    await store.create(_task(_TASK, _OWNER))
    ledger = SideEffectLedger(pool, _OWNER)
    ctx = DurableReActContext(task_id=_TASK, owner_id=_OWNER, ledger=ledger, iteration=0)
    callback = make_checkpoint_callback(ctx, store)

    for i in (0, 1, 2):
        # The drive runs iteration i with ctx.iteration == i (invariant).
        assert ctx.iteration == i
        state = _state(i)
        await callback(state)

        # Checkpoint persisted with the matching iteration + messages.
        blob = await store.load_checkpoint(_TASK)
        assert blob is not None
        cp = deserialize(blob)
        assert cp.iteration == i
        assert cp.messages == state.messages
        assert cp.tool_call_records == state.tool_call_records

        # current_step advanced to i + 1 ("iterations 0..i completed").
        task = await store.get(_TASK)
        assert task.current_step == i + 1
        assert task.status == "running"

        # CRITICAL ALIGNMENT — ctx.iteration advanced to i + 1.
        assert ctx.iteration == i + 1


# ---------------------------------------------------------------------------
# ALIGNMENT-WITH-LEDGER — the core S4 correctness proof
# ---------------------------------------------------------------------------


async def test_alignment_makes_side_effects_land_under_right_step(pool: DbPool) -> None:
    store = DurableTaskStore(pool, _OWNER)
    await store.create(_task(_TASK, _OWNER))
    ledger = SideEffectLedger(pool, _OWNER)
    ctx = DurableReActContext(task_id=_TASK, owner_id=_OWNER, ledger=ledger, iteration=0)
    callback = make_checkpoint_callback(ctx, store)

    with activate(ctx):
        # --- Iteration 0: ctx.iteration == 0 ---
        assert ctx.iteration == 0
        res0 = await ledger_guard(
            "send_email", {"to": "a@x", "body": "hello"}, "consequential",
            _run("sent-0"),
        )
        assert res0.output == "sent-0"
        # End of iteration 0 — callback advances ctx.iteration -> 1.
        await callback(_state(0))
        assert ctx.iteration == 1

        # --- Iteration 1: ctx.iteration == 1 ---
        res1 = await ledger_guard(
            "write_file", {"path": "out.txt", "content": "data"}, "write",
            _run("wrote-1"),
        )
        assert res1.output == "wrote-1"
        await callback(_state(1))
        assert ctx.iteration == 2

    # The two ledger rows landed under step_index 0 and 1 respectively —
    # proving the callback's alignment keys each iteration's side effects right.
    rows = await _ledger_rows(pool)
    assert len(rows) == 2
    assert [int(str(r["step_index"])) for r in rows] == [0, 1]
    by_step = {int(str(r["step_index"])): r for r in rows}
    assert by_step[0]["tool_name"] == "send_email"
    assert by_step[0]["status"] == "committed"
    assert by_step[1]["tool_name"] == "write_file"
    assert by_step[1]["status"] == "committed"


# ---------------------------------------------------------------------------
# save-failure path — logs + raises (no swallow)
# ---------------------------------------------------------------------------


async def test_save_failure_propagates_no_swallow(pool: DbPool) -> None:
    store = DurableTaskStore(pool, _OWNER)
    await store.create(_task(_TASK, _OWNER))
    ledger = SideEffectLedger(pool, _OWNER)
    ctx = DurableReActContext(task_id=_TASK, owner_id=_OWNER, ledger=ledger, iteration=0)
    callback = make_checkpoint_callback(ctx, store)

    boom = RuntimeError("disk full")

    async def _fail(_task_id: str, _blob: str) -> None:
        raise boom

    # Force the checkpoint write to fail.
    store.save_checkpoint = _fail  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="disk full"):
        await callback(_state(0))

    # Alignment was NOT advanced (the failure propagated before the bump).
    assert ctx.iteration == 0


# ---------------------------------------------------------------------------
# update_status-failure path — crash-window proof (save succeeded, status did not)
# ---------------------------------------------------------------------------


async def test_update_status_failure_propagates_no_advance(pool: DbPool) -> None:
    """Crash window: save_checkpoint succeeds but update_status raises.

    The callback must propagate the error and must NOT advance ctx.iteration —
    proving that the alignment bump only happens after both writes succeed.
    """
    store = DurableTaskStore(pool, _OWNER)
    await store.create(_task(_TASK, _OWNER))
    ledger = SideEffectLedger(pool, _OWNER)
    ctx = DurableReActContext(task_id=_TASK, owner_id=_OWNER, ledger=ledger, iteration=0)
    callback = make_checkpoint_callback(ctx, store)

    boom = RuntimeError("db locked")

    async def _fail_status(_task_id: str, _status: str, **_kwargs: object) -> None:
        raise boom

    # save_checkpoint succeeds (real impl); only update_status is patched to fail.
    store.update_status = _fail_status  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="db locked"):
        await callback(_state(0))

    # Alignment was NOT advanced (error propagated before the ctx.iteration bump).
    assert ctx.iteration == 0
