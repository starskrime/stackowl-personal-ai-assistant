"""Tests for Learning Commit 5 — ToolHeuristicStore + ToolOutcomeMiner +
HeuristicMatcher."""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.learning.heuristic_matcher import (
    _extract_error_class,
    match_and_emit,
)
from stackowl.learning.lessons_index import LessonDraft
from stackowl.learning.tool_heuristic_store import (
    ToolHeuristicStore,
    heuristic_summary,
)
from stackowl.learning.tool_outcome_miner import ToolOutcomeMiner
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.tools.base import ToolResult

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


async def test_miner_writes_heuristic_when_evidence_threshold_met(
    tmp_db: DbPool,
) -> None:
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
    assert report.n_heuristics_written == 1
    rows = await heur_store.find_for_tool("web_fetch", min_evidence=3)
    assert len(rows) == 1
    assert rows[0].condition_value == "ToolTimeoutError"
    assert rows[0].predicted_outcome == "fails"


async def test_miner_skips_below_threshold(tmp_db: DbPool) -> None:
    await _seed_outcomes_with_failures(tmp_db, n_fail=2, n_success=0)
    miner = ToolOutcomeMiner(
        outcome_store=TaskOutcomeStore(tmp_db),
        heuristic_store=ToolHeuristicStore(tmp_db),
        lessons_index=None, min_evidence=3,
    )
    report = await miner.mine()
    assert report.n_heuristics_written == 0


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
    # Seed 3 failures with known quality scores (all 0.4) so mean_quality == 0.4
    store = TaskOutcomeStore(tmp_db)
    for i in range(3):
        tid = f"mq-fail-{i}"
        await store.record(
            trace_id=tid, session_id="s", owl_name="scout", channel="cli",
            success=False, latency_ms=3000.0, tool_call_count=1,
            failure_class="ToolTimeoutError", step_durations={},
            input_text=f"task {i}", response_text="(error)",
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


async def test_matcher_emits_event_when_failure_matches(tmp_db: DbPool) -> None:
    store = ToolHeuristicStore(tmp_db)
    await store.upsert(
        tool_name="web_fetch", condition_kind="failure_class",
        condition_value="ToolTimeoutError", predicted_outcome="fails",
        evidence_count=10, mean_quality=0.2, failure_class="ToolTimeoutError",
    )
    bus = EventBus()
    received: list[Any] = []
    bus.subscribe("tool.heuristic_match", received.append)

    failed = ToolResult(
        success=False, output="",
        error="ToolTimeoutError: web_fetch timed out", duration_ms=5000.0,
    )
    await match_and_emit(
        tool_name="web_fetch", tool_result=failed,
        heuristic_store=store, event_bus=bus,
    )
    assert len(received) == 1
    assert received[0]["tool_name"] == "web_fetch"
    assert received[0]["failure_class"] == "ToolTimeoutError"
    assert received[0]["evidence_count"] == 10


async def test_matcher_silent_when_no_heuristic_matches(tmp_db: DbPool) -> None:
    bus = EventBus()
    received: list[Any] = []
    bus.subscribe("tool.heuristic_match", received.append)
    failed = ToolResult(
        success=False, output="",
        error="ValueError: bad arg", duration_ms=10.0,
    )
    await match_and_emit(
        tool_name="never_used_tool", tool_result=failed,
        heuristic_store=ToolHeuristicStore(tmp_db), event_bus=bus,
    )
    assert received == []


async def test_matcher_tolerates_none_stores() -> None:
    """No store or no bus → silent no-op (test mode)."""
    failed = ToolResult(success=False, output="", error="X: y", duration_ms=10.0)
    await match_and_emit(
        tool_name="x", tool_result=failed,
        heuristic_store=None, event_bus=EventBus(),
    )
    await match_and_emit(
        tool_name="x", tool_result=failed,
        heuristic_store=None, event_bus=None,
    )
    # No exception = pass
