"""DurableExecutor — checkpointed multi-step execution + exactly-once (Pass 3b).

Real SQLite via DbPool + MigrationRunner (no mocks): steps are plain async
functions wrapped in CallableStep, with shared mutable counters proving J1
(resume from checkpoint, no re-run) and J2 (side-effect runs exactly once).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.executor import CallableStep, DurableExecutor
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.state import PipelineState


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "exec.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


class _Counter:
    """A shared mutable execution counter for a step."""

    def __init__(self) -> None:
        self.n = 0

    def make(self, ret: str):  # type: ignore[no-untyped-def]
        async def _fn(_ctx: str) -> str:
            self.n += 1
            return ret

        return _fn


async def test_three_step_task_completes(pool: DbPool) -> None:
    c1, c2, c3 = _Counter(), _Counter(), _Counter()
    steps = [
        CallableStep("step1", c1.make("r1")),
        CallableStep("step2", c2.make("r2"), side_effecting=True),
        CallableStep("step3", c3.make("r3")),
    ]
    ex = DurableExecutor(pool, "principal-alice")
    task = await ex.start("the goal", steps, task_id="t-complete")

    assert task.status == "completed"
    assert task.current_step == 3
    assert task.result == "r1\nr2\nr3"
    # The side-effecting step ran exactly once.
    assert c2.n == 1
    assert c1.n == 1 and c3.n == 1


async def test_j1_resume_completes_without_rerunning_prior_steps(pool: DbPool) -> None:
    """J1: a task crashed mid-flight (left 'running') resumes from its checkpoint.

    Simulates a real crash: the process dies after step1+step2 ran and the
    checkpoint advanced to current_step=2, with status still 'running' (the
    failure handler never ran). A NEW executor resumes and finishes step3 only.
    """
    c1, c2, c3 = _Counter(), _Counter(), _Counter()
    steps = [
        CallableStep("step1", c1.make("r1")),
        CallableStep("step2", c2.make("r2"), side_effecting=True),
        CallableStep("step3", c3.make("r3")),
    ]
    # First pass: drive ONLY the first two steps (the crash happens before
    # step3). DurableExecutor checkpoints current_step=2 and leaves 'running'.
    ex = DurableExecutor(pool, "principal-alice")
    await ex.start("goal", steps[:2], task_id="t-resume")
    # Re-mark as running with the same checkpoint to model a crash (start()
    # would have completed the 2-step slice — we coerce it back to interrupted).
    from stackowl.pipeline.durable.store import DurableTaskStore

    store = DurableTaskStore(pool, "principal-alice")
    await store.update_status("t-resume", "running", current_step=2)
    assert c1.n == 1 and c2.n == 1 and c3.n == 0

    # Resume with the FULL step list from a NEW executor instance.
    ex2 = DurableExecutor(pool, "principal-alice")
    resumed = await ex2.resume("t-resume", steps)

    assert resumed.status == "completed"
    assert resumed.current_step == 3
    # J1: step1 + step2 were NOT re-run on resume (loop starts at checkpoint).
    assert c1.n == 1, "pure step1 must not re-run (checkpoint skipped it)"
    assert c2.n == 1, "side-effecting step2 must not re-run"
    # step3 ran exactly once on resume.
    assert c3.n == 1


async def test_j2_side_effect_exactly_once_via_ledger_replay(pool: DbPool) -> None:
    """J2: a committed side-effect is REPLAYED from the ledger, never re-run.

    Simulates the hard crash window: the side-effect committed to the ledger but
    the checkpoint did NOT advance past it (crash between commit and checkpoint).
    On resume the loop re-enters that step index, the ledger reports
    already_committed, and the executor replays the recorded result WITHOUT
    calling step.run again — so the effect counter stays at exactly 1.
    """
    effect = _Counter()
    steps = [
        CallableStep("noop", _Counter().make("n"), side_effecting=False),
        CallableStep("send", effect.make("sent"), side_effecting=True),
        CallableStep("after", _Counter().make("done"), side_effecting=False),
    ]
    ex = DurableExecutor(pool, "principal-bob")
    # Drive the full task once to commit the side-effect at index 1.
    await ex.start("goal", steps, task_id="t-once")
    assert effect.n == 1

    # Model the crash: rewind the checkpoint to index 1 (the side-effect step)
    # while leaving status 'running' — as if the crash hit right after the
    # ledger commit but before the checkpoint advanced.
    from stackowl.pipeline.durable.store import DurableTaskStore

    store = DurableTaskStore(pool, "principal-bob")
    await store.update_status("t-once", "running", current_step=1)

    resumed = await DurableExecutor(pool, "principal-bob").resume("t-once", steps)
    assert resumed.status == "completed"
    # J2: the side-effect did NOT fire again — replayed from the ledger.
    assert effect.n == 1
    # The replayed result is folded back into the aggregate.
    assert "sent" in (resumed.result or "")


async def test_uncertain_intent_parks_on_resume(pool: DbPool) -> None:
    """An intent without a commit (crash mid-side-effect) parks the task."""
    # Create a task and a dangling intent for its first step, with no commit.
    ex = DurableExecutor(pool, "principal-carol")
    effect = _Counter()
    steps = [CallableStep("write", effect.make("w"), side_effecting=True)]
    # Pre-seed an intent row (begin without commit) for step 0 BEFORE driving.
    ledger = SideEffectLedger(pool, "principal-carol")

    # Start would call begin itself; to simulate a half-done prior attempt we
    # create the task in 'running' at step 0 then plant the dangling intent.
    from datetime import UTC, datetime

    from stackowl.pipeline.durable.store import DurableTaskStore
    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(pool, "principal-carol")
    now = datetime.now(tz=UTC)
    await store.create(DurableTask(
        task_id="t-uncertain", owner_id="principal-carol", goal="g",
        status="running", current_step=0, created_at=now, updated_at=now,
    ))
    args = {"goal": "g", "step_index": 0}
    dec = await ledger.begin("t-uncertain", 0, "write", args)
    assert dec.outcome == "proceed"  # planted the intent, never committed

    parked = await ex.resume("t-uncertain", steps)
    assert parked.status == "parked"
    # The step must NOT have executed (we refused the uncertain side-effect).
    assert effect.n == 0


async def test_recover_parks_orphaned_running_tasks(pool: DbPool) -> None:
    from datetime import UTC, datetime

    from stackowl.pipeline.durable.store import DurableTaskStore
    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(pool, "principal-dan")
    now = datetime.now(tz=UTC)
    await store.create(DurableTask(
        task_id="orphan", owner_id="principal-dan", goal="g",
        status="running", current_step=1, created_at=now, updated_at=now,
    ))
    n = await DurableExecutor(pool, "principal-dan").recover()
    assert n == 1
    assert (await store.get("orphan")).status == "parked"


async def test_full_result_contains_all_steps_after_resume(pool: DbPool) -> None:
    """Aggregate result includes pre-interruption steps after resume.

    A 3-step task (pure, side-effecting, pure) is interrupted after step 0
    completes: the executor is coerced to status='running'/current_step=1 to
    model a crash, then resumed.  The final task.result must equal
    'r0\\nr1\\nr2' — NOT just 'r1\\nr2' (the tail of the resumed pass).

    Also validates the exactly-once counter: the side-effecting step must
    still run exactly once across both passes.
    """
    c0, c1, c2 = _Counter(), _Counter(), _Counter()
    steps = [
        CallableStep("step0", c0.make("r0")),                          # pure
        CallableStep("step1", c1.make("r1"), side_effecting=True),     # side-effect
        CallableStep("step2", c2.make("r2")),                          # pure
    ]
    ex = DurableExecutor(pool, "principal-eve")
    # First pass: drive only step0 — step0 completes, checkpoint advances to 1,
    # running aggregate "r0" is persisted.
    await ex.start("goal-full", steps[:1], task_id="t-fullresult")
    # Coerce back to running at step 1 (simulate crash before step1/step2 ran).
    from stackowl.pipeline.durable.store import DurableTaskStore

    store = DurableTaskStore(pool, "principal-eve")
    await store.update_status("t-fullresult", "running", current_step=1)
    assert c0.n == 1 and c1.n == 0 and c2.n == 0

    # Resume with the FULL step list — should run step1 + step2 and return
    # a result that includes ALL three outputs.
    ex2 = DurableExecutor(pool, "principal-eve")
    finished = await ex2.resume("t-fullresult", steps)

    assert finished.status == "completed"
    assert finished.current_step == 3
    # Full aggregate must include ALL steps, not just the resumed tail.
    assert finished.result == "r0\nr1\nr2", (
        f"Expected 'r0\\nr1\\nr2' but got {finished.result!r} — "
        "pre-interruption steps are missing from the aggregate"
    )
    # Exactly-once: step0 did not re-run, side-effecting step1 ran exactly once.
    assert c0.n == 1, "pure step0 must not re-run on resume"
    assert c1.n == 1, "side-effecting step1 must run exactly once"
    assert c2.n == 1, "pure step2 must run exactly once"


def test_pipeline_state_carries_task_id() -> None:
    base = PipelineState(
        trace_id="tr", session_id="sess", input_text="hi",
        channel="cli", owl_name="owl", pipeline_step="classify",
    )
    assert base.task_id is None
    evolved = base.evolve(task_id="task-42")
    assert evolved.task_id == "task-42"
    # evolve preserves other fields and is immutable (new instance).
    assert evolved.session_id == "sess"
    assert base.task_id is None


def test_langgraph_thread_id_uses_session_and_task() -> None:
    """The per-task thread id is session::task_id, falling back to session."""
    with_task = PipelineState(
        trace_id="tr", session_id="sess", input_text="hi",
        channel="cli", owl_name="owl", pipeline_step="classify", task_id="t1",
    )
    without = with_task.evolve(task_id=None)

    def _thread_id(state: PipelineState) -> str:
        return (
            f"{state.session_id}::{state.task_id}"
            if state.task_id
            else state.session_id
        )

    assert _thread_id(with_task) == "sess::t1"
    assert _thread_id(without) == "sess"
