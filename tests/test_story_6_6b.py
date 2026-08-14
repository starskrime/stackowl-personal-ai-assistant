"""The DreamWorker seat — registered, unscheduled, and deliberately empty.

WHAT THIS FILE USED TO BE. Twelve tests over the nightly consolidation pass:
full-pass completion, checkpoint rows, resume-from-promotion,
resume-from-pruning, stale-run restart, phase-persisted-before-work,
kuzu-phase invocation, idempotency across two runs. All of it exercised the five
fact phases, and all five were fact work over a store that has held zero rows
since D08.1's migration 0112.

WHAT SURVIVED, and why it is not simply a deletion. D08.1 UNSCHEDULED this
handler rather than deleting it (migration 0113) so **N01 Dreaming** — Bakir's
own idea, outside the reference map — would have somewhere to land. The seat is
the point, so the seat gets tests: it is registered under the right name, it
takes no fact-store dependencies any more, and it reports honestly that it has
nothing to run rather than returning a silent success that would look identical
to a working pass.

One test is carried over almost verbatim from the old file — `handler_name`
returns "dream_worker" — because that string is what the scheduler's job row
keys on. If it drifts, N01 inherits a seat nothing can reach.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stackowl.memory.dream_worker import DreamWorkerJobHandler
from stackowl.scheduler.job import Job


def _job(job_id: str = "dw-1") -> Job:
    return Job(
        job_id=job_id,
        handler_name="dream_worker",
        schedule="every 30m",
        idempotency_key="dream_worker",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


def test_the_seat_keeps_its_handler_name() -> None:
    """T17, carried over: the scheduler's job row keys on this exact string.

    If it drifts, the registered handler and the `dream_worker` job stop
    matching and N01 inherits a seat nothing can reach.
    """
    assert DreamWorkerJobHandler().handler_name == "dream_worker"


def test_the_seat_takes_no_fact_store_dependencies() -> None:
    """It used to require bridge, promoter, pruner, kuzu_handler and detector.

    All five were phase dependencies. A seat that still demanded them would
    force N01 to satisfy a fact pipeline that no longer exists.
    """
    handler = DreamWorkerJobHandler()  # no arguments — the assertion IS the call
    assert handler is not None


def test_the_seat_still_defers_under_load() -> None:
    """Whatever N01 puts here will be a background pass, so yielding to live
    turns is the safe default to inherit."""
    assert DreamWorkerJobHandler().defer_under_load is True


@pytest.mark.asyncio
async def test_an_empty_pass_reports_itself_rather_than_succeeding_silently() -> None:
    """NOT a silent success.

    An empty pass that logged nothing and returned success would be
    indistinguishable from a working one — the write-with-no-effect shape this
    programme keeps finding. The result says how many phases ran, and classifies
    itself read_only because it touches nothing.
    """
    result = await DreamWorkerJobHandler().execute(_job())

    assert result.success is True
    assert result.effect_class == "read_only"
    assert result.metadata["phases_run"] == 0
    assert "no phases" in (result.output or "")
