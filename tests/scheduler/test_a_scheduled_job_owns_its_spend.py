"""A scheduled job's model calls must be attributable to that job.

MEASURED 2026-08-29 against the live cost_records table::

    all calls          123,648  /  643,822,541 input tokens
    BLANK trace_id      67,383 (54.5%)  /  127,088,340 (19.7%)
    ...and blank session_key AND blank conversation_id on every one of them.

CORRECTED IN THE SAME MEASUREMENT, because the all-time figure overstates today:
in the last 24h blank-trace calls are 33.6% of calls but only **4.0% of tokens**
(1,212,988 of 30,670,413). The 19.7% is dominated by history, not current
behaviour. This is attribution work, not a spend emergency.

WHO THEY ARE. Correlated by timestamp against the log: background scheduler
handlers — critic, reflection_writer, learning/lessons, entity_extractor,
rollover_summary — which call `provider.complete(...)` directly and never
construct a PipelineState. `_record_cost` reads trace_id off TraceContext, and
nothing ever set one for them, so a fifth of all recorded spend is attributable to
nothing at all.

THE FIX IS BUILT-BUT-NOT-WIRED, again. `TraceContext.start()` says so in its own
docstring: *"When trace_id is None we mint a fresh UUID — useful for background
jobs/scheduler handlers that start their own root trace."* The scheduler never
calls it. There are exactly 4 callers of `TraceContext.start` in src/ and none is
the scheduler.

THE PRECEDENT IS AT THE SAME LINE. `_run_job` already binds `retry_ledger` there,
and its comment makes precisely this argument: *"a scheduled job never constructs a
PipelineState/goes through backend.run() UNLESS its handler itself does ... so
retry_ledger would otherwise never be bound for those handlers' own provider calls
... Binding HERE, at the one central dispatch point every handler funnels through,
covers all of them with one change."* Same site, same reason, same shape.

WHAT IS DELIBERATELY NOT SET, and it is a recorded decision this must not violate.
`trace.py` on `conversation_id`: *"None is a real answer: background work that
never passed through ingress has a lane but no incarnation, and inventing one would
attribute its cost to a conversation that never happened."* So conversation_id
stays None. A job gets a trace (a unit of work) and a lane (`job:<job_id>`), never
a conversation.

WHY A FRESH TRACE PER RUN AND NOT THE job_id ITSELF. job_id is stable across every
run of a recurring job. Using it as trace_id would fold a daily job's entire
lifetime into one accumulating total, and the new per-turn token ceiling reads that
total — a recurring job would eventually breach a cap it never earned.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.infra.trace import TraceContext


class _Job:
    def __init__(self, job_id: str = "reflection_writer-ee748779") -> None:
        self.job_id = job_id
        self.handler_name = "reflection_writer"
        self.retry_count = 0


class _CapturingHandler:
    """Records the TraceContext its handler body actually observes."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def execute(self, job: Any) -> Any:
        self.seen.append(dict(TraceContext.get()))

        class _R:
            job_id = job.job_id
            success = True
            output = None
            error = None
            duration_ms = 1.0
            verified = None
        return _R()


@pytest.mark.asyncio
async def test_the_handler_runs_inside_a_trace() -> None:
    """The defect: nothing set one, so every provider call recorded trace_id ''."""
    from stackowl.scheduler.scheduler import _bind_job_trace

    handler = _CapturingHandler()
    job = _Job()
    token = _bind_job_trace(job)
    try:
        await handler.execute(job)
    finally:
        TraceContext.reset(token)

    seen = handler.seen[0]
    assert seen.get("trace_id"), (
        "the handler ran with no trace_id — its model calls are recorded against "
        "nothing and cannot be attributed to the job that made them"
    )


@pytest.mark.asyncio
async def test_the_lane_names_the_JOB() -> None:
    """Attribution needs to survive to a query, not just exist."""
    from stackowl.scheduler.scheduler import _bind_job_trace

    handler = _CapturingHandler()
    job = _Job()
    token = _bind_job_trace(job)
    try:
        await handler.execute(job)
    finally:
        TraceContext.reset(token)

    assert job.job_id in str(handler.seen[0].get("session_key") or ""), (
        f"session_key {handler.seen[0].get('session_key')!r} does not name the job"
    )


@pytest.mark.asyncio
async def test_conversation_id_stays_None() -> None:
    """A RECORDED DECISION, not a preference — see trace.py.

    "background work that never passed through ingress has a lane but no
    incarnation, and inventing one would attribute its cost to a conversation that
    never happened."
    """
    from stackowl.scheduler.scheduler import _bind_job_trace

    handler = _CapturingHandler()
    token = _bind_job_trace(_Job())
    try:
        await handler.execute(_Job())
    finally:
        TraceContext.reset(token)

    assert handler.seen[0].get("conversation_id") in (None, ""), (
        "a background job invented a conversation it never had"
    )


@pytest.mark.asyncio
async def test_each_RUN_gets_its_own_trace() -> None:
    """Two runs of one recurring job must not share a trace.

    job_id is stable for the life of the job. Reusing it as the trace would fold a
    daily job's entire history into one accumulating total — and the per-turn token
    ceiling reads exactly that total, so a recurring job would eventually breach a
    cap it never earned.
    """
    from stackowl.scheduler.scheduler import _bind_job_trace

    handler = _CapturingHandler()
    job = _Job()
    for _ in range(2):
        token = _bind_job_trace(job)
        try:
            await handler.execute(job)
        finally:
            TraceContext.reset(token)

    a, b = handler.seen[0]["trace_id"], handler.seen[1]["trace_id"]
    assert a != b, f"both runs of {job.job_id} shared trace {a}"


@pytest.mark.asyncio
async def test_the_context_does_not_LEAK_to_the_next_job() -> None:
    """A ContextVar left set would attribute job B's spend to job A."""
    from stackowl.scheduler.scheduler import _bind_job_trace

    before = dict(TraceContext.get())
    token = _bind_job_trace(_Job())
    TraceContext.reset(token)
    after = dict(TraceContext.get())
    assert after.get("trace_id") == before.get("trace_id")
    assert after.get("session_key") == before.get("session_key")


@pytest.mark.asyncio
async def test_a_handler_that_starts_its_OWN_trace_still_wins() -> None:
    """goal_execution runs a real turn and binds its own; nesting must isolate.

    The same nested-bind semantics retry_ledger already relies on at this site.
    """
    from stackowl.scheduler.scheduler import _bind_job_trace

    outer = _bind_job_trace(_Job())
    try:
        inner = TraceContext.start(session_key="owl:secretary:telegram:dm:1",
                                   trace_id="turn-trace")
        try:
            assert TraceContext.get()["trace_id"] == "turn-trace"
        finally:
            TraceContext.reset(inner)
        assert TraceContext.get()["trace_id"] != "turn-trace", (
            "the job trace did not come back after the inner turn finished"
        )
    finally:
        TraceContext.reset(outer)
