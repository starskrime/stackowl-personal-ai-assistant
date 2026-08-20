"""A task that still owes a delivery is not declared complete when the drive returns.

BAKIR, 2026-08-17: *"a task is complete when its outcome reached its DESTINATION,
not when the function returned."* ``DurableTaskRunner._finalize`` broke that rule
inside the runner that drives every durable task.

THE SEQUENCE, MEASURED LIVE 2026-08-20 across four warnings in one day::

    task_runner._drive       _finalize(task_id, "completed")   <- the drive returned
    goal_execution           _deliver_answer -> complete_agent_task -> mark_delivered

The row therefore reached ``completed`` with ``delivered_at`` NULL and the platform
warned about it on the HAPPY path, so the counter could never separate a real gap
from ordinary operation — D08.1's unfalsifiable-check shape, again.

AND THE STATE IT LEAVES BEHIND IS UNREACHABLE. ``revive_undelivered_failures``
scans ``status='failed'``. A row sitting at ``completed`` with no proof is visible
to no sweep at all, so a crash between the drive returning and the delivery landing
turns a merely-unproven answer into a permanently lost one. Two such rows were on
the live table when this was written.

WHY NOT SIMPLY REQUEUE IT. Because re-driving a task whose answer already reached
the user answers them twice, and 4f8e8db5 exists because a duplicate flood is worse
than an unproven record. So the runner does the one honest thing available to it:
it declines to claim a success it cannot prove and leaves the row ``running`` for
the delivery path, which already computes the honest verdict. If delivery never
happens, the row is still open and the loop's existing recovery owns it — exactly
once, through the claim it already has.

A TASK WITH NO DESTINATION IS UNAFFECTED. A sweep or an internal sub-task has
nobody waiting, so "completed" is the whole truth for it, and that path must stay
byte-identical.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio

_OWNER = "principal-default"


class _Backend:
    """Returns a state carrying an answer and no errors — a clean drive."""

    def __init__(self, *, errors: tuple[str, ...] = (), parked: bool = False) -> None:
        self._errors = errors
        self._parked = parked

    async def run(self, state: PipelineState) -> PipelineState:
        return state.evolve(
            responses=[
                ResponseChunk(
                    content="the answer is 42", is_final=False, chunk_index=0,
                    trace_id=state.trace_id, owl_name=state.owl_name,
                )
            ],
            errors=list(self._errors),
            durable_parked=self._parked,
        )


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "owed_delivery.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _seed(
    store: DurableTaskStore, task_id: str, *, destination: str | None
) -> None:
    await store.enqueue(
        DurableTask(
            task_id=task_id,
            owner_id=_OWNER,
            goal="answer the question",
            status="running",
            trigger_kind="chat" if destination else "subgoal",
            destination=destination,
        )
    )


def _state(task_id: str) -> PipelineState:
    return PipelineState(
        trace_id=task_id,
        session_key="owl:secretary:telegram:dm:72055773",
        input_text="answer the question",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="",
    )


class TestTheRunnerWillNotClaimAnUnprovenSuccess:
    async def test_a_task_with_a_destination_stays_running_until_delivery(
        self, pool: DbPool
    ) -> None:
        """The whole bug in one assertion: the drive returned, nothing has been
        delivered, so the row must NOT read completed."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-owes", destination="telegram:72055773")

        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-owes", state=_state("t-owes")
        )

        task = await store.get("t-owes")
        assert task.status == "running", "declared complete with no delivery proof"
        assert task.delivered_at is None

    async def test_the_delivery_path_then_completes_it(self, pool: DbPool) -> None:
        """And the row is still there to be completed — leaving it running must not
        strand it. ``mark_delivered`` is the transition that closes it."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-owes", destination="telegram:72055773")

        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-owes", state=_state("t-owes")
        )
        await store.mark_delivered("t-owes", result="the answer is 42")

        task = await store.get("t-owes")
        assert task.status == "completed"
        assert task.delivered_at is not None

    async def test_a_task_already_proven_delivered_is_untouched(
        self, pool: DbPool
    ) -> None:
        """The chat path delivers INSIDE the pipeline, so the proof lands before the
        runner finalizes. The existing terminal-status guard must keep that a no-op
        rather than rewriting a delivered row."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-done", destination="telegram:72055773")
        await store.mark_delivered("t-done", result="already sent")
        before = await store.get("t-done")

        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-done", state=_state("t-done")
        )

        after = await store.get("t-done")
        assert after.status == "completed"
        assert after.delivered_at == before.delivered_at
        assert after.result == "already sent"


class TestEveryOtherEndingIsUnchanged:
    async def test_a_task_with_no_destination_still_completes(
        self, pool: DbPool
    ) -> None:
        """A sweep or internal sub-task has nobody waiting, so "completed" IS the
        whole truth. This path must be byte-identical or every housekeeping task on
        the platform is stranded running."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-internal", destination=None)

        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-internal", state=_state("t-internal")
        )

        assert (await store.get("t-internal")).status == "completed"

    async def test_a_parked_task_is_still_parked(self, pool: DbPool) -> None:
        """Only terminal SUCCESS is withheld. A park is a real, honest outcome and
        the row must reach it whether or not anything was delivered."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-parked", destination="telegram:72055773")

        await DurableTaskRunner(store, _Backend(parked=True)).resume(
            task_id="t-parked", state=_state("t-parked")
        )

        assert (await store.get("t-parked")).status == "parked"

    async def test_a_failed_task_still_returns_to_the_loop(self, pool: DbPool) -> None:
        """A failure goes back to pending WITH what failed — the loop's contract,
        unchanged by this fix."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-failed", destination="telegram:72055773")

        await DurableTaskRunner(store, _Backend(errors=("the tool exploded",))).resume(
            task_id="t-failed", state=_state("t-failed")
        )

        task = await store.get("t-failed")
        assert task.status == "pending"
        assert task.last_error and "exploded" in task.last_error


class TestTheRowLeftOpenIsRecoveredExactlyOnce:
    """The fix moves a crash window from invisible to recoverable — and the whole
    point is that recovering it must not answer the user twice.

    Before: a crash between the drive returning and delivery landing left the row
    at ``completed`` with ``delivered_at`` NULL, which NO sweep scans
    (``revive_undelivered_failures`` reads ``status='failed'``). The answer was
    lost silently.

    After: the row is ``running``, which is exactly what the startup orphan sweep
    claims. The claim is a compare-and-set, so concurrent claimers produce exactly
    one winner — the property 4f8e8db5 exists to protect.
    """

    async def test_the_open_row_is_claimable_by_recovery(self, pool: DbPool) -> None:
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-crash", destination="telegram:72055773")
        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-crash", state=_state("t-crash")
        )
        # ... the process dies here, before mark_delivered.

        assert await store.claim_for_recovery("t-crash") is True

    async def test_the_open_row_keeps_a_fresh_updated_at(self, pool: DbPool) -> None:
        """NEVER TWICE, and this is the assertion that buys it.

        The periodic ``TaskLivenessSweepHandler`` reclaims root ``running`` rows
        whose ``updated_at`` is older than ``DEFAULT_STALE_AFTER_S`` (600s). The
        declined completion is still a WRITE, so it stamps ``updated_at`` — the row
        left open for delivery is a freshly-touched one, and delivery lands in
        milliseconds. Were the write skipped entirely instead, a row already close
        to the threshold could be swept while its answer was in flight and the user
        would be answered twice, which is the failure 4f8e8db5 exists to prevent.
        """
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-fresh", destination="telegram:72055773")
        before = await store.get("t-fresh")

        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-fresh", state=_state("t-fresh")
        )

        after = await store.get("t-fresh")
        assert after.updated_at >= before.updated_at
        # And the produced answer is on the row, so nothing is lost by not
        # declaring it complete.
        assert after.result == "the answer is 42"

    async def test_a_delivered_row_is_never_reclaimed(self, pool: DbPool) -> None:
        """Once the proof lands the row is terminal, and the sweep that hunts
        orphans must not be able to pull it back and re-send the answer."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-sent", destination="telegram:72055773")
        await DurableTaskRunner(store, _Backend()).resume(
            task_id="t-sent", state=_state("t-sent")
        )
        await store.mark_delivered("t-sent", result="the answer is 42")

        assert await store.claim_for_recovery("t-sent") is False


class TestNothingToDeliverIsStillDone:
    """A REGRESSION THE FIX WOULD OTHERWISE HAVE CAUSED, caught by reading the
    callers rather than by a test going red.

    ``goal_execution`` lets a scheduled goal answer with the ``NO_NOTIFY_NEEDED``
    sentinel when its condition is not met; the handler blanks ``response_text``
    and ``_deliver_answer`` returns ("completed", False) — "empty answer, nothing
    to deliver". ``complete_agent_task`` then returns early on an empty result and
    never calls ``mark_delivered``.

    So a watch-style job that correctly had nothing to say would be declined a
    terminal status, sit ``running``, be reclaimed by the liveness sweep after
    600s, and grind. The row's achievement — "the answer is delivered to the job's
    targets" — is VACUOUS when there is no answer, and a task with nothing to
    deliver has delivered everything it owed.

    The decline therefore turns on an answer EXISTING, not merely on a destination
    existing.
    """

    async def test_a_completion_with_no_answer_is_terminal(
        self, pool: DbPool
    ) -> None:
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-quiet", destination="telegram:72055773")

        await store.update_status("t-quiet", "completed", result=None)

        assert (await store.get("t-quiet")).status == "completed"

    async def test_a_completion_with_a_blank_answer_is_terminal(
        self, pool: DbPool
    ) -> None:
        """Whitespace is not an answer either — the delivery seams both test with
        ``.strip()``, so this must agree with them or the two drift."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-blank", destination="telegram:72055773")

        await store.update_status("t-blank", "completed", result="   \n ")

        assert (await store.get("t-blank")).status == "completed"

    async def test_an_actual_answer_is_still_declined(self, pool: DbPool) -> None:
        """The carve-out must not swallow the bug it sits next to."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-real", destination="telegram:72055773")

        await store.update_status("t-real", "completed", result="the answer is 42")

        assert (await store.get("t-real")).status == "running"


class TestTheLogSaysWhatActuallyHappened:
    """OBSERVED ON THE LIVE RUN THAT PROVED THIS FIX, 2026-08-20::

        22:45:10 [loop] the drive finished but the answer has not reached its
                 destination yet — leaving the task open for the delivery path
        22:45:10 [tasks] runner._finalize: finalized {"status": "completed"}

    The second line is false. The row was left ``running``; ``_finalize`` logs the
    status it REQUESTED, because ``update_status`` told it nothing. Two lines apart
    and they contradict each other, and only one of them is true — which is the
    same defect as the stream-miss line that claimed "answer delivered" while
    printing status=failed. A log that disagrees with the database sends the next
    debugging session the wrong way.

    So the write reports what it wrote, and the caller logs that.
    """

    async def test_update_status_reports_the_status_it_actually_wrote(
        self, pool: DbPool
    ) -> None:
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-report", destination="telegram:72055773")

        written = await store.update_status(
            "t-report", "completed", result="the answer is 42",
        )

        assert written == "running", "reported a completion it declined to make"
        assert (await store.get("t-report")).status == "running"

    async def test_an_ordinary_completion_reports_itself(self, pool: DbPool) -> None:
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-plain", destination=None)

        written = await store.update_status("t-plain", "completed", result="done")

        assert written == "completed"

    async def test_a_failure_reports_where_the_loop_put_it(self, pool: DbPool) -> None:
        """A failure does not stay 'failed' — the chokepoint returns it to pending
        with what broke. The caller's log must say pending, not failed."""
        store = DurableTaskStore(pool, _OWNER)
        await _seed(store, "t-fail", destination="telegram:72055773")

        written = await store.update_status("t-fail", "failed", result="it exploded")

        assert written == "pending"
        assert (await store.get("t-fail")).status == "pending"
