"""DEBT-32 — the dream worker could never finish, and never will without a bound.

THE DEFECT, measured on the live database 2026-07-29:

  * `mine_all` mines EVERY session with staged conversation turns, one LLM call
    each at 15-35s observed.
  * There were 928 such sessions — 5 to 9 HOURS of work — against the scheduler's
    1200s handler timeout.
  * So the run was killed every time, at 30 consecutive failures and climbing.

And it is a RATCHET, not just a slow job. `_mine()` runs BEFORE `_run_phases`,
and the pruning phase is what DELETEs from staged_facts. Mining never finished,
so the phases never ran, so the backlog never drained, so mining could never
finish. Each run burned ~$1.50 of LLM calls re-mining the same first ~60 sessions
and produced nothing.

THE FIX IS A TIME BUDGET, NOT A SESSION CAP. An invented "mine N per run" would
be exactly the arbitrary numeric limit this codebase spent an arc removing. The
real constraint is the handler deadline, so mining is bounded BY that deadline and
defers the rest to the next run — which now happens, because the pass completes.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.conversation_miner import ConversationMiner
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio


class _SlowExtractor:
    """Each extraction costs real time, like the live LLM call it stands in for."""

    def __init__(self, seconds: float) -> None:
        self.calls: list[str] = []
        self._seconds = seconds

    async def extract(self, messages: list[object], session_key: str) -> list[StagedFact]:
        import asyncio

        self.calls.append(session_key)
        await asyncio.sleep(self._seconds)
        return [StagedFact(
            content=f"fact about {session_key}", source_type="conversation_fact",
            source_ref=session_key, confidence=0.9,
        )]


async def _stage_sessions(db: DbPool, n: int) -> None:
    bridge = SqliteMemoryBridge(db)
    for i in range(n):
        await bridge.stage(StagedFact(
            content=f"user said something in session {i}",
            source_type="conversation", source_ref=f"lane-{i}", confidence=1.0,
        ))


async def test_mining_stops_when_its_time_budget_is_spent(tmp_db: DbPool) -> None:
    """The core fix: a backlog larger than the budget no longer kills the run."""
    await _stage_sessions(tmp_db, 6)
    extractor = _SlowExtractor(seconds=0.05)
    miner = ConversationMiner(tmp_db, extractor, SqliteMemoryBridge(tmp_db))  # type: ignore[arg-type]

    await miner.mine_all(budget_s=0.12)

    assert len(extractor.calls) < 6, (
        "mining ignored its budget and processed the whole backlog — the exact "
        "behaviour that made the handler time out on every run"
    )
    assert extractor.calls, "mining should still make progress, not stop at zero"


async def test_mining_without_a_budget_still_processes_everything(tmp_db: DbPool) -> None:
    """Default is unchanged: no budget means the historical behaviour."""
    await _stage_sessions(tmp_db, 4)
    extractor = _SlowExtractor(seconds=0.0)
    miner = ConversationMiner(tmp_db, extractor, SqliteMemoryBridge(tmp_db))  # type: ignore[arg-type]

    await miner.mine_all()

    assert len(extractor.calls) == 4


async def test_deferred_sessions_are_reported_not_silent(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent cap reads as 'finished'. The whole reason this defect survived 30
    runs is that nothing said what was left undone."""
    await _stage_sessions(tmp_db, 6)
    extractor = _SlowExtractor(seconds=0.05)
    miner = ConversationMiner(tmp_db, extractor, SqliteMemoryBridge(tmp_db))  # type: ignore[arg-type]

    with caplog.at_level("INFO"):
        await miner.mine_all(budget_s=0.12)

    assert any("deferred" in r.message.lower() for r in caplog.records), (
        "the run must say how much backlog it left behind"
    )


async def test_consecutive_runs_make_FORWARD_PROGRESS(tmp_db: DbPool) -> None:
    """The second half of DEBT-32, and the one that nearly got missed.

    Bounding the run stopped the timeout, but nothing removes a session from
    mine_all's own work queue — the only caller of clear_session is /reset. So an
    unordered SELECT DISTINCT returns the same rows every time and a budgeted run
    re-mines the SAME first N sessions forever, never reaching the rest. The job
    stops failing while still making no progress, which is arguably worse: it
    looks healthy.

    Sessions are therefore ordered by when they were LAST MINED, never-mined
    first. That guarantees progress without losing reinforcement — every session
    comes back round eventually.
    """
    await _stage_sessions(tmp_db, 6)
    extractor = _SlowExtractor(seconds=0.05)
    miner = ConversationMiner(tmp_db, extractor, SqliteMemoryBridge(tmp_db))  # type: ignore[arg-type]

    await miner.mine_all(budget_s=0.12)
    first_pass = set(extractor.calls)
    extractor.calls.clear()

    await miner.mine_all(budget_s=0.12)
    second_pass = set(extractor.calls)

    assert second_pass, "the second run mined nothing at all"
    assert second_pass - first_pass, (
        f"the second run re-mined only sessions the first already did "
        f"({sorted(second_pass)}) — no forward progress, so the backlog can "
        f"never drain however many times the job runs"
    )


class _BarrenExtractor:
    """Finds nothing durable — the case that broke the first forward-progress fix.

    Most conversations yield no lasting fact. Ordering the queue by facts
    PRODUCED therefore leaves these sessions with no timestamp at all, so they
    sort first forever and are re-mined every run at full LLM cost for nothing.
    Verified live: 4 of 5 sessions in one run had zero staged AND zero committed
    facts. The queue must record the ATTEMPT, not the outcome.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, messages: list[object], session_key: str) -> list[StagedFact]:
        self.calls.append(session_key)
        return []


async def test_a_barren_session_still_leaves_the_queue(tmp_db: DbPool) -> None:
    """The defect the previous fix missed, because its stub always produced a fact."""
    await _stage_sessions(tmp_db, 3)
    extractor = _BarrenExtractor()
    miner = ConversationMiner(tmp_db, extractor, SqliteMemoryBridge(tmp_db))  # type: ignore[arg-type]

    await miner.mine_all()
    first = list(extractor.calls)
    extractor.calls.clear()

    await miner.mine_all()

    assert first, "the first pass mined nothing at all"
    assert not extractor.calls, (
        f"a second pass re-mined {extractor.calls} — sessions that yield no facts "
        f"never leave the queue, so they are re-mined forever at full LLM cost"
    )


async def test_marking_mined_does_not_hide_turns_from_context(tmp_db: DbPool) -> None:
    """The risk this approach carries, pinned.

    Mining flips the conversation rows' status. Every context read of
    source_type='conversation' is status-agnostic today (verified before
    choosing this approach) — this test is what stops someone adding a
    status filter later and silently deleting conversation memory.
    """
    await _stage_sessions(tmp_db, 1)
    bridge = SqliteMemoryBridge(tmp_db)
    miner = ConversationMiner(tmp_db, _BarrenExtractor(), bridge)  # type: ignore[arg-type]

    before = await bridge.recent_conversation_turns("lane-0", limit=10)
    await miner.mine_all()
    after = await bridge.recent_conversation_turns("lane-0", limit=10)

    assert before, "fixture staged no turns"
    assert len(after) == len(before), (
        "mining hid conversation turns from context — the model would silently "
        "lose the history it had before"
    )
