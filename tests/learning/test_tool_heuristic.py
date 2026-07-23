"""Tests for Learning Commit 5 — ToolHeuristicStore + ToolOutcomeMiner +
HeuristicMatcher."""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.heuristic_matcher import _extract_error_class
from stackowl.learning.lessons_index import LessonDraft
from stackowl.learning.tool_heuristic_store import (
    ToolHeuristicStore,
    heuristic_summary,
)
from stackowl.learning.tool_outcome_miner import ToolOutcomeMiner
from stackowl.memory.outcome_store import TaskOutcomeStore

# ---------- ToolHeuristicStore CRUD ----------------------------------------


async def test_heuristic_store_upsert_then_list(tmp_db: DbPool) -> None:
    store = ToolHeuristicStore(tmp_db)
    hid = await store.upsert(
        tool_name="web_fetch", condition_kind="failure_class",
        condition_value="ToolTimeoutError", predicted_outcome="fails",
        evidence_count=5, mean_quality=0.3, failure_class="ToolTimeoutError",
    )
    assert hid >= 1
    rows = await store.find_for_tool("web_fetch", min_evidence=1)
    assert len(rows) == 1
    assert rows[0].evidence_count == 5
    assert rows[0].predicted_outcome == "fails"


async def test_heuristic_store_upsert_is_idempotent_on_key(tmp_db: DbPool) -> None:
    store = ToolHeuristicStore(tmp_db)
    await store.upsert(
        tool_name="shell", condition_kind="failure_class",
        condition_value="PermissionError", predicted_outcome="fails",
        evidence_count=3,
    )
    await store.upsert(
        tool_name="shell", condition_kind="failure_class",
        condition_value="PermissionError", predicted_outcome="fails",
        evidence_count=7, mean_quality=0.1,
    )
    rows = await store.find_for_tool("shell", min_evidence=1)
    assert len(rows) == 1
    assert rows[0].evidence_count == 7  # second upsert wins


async def test_heuristic_store_min_evidence_filter(tmp_db: DbPool) -> None:
    store = ToolHeuristicStore(tmp_db)
    await store.upsert(
        tool_name="tx", condition_kind="failure_class",
        condition_value="X", predicted_outcome="fails", evidence_count=2,
    )
    await store.upsert(
        tool_name="tx", condition_kind="failure_class",
        condition_value="Y", predicted_outcome="fails", evidence_count=8,
    )
    assert len(await store.find_for_tool("tx", min_evidence=5)) == 1


def test_heuristic_summary_human_readable() -> None:
    from stackowl.learning.tool_heuristic_store import ToolHeuristic

    h = ToolHeuristic(
        heuristic_id=1, tool_name="web_fetch",
        condition_kind="failure_class", condition_value="ToolTimeoutError",
        predicted_outcome="fails", evidence_count=12, mean_quality=0.2,
        failure_class="ToolTimeoutError",
        last_seen_at=0.0, created_at=0.0, updated_at=0.0,
    )
    s = heuristic_summary(h)
    assert "web_fetch" in s
    assert "ToolTimeoutError" in s
    assert "evidence=12" in s


# ---------- ToolOutcomeMiner ----------------------------------------------


async def _seed_outcomes_with_failures(
    db: DbPool, *, n_fail: int = 3, n_success: int = 1,
    failure_class: str = "ToolTimeoutError", tool: str = "web_fetch",
) -> None:
    store = TaskOutcomeStore(db)
    for i in range(n_fail):
        tid = f"fail-{tool}-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=False, latency_ms=5000.0, tool_call_count=1,
            failure_class=failure_class, step_durations={},
            input_text=f"task {i}", response_text="(error)",
            tool_sequence=(tool,),
        )
        o = await store.get_by_trace_id(tid)
        await store.set_quality_score(o.outcome_id, 0.2)
    for i in range(n_success):
        tid = f"ok-{tool}-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=True, latency_ms=200.0, tool_call_count=1,
            failure_class=None, step_durations={},
            input_text=f"task {i}", response_text="done",
            tool_sequence=(tool,),
        )
        o = await store.get_by_trace_id(tid)
        await store.set_quality_score(o.outcome_id, 0.9)


async def test_miner_skips_failures_positive_only(
    tmp_db: DbPool,
) -> None:
    # POSITIVE-ONLY LEARNING: failures are never mined into a heuristic — the
    # platform never learns "tool X fails under Y".
    await _seed_outcomes_with_failures(tmp_db, n_fail=4, n_success=0)
    heur_store = ToolHeuristicStore(tmp_db)
    miner = ToolOutcomeMiner(
        outcome_store=TaskOutcomeStore(tmp_db),
        heuristic_store=heur_store,
        lessons_index=None,
        min_evidence=3,
    )
    report = await miner.mine()
    assert report.n_outcomes_scanned == 4
    assert report.n_heuristics_written == 0  # failures yield no heuristic
    rows = await heur_store.find_for_tool("web_fetch", min_evidence=3)
    assert rows == []


async def test_miner_skips_below_threshold(tmp_db: DbPool) -> None:
    await _seed_outcomes_with_failures(tmp_db, n_fail=2, n_success=0)
    miner = ToolOutcomeMiner(
        outcome_store=TaskOutcomeStore(tmp_db),
        heuristic_store=ToolHeuristicStore(tmp_db),
        lessons_index=None, min_evidence=3,
    )
    report = await miner.mine()
    assert report.n_heuristics_written == 0


async def test_miner_skips_disliked_approach_positive_only(tmp_db: DbPool) -> None:
    """PATHFINDER-2026-07-22 Proposal 3 gap fix: a Disliked (approach_rating=
    "negative") outcome must be excluded from tool-heuristic mining too, not
    just from DNA attribution — a user explicitly rejecting the approach is
    a clear "this was not a genuine positive" signal, shared via
    outcome_store.is_positive_signal (this miner's own filter used to skip
    only failure_class, missing this check entirely)."""
    store = TaskOutcomeStore(tmp_db)
    for i in range(3):
        tid = f"disliked-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=True, latency_ms=200.0, tool_call_count=1,
            failure_class=None, step_durations={},
            input_text=f"task {i}", response_text="done",
            tool_sequence=("web_fetch",),
        )
        o = await store.get_by_trace_id(tid)
        await store.set_quality_score(o.outcome_id, 0.9)
        await store.set_approach_rating(trace_id=tid, rating="negative")

    heur_store = ToolHeuristicStore(tmp_db)
    miner = ToolOutcomeMiner(
        outcome_store=store, heuristic_store=heur_store,
        lessons_index=None, min_evidence=3,
    )
    report = await miner.mine()

    assert report.n_heuristics_written == 0
    assert await heur_store.find_for_tool("web_fetch", min_evidence=3) == []


async def test_miner_writes_success_heuristic_too(tmp_db: DbPool) -> None:
    """High-frequency successes also produce heuristics (predicted_outcome='succeeds')."""
    await _seed_outcomes_with_failures(tmp_db, n_fail=0, n_success=5)
    heur_store = ToolHeuristicStore(tmp_db)
    miner = ToolOutcomeMiner(
        outcome_store=TaskOutcomeStore(tmp_db),
        heuristic_store=heur_store, lessons_index=None, min_evidence=3,
    )
    await miner.mine()
    rows = await heur_store.find_for_tool("web_fetch", min_evidence=3)
    assert len(rows) == 1
    assert rows[0].condition_value == "succeeded"
    assert rows[0].predicted_outcome == "succeeds"


async def test_miner_heuristic_lesson_metadata_includes_mean_quality(
    tmp_db: DbPool,
) -> None:
    """Heuristic LessonDraft.metadata must carry both evidence_count and mean_quality."""
    # POSITIVE-ONLY: seed 3 SUCCESSES with known quality scores (all 0.4) so
    # mean_quality == 0.4 — the miner learns only from what worked.
    store = TaskOutcomeStore(tmp_db)
    for i in range(3):
        tid = f"mq-ok-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=True, latency_ms=300.0, tool_call_count=1,
            failure_class=None, step_durations={},
            input_text=f"task {i}", response_text="(done)",
            tool_sequence=("web_fetch",),
        )
        o = await store.get_by_trace_id(tid)
        await store.set_quality_score(o.outcome_id, 0.4)

    # Capturing fake — implements only what miner.mine() calls on the lessons side.
    class _CapturingIndex:
        def __init__(self) -> None:
            self.captured: list[LessonDraft] = []

        async def publish_many(self, drafts: list[LessonDraft]) -> int:
            self.captured.extend(drafts)
            return len(drafts)

    capturing = _CapturingIndex()
    miner = ToolOutcomeMiner(
        outcome_store=store,
        heuristic_store=ToolHeuristicStore(tmp_db),
        lessons_index=capturing,  # type: ignore[arg-type]
        min_evidence=3,
    )
    await miner.mine()

    heuristic_drafts = [
        d for d in capturing.captured if d.source_type == "tool_heuristic"
    ]
    assert len(heuristic_drafts) >= 1, "expected at least one heuristic lesson draft"
    draft = heuristic_drafts[0]
    assert "evidence_count" in draft.metadata
    assert "mean_quality" in draft.metadata
    assert draft.metadata["mean_quality"] == pytest.approx(0.4)


# ---------- HeuristicMatcher ----------------------------------------------


def test_extract_error_class_parses_exception_prefix() -> None:
    assert _extract_error_class("ToolTimeoutError: request hung") == "ToolTimeoutError"
    assert _extract_error_class("ValueError: bad input") == "ValueError"
    assert _extract_error_class("tool failed unexpectedly") == "tool_error"
    assert _extract_error_class(None) == ""
    assert _extract_error_class("") == ""
