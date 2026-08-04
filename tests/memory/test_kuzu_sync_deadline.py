"""DEBT-19 — kuzu_sync must RETURN, not run to completion.

Measured on the live box before this fix:
  * 27,780 facts unmirrored, ~828 synced/day → ~34 days of backlog
  * kuzu_sync started 42-49x/day; contradiction/promotion/pruning 1-5x
  * 47 "handler timed out" ERRORs/day

The handler was not failing to PRODUCE — it synced ~828 facts a day. It was
failing to RETURN, which pinned the dream worker's checkpoint on this phase and
starved every phase after it.
"""

from __future__ import annotations

import time

import pytest

from stackowl.memory.kuzu_sync_handler import (
    KuzuSyncJobHandler,
    _sync_budget_s,
)
from stackowl.scheduler.job import Job


def _job() -> Job:
    return Job(
        job_id="t", handler_name="kuzu_sync", schedule="manual",
        idempotency_key="t", last_run_at=None, next_run_at="2026-01-01T00:00:00Z", status="pending",
    )


class _Db:
    def __init__(self, n: int) -> None:
        self._rows = [{"fact_id": f"f{i}", "content": f"c{i}"} for i in range(n)]
        self.executed: list = []

    async def fetch_all(self, sql, params=None):
        return self._rows[: (params[0] if params else len(self._rows))]

    async def execute(self, sql, params=None):
        self.executed.append(params)


class _SlowExtractor:
    """Each extract burns wall-clock, like the real per-fact LLM call."""

    def __init__(self, seconds: float) -> None:
        self._s = seconds
        self.calls = 0

    async def extract(self, content, fact_id):
        self.calls += 1
        time.sleep(self._s)
        return []


class _Kuzu:
    """Method names taken from the handler, not guessed — a stub missing
    upsert_fact_node makes every fact 'fail' and the test passes for the wrong
    reason (it did, on the first run)."""

    async def upsert_fact_node(self, *a, **k): return None
    async def upsert_entity(self, *a, **k): return None
    async def link_fact_to_entity(self, *a, **k): return None


def test_the_budget_is_read_from_the_scheduler_timeout():
    """So a retune follows automatically instead of silently going stale — the
    exact rot that produced DEBT-19."""
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    assert 0 < _sync_budget_s() < float(_HANDLER_TIMEOUT_SEC)


def test_the_budget_is_smaller_than_minings():
    """This is the LAST dream-worker phase, so it inherits whatever the earlier
    phases already spent."""
    from stackowl.memory.dream_worker import _mining_budget_s

    assert _sync_budget_s() < _mining_budget_s()


@pytest.mark.asyncio
async def test_a_slow_batch_RETURNS_instead_of_running_to_completion(monkeypatch):
    """THE FIX. Before, this loop ran all 50 facts however long it took and the
    handler was killed by the scheduler mid-batch."""
    monkeypatch.setattr(
        "stackowl.memory.kuzu_sync_handler._sync_budget_s", lambda: 0.05
    )
    db = _Db(50)
    ex = _SlowExtractor(0.02)
    h = KuzuSyncJobHandler(_Kuzu(), ex, db, batch_size=50)

    result = await h.execute(_job())

    assert result.success, "a deferred batch is SUCCESS, not failure"
    assert ex.calls < 50, f"the batch was not bounded — {ex.calls} facts processed"
    assert "deferred=" in (result.output or "")


@pytest.mark.asyncio
async def test_a_fast_batch_still_completes_every_fact(monkeypatch):
    """The budget must not truncate work that fits — the backlog has to drain."""
    monkeypatch.setattr(
        "stackowl.memory.kuzu_sync_handler._sync_budget_s", lambda: 60.0
    )
    db = _Db(5)
    ex = _SlowExtractor(0.0)
    h = KuzuSyncJobHandler(_Kuzu(), ex, db, batch_size=5)

    result = await h.execute(_job())

    assert result.success
    assert ex.calls == 5
    assert "deferred=" not in (result.output or "")


@pytest.mark.asyncio
async def test_deferred_facts_stay_unmirrored_for_the_next_tick(monkeypatch):
    """Deferring must not mark a fact synced — otherwise the backlog would be
    'drained' by skipping it, which is worse than the timeout."""
    monkeypatch.setattr(
        "stackowl.memory.kuzu_sync_handler._sync_budget_s", lambda: 0.05
    )
    db = _Db(50)
    ex = _SlowExtractor(0.02)
    h = KuzuSyncJobHandler(_Kuzu(), ex, db, batch_size=50)

    await h.execute(_job())

    assert len(db.executed) == ex.calls, (
        "a sync-log row was written for a fact that was never extracted"
    )


# --------------------------------------------------------------------------- #
# The REGRESSION I nearly shipped: a fixed share ignores how much of the window
# the earlier phases actually used.
#
# Measured live with a fixed 0.33 share: synced=8, deferred=42, elapsed=403.8s of
# a 1200s window — ~800s left unused. That halved backfill throughput to ~384
# facts/day against an arrival rate of ~419/day, which would have turned a
# shrinking backlog into a growing one.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_explicit_budget_overrides_the_default_share():
    db = _Db(50)
    ex = _SlowExtractor(0.01)
    h = KuzuSyncJobHandler(_Kuzu(), ex, db, batch_size=50)

    await h.execute(_job(), budget_s=0.03)
    few = ex.calls

    ex.calls = 0
    db2 = _Db(50)
    h2 = KuzuSyncJobHandler(_Kuzu(), ex, db2, batch_size=50)
    await h2.execute(_job(), budget_s=10.0)

    assert ex.calls > few, "a larger budget must process more facts"


def test_the_dream_worker_gives_kuzu_sync_the_REMAINING_time():
    """Not a fixed fraction. kuzu_sync is the last phase, so its budget is
    whatever the earlier work did not use."""
    import time as _t

    from stackowl.memory.dream_worker import (
        _KUZU_MIN_BUDGET_S,
        _RETURN_MARGIN_S,
        DreamWorkerJobHandler,
    )
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    h = object.__new__(DreamWorkerJobHandler)

    # Fresh run: nearly the whole window is still available.
    h._handler_started = _t.monotonic()
    fresh = h._remaining_budget_s()
    assert fresh > float(_HANDLER_TIMEOUT_SEC) * 0.5, (
        f"a fresh run should get most of the window, got {fresh}"
    )
    assert fresh <= float(_HANDLER_TIMEOUT_SEC) - _RETURN_MARGIN_S

    # Late run: most of the window is gone.
    h._handler_started = _t.monotonic() - (float(_HANDLER_TIMEOUT_SEC) - 30.0)
    late = h._remaining_budget_s()
    assert late == _KUZU_MIN_BUDGET_S, "a late run still gets the floor, not zero"


def test_a_standalone_call_falls_back_to_the_safe_default():
    """The scheduler calls execute(job) with no budget; there is no run clock."""
    from stackowl.memory.dream_worker import (
        _KUZU_FALLBACK_BUDGET_S,
        DreamWorkerJobHandler,
    )

    h = object.__new__(DreamWorkerJobHandler)
    assert h._remaining_budget_s() == _KUZU_FALLBACK_BUDGET_S
