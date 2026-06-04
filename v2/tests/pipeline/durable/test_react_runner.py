"""DurableReActRunner — start, resume, exactly-once-on-resume (S5).

Drives the REAL durable primitives over a real SQLite DB (DbPool +
MigrationRunner, REAL DurableTaskStore + SideEffectLedger + ledger_guard +
checkpoint callback) with only the provider loop faked via an injected
``drive``.  Proves:

* **happy path** — a 3-iteration fake drive (one iteration dispatching a
  side-effecting tool through the REAL ledger_guard) completes; the task is
  ``completed`` with the result, a checkpoint was persisted, and the ledger has
  the committed row.

* **J1/J2 resume (the core proof)** — a first drive runs iterations 0 and 1
  (iter 1 commits a side-effect), persists the iter-1 checkpoint, then RAISES
  (crash).  The task is ``failed`` with a checkpoint at iteration 1.  A second
  (resume) drive is handed the restored messages + callback and re-runs from
  iteration 2 to completion; when it RE-attempts iteration 1's side-effecting
  tool (same task/iteration/tool/args) the ledger returns ``already_committed``
  and the underlying effect counter stays 1 (exactly-once).  ``ctx.iteration``
  is seeded to 2 and the restored messages drive completion.

* **uncertain -> parked** — a drive whose side-effecting tool hits an
  intent-without-commit ledger row raises ``DurableReplayUncertain`` and the
  task is ``parked`` (never re-run blindly).

* **completed-task resume is a no-op** — resuming a ``completed`` task returns
  it unchanged without invoking the drive.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.context import get_active
from stackowl.pipeline.durable.ledger_guard import ledger_guard
from stackowl.pipeline.durable.react_checkpoint import deserialize
from stackowl.pipeline.durable.react_runner import DurableReActRunner
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.tools.base import ToolResult

_OWNER = "principal-alice"


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "react_runner.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _ok(output: str) -> ToolResult:
    return ToolResult(success=True, output=output, error=None, duration_ms=1.0)


async def _ledger_rows(pool: DbPool) -> list[dict]:
    return await pool.fetch_all(
        "SELECT step_index, status, tool_name, result_blob FROM side_effect_ledger "
        "ORDER BY step_index",
        (),
    )


# ---------------------------------------------------------------------------
# happy path — 3 iterations, one side-effect, completes
# ---------------------------------------------------------------------------


async def test_start_happy_path_completes_with_checkpoint_and_ledger(pool: DbPool) -> None:
    effect_counter = {"n": 0}

    async def _side_effect() -> ToolResult:
        effect_counter["n"] += 1
        return _ok(f"effect-{effect_counter['n']}")

    async def drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:
        # 3 iterations; iteration 1 dispatches a side-effecting tool through the
        # REAL ledger_guard under the active durable context.
        for i in range(3):
            # The runner activated the context: ctx.iteration must equal i here.
            ctx = get_active()
            assert ctx is not None
            assert ctx.iteration == i
            messages.append({"role": "assistant", "content": f"round {i}"})
            if i == 1:
                res = await ledger_guard(
                    "send_email", {"to": "a@x", "body": "hi"},
                    "consequential", _side_effect,
                )
                assert res.output == "effect-1"
                messages.append({"role": "user", "content": "observation"})
            await callback(ReActIterationState(iteration=i, messages=list(messages)))
        return "final answer", messages

    runner = DurableReActRunner(pool, _OWNER)
    task = await runner.start("do the thing", drive)

    assert task.status == "completed"
    assert task.result == "final answer"
    # Side effect ran exactly once.
    assert effect_counter["n"] == 1

    # A checkpoint was persisted (last iteration = 2).
    store = DurableTaskStore(pool, _OWNER)
    blob = await store.load_checkpoint(task.task_id)
    assert blob is not None
    cp = deserialize(blob)
    assert cp.iteration == 2
    # current_step advanced to 3 (iterations 0..2 completed).
    reloaded = await store.get(task.task_id)
    assert reloaded.current_step == 3

    # The ledger has the committed side-effect row at step_index 1.
    rows = await _ledger_rows(pool)
    assert len(rows) == 1
    assert int(str(rows[0]["step_index"])) == 1
    assert rows[0]["tool_name"] == "send_email"
    assert rows[0]["status"] == "committed"


# ---------------------------------------------------------------------------
# J1/J2 resume — crash after iter-1 commit, resume re-runs exactly-once
# ---------------------------------------------------------------------------


async def test_resume_replays_exactly_once_across_crash(pool: DbPool) -> None:
    effect_counter = {"n": 0}

    async def _side_effect() -> ToolResult:
        effect_counter["n"] += 1
        return _ok(f"effect-{effect_counter['n']}")

    # --- First drive: runs iters 0 and 1 (iter 1 commits the side-effect),
    #     persists the iter-1 checkpoint, then crashes. ---
    class _Crash(RuntimeError):
        pass

    async def crash_drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:
        for i in range(2):
            ctx = get_active()
            assert ctx is not None
            assert ctx.iteration == i
            messages.append({"role": "assistant", "content": f"round {i}"})
            if i == 1:
                res = await ledger_guard(
                    "send_email", {"to": "a@x", "body": "hi"},
                    "consequential", _side_effect,
                )
                assert res.output == "effect-1"
                messages.append({"role": "user", "content": "observation"})
            await callback(ReActIterationState(iteration=i, messages=list(messages)))
        # The iter-1 checkpoint has been persisted; now CRASH before terminal.
        raise _Crash("simulated crash after iter-1 commit")

    runner = DurableReActRunner(pool, _OWNER)
    with pytest.raises(_Crash):
        await runner.start("do the thing", crash_drive, task_id="task-crash")

    # The crash interrupted the task with a checkpoint at iteration 1.  The
    # in-process exception ran the fail-loud handler (status=failed); a REAL
    # crash (process kill) would leave it `running`.  Simulate the orphaned-
    # process state that S7 recovery / resume operates on by resetting to
    # `running` — resume only no-ops on already-terminal tasks.
    store = DurableTaskStore(pool, _OWNER)
    crashed = await store.get("task-crash")
    assert crashed.status == "failed"
    blob = await store.load_checkpoint("task-crash")
    assert blob is not None
    assert deserialize(blob).iteration == 1
    # The side effect committed exactly once pre-crash.
    assert effect_counter["n"] == 1
    await store.update_status("task-crash", "running")

    # --- Resume drive: gets the restored messages + callback; ctx.iteration is
    #     seeded to 2 by the runner.  It re-attempts iter-1's side-effecting tool
    #     (which must return already_committed) then runs iter 2 to completion. ---
    captured: dict = {}

    async def resume_drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:
        ctx = get_active()
        assert ctx is not None
        # The runner seeded iteration to cp.iteration + 1 == 2.
        captured["resume_iteration"] = ctx.iteration
        captured["restored_messages"] = list(messages)

        # Re-attempt iteration 1's side-effecting tool at its ORIGINAL step_index
        # (1) — same task/iteration/tool/args -> already_committed, no re-run.
        ctx.iteration = 1
        replay = await ledger_guard(
            "send_email", {"to": "a@x", "body": "hi"},
            "consequential", _side_effect,
        )
        assert replay.output == "effect-1"  # recorded result, NOT a fresh run
        ctx.iteration = 2

        # Continue from iteration 2 to the terminal answer.
        messages.append({"role": "assistant", "content": "round 2"})
        await callback(ReActIterationState(iteration=2, messages=list(messages)))
        return "resumed final answer", messages

    task = await runner.resume("task-crash", resume_drive)

    # Resume seeded ctx.iteration to 2 (cp.iteration 1 + 1).
    assert captured["resume_iteration"] == 2
    # Messages were restored from the checkpoint (iter-1 transcript), NOT reset
    # to just the goal.
    restored = captured["restored_messages"]
    assert {"role": "assistant", "content": "round 0"} in restored
    assert {"role": "assistant", "content": "round 1"} in restored

    # Task completed on resume.
    assert task.status == "completed"
    assert task.result == "resumed final answer"

    # EXACTLY-ONCE: the underlying side effect counter is STILL 1 across the
    # crash + resume — the replayed call hit already_committed and did not run.
    assert effect_counter["n"] == 1

    # The ledger still has exactly one committed row for that side effect.
    rows = await _ledger_rows(pool)
    assert len(rows) == 1
    assert rows[0]["status"] == "committed"
    assert int(str(rows[0]["step_index"])) == 1


# ---------------------------------------------------------------------------
# uncertain -> parked
# ---------------------------------------------------------------------------


async def test_uncertain_ledger_replay_parks_task(pool: DbPool) -> None:
    # Pre-seed an INTENT-without-commit row for (task, step 0, tool, args): a
    # prior attempt died mid-execution.  A drive that re-attempts that exact call
    # must get `uncertain` -> DurableReplayUncertain -> task parked.
    runner = DurableReActRunner(pool, _OWNER)

    async def drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:
        ctx = get_active()
        assert ctx is not None
        # Write the intent row directly via the ledger's begin (proceed), then a
        # SECOND begin of the same key returns uncertain — but to model a crash
        # *between attempts* we open the intent then re-attempt via ledger_guard.
        await ctx.ledger.begin(ctx.task_id, 0, "send_email", {"to": "a@x"})
        # Now the guard sees an existing intent -> uncertain -> raises.
        async def _never() -> ToolResult:  # pragma: no cover - must NOT run
            raise AssertionError("side effect must not run on uncertain")

        await ledger_guard("send_email", {"to": "a@x"}, "consequential", _never)
        return "unreachable", messages

    task = await runner.start("park me", drive, task_id="task-park")
    assert task.status == "parked"

    store = DurableTaskStore(pool, _OWNER)
    reloaded = await store.get("task-park")
    assert reloaded.status == "parked"


# ---------------------------------------------------------------------------
# completed-task resume is a no-op
# ---------------------------------------------------------------------------


async def test_resume_completed_task_is_noop(pool: DbPool) -> None:
    runner = DurableReActRunner(pool, _OWNER)

    async def drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:
        await callback(ReActIterationState(iteration=0, messages=list(messages)))
        return "done", messages

    task = await runner.start("finish me", drive, task_id="task-done")
    assert task.status == "completed"

    # Resume must NOT invoke the drive again.
    async def boom_drive(
        messages: list[dict], callback: IterationCallback,
    ) -> tuple[str, list[dict]]:  # pragma: no cover - must NOT run
        raise AssertionError("drive must not run for a completed task")

    resumed = await runner.resume("task-done", boom_drive)
    assert resumed.status == "completed"
    assert resumed.result == "done"
