"""Spend with no trace is spend nobody can attribute — ask the ambient context.

WHY THIS EXISTS, and it is a live defect measured 2026-09-06.

`cost_records` holds 130,840 rows and **67,433 of them carry `trace_id = ''`** —
127,097,221 input tokens that cannot be attributed to any turn. Not NULL, so
every query written as `WHERE trace_id IS NOT NULL` silently counted them as
named.

THE SHAPE IS A DEFAULT ARGUMENT. `CostTracker.record()` declares
`trace_id: str = ""`, so a caller that does not pass one is recorded as
belonging to nothing — silently, with no error and no warning. The same is true
of `session_key`, `conversation_id` and `owl_name`, all four blank on exactly
these rows.

WHAT MAKES IT REACH SO FAR. Fourteen classifiers and judges — the router, the
intent classifiers, the achievement judge and writer, the delivery gate, the
critic scorer, the acceptance judge, the shadow validator, evolution — all issue
their model calls through ONE shared helper, `interaction.classifier_base
.safe_complete`, and that helper has no trace parameter at all. So none of them
could pass a trace even if each remembered to. Threading an argument through
fifteen call sites would fix today's fifteen and lose the sixteenth.

MEASURED, the residual is small and real: a cliff on 2026-08-30 took blanks from
33.8% of the day's rows to 3.6%, then 0.4% — an earlier trace-propagation fix.
What survives is ~1-2% a day, every one of them a small `NeraAiRaw` call of
~180-244 input tokens with session and conversation blank too. Correlated
against the log at those timestamps, the writer is the achievement judge: a
`195in/15out` cost line one millisecond before
`turn_achievement_writer.write: criterion written`.

THE MECHANISM THAT KNOWS THE ANSWER ALREADY EXISTS AND WAS NOT ASKED.
`infra.trace.TraceContext` holds an ambient `trace_id` ContextVar — it is what
puts `trace_id` on every log line in the file this defect was measured from — and
it carries `session_key`, `conversation_id` and `owl_name` beside it. The cost
seam simply never consulted it. That is defect shape 5, built but not wired, at
the one seam where the cost of not wiring it is measured in tokens.

SO THE FIX GOES AT THE SEAM, NOT THE CALLERS. An explicit argument still wins —
a caller that knows better is never overridden — but the ABSENCE of one now means
"ask the context", not "record nothing". A helper that returns the right answer
cannot be half-used; a default that means empty can.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.infra.trace import TraceContext
from stackowl.providers.cost_tracker import CostTracker

pytestmark = pytest.mark.asyncio


async def _record(tracker: CostTracker, **over: object) -> None:
    kwargs: dict[str, object] = dict(
        provider_name="NeraAiRaw", model="neraai-v1-raw",
        input_tokens=195, output_tokens=15, duration_ms=2023.9,
    )
    kwargs.update(over)
    await tracker.record(**kwargs)  # type: ignore[arg-type]


async def _row(db: DbPool) -> tuple:
    """The four attribution fields of the single recorded row.

    Read BY KEY. `fetch_all` returns mapping-like rows, so `tuple(row)` yields
    the column NAMES and every assertion below would compare against
    `'trace_id'` — the instrument answering instead of the system, which is the
    same trap that made these rows look attributed in the first place.
    """
    rows = await db.fetch_all(
        "SELECT trace_id, session_key, conversation_id, owl_name FROM cost_records"
    )
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    r = rows[0]
    return (r["trace_id"], r["session_key"], r["conversation_id"], r["owl_name"])


class TestAnUnnamedCallInheritsTheTurnItRanIn:
    async def test_the_ambient_trace_is_used_when_no_argument_is_passed(
        self, tmp_db: DbPool
    ) -> None:
        """THE DEFECT ITSELF. This is the achievement judge's call: it runs inside
        a turn, passes no trace, and was recorded as belonging to nothing."""
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        token = TraceContext.start(
            trace_id="trace-abc", session_key="sess-1", conversation_id="conv-1"
        )
        try:
            await _record(tracker)
        finally:
            TraceContext.reset(token)

        trace_id, session_key, conversation_id, _owl = await _row(tmp_db)
        assert trace_id == "trace-abc", "the spend was not attributed to its turn"
        assert session_key == "sess-1"
        assert conversation_id == "conv-1"

    async def test_an_explicit_argument_still_wins(self, tmp_db: DbPool) -> None:
        """A caller that knows better is never overridden. Delegated children and
        MoA proposers deliberately record under the PARENT's trace so the turn
        ledger sums the whole turn — a context fallback must not fight that."""
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        token = TraceContext.start(trace_id="ambient", session_key="ambient-sess")
        try:
            await _record(tracker, trace_id="explicit", session_key="explicit-sess")
        finally:
            TraceContext.reset(token)

        trace_id, session_key, _c, _o = await _row(tmp_db)
        assert trace_id == "explicit" and session_key == "explicit-sess"

    async def test_outside_any_turn_it_stays_blank_and_does_not_raise(
        self, tmp_db: DbPool
    ) -> None:
        """A genuinely context-free call — a boot probe, a backfill — has no turn
        to name. It must record blank rather than invent one or crash. This is
        also the honest limit of the fix: it attributes what HAS a context."""
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        await _record(tracker)

        trace_id, session_key, conversation_id, _o = await _row(tmp_db)
        assert (trace_id, session_key, conversation_id) == ("", "", "")


class TestTheSharedClassifierPathCanCarryATrace:
    """The reach. Fourteen classifiers issue model calls through ONE helper, so
    the fix has to hold at the helper's seam or it holds for none of them."""

    def test_safe_complete_is_the_single_shared_path(self) -> None:
        """VACUITY CONTROL for the claim above. If this set shrank to a couple of
        modules, 'fix the seam, not the callers' would be an argument about
        nothing."""
        from pathlib import Path

        src = Path("src/stackowl")
        users = {
            p.relative_to(src).as_posix()
            for p in src.rglob("*.py")
            if "safe_complete" in p.read_text(encoding="utf-8")
            and p.name != "classifier_base.py"
        }

        assert len(users) >= 10, f"only {len(users)} modules share the helper: {users}"
        assert "interaction/turn_achievement_judge.py" in users, (
            "the module the live blank rows were traced to is no longer on this path"
        )
