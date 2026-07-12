"""Tests for CostTracker.get_turn_token_totals (Epic 3 Task 1 — token-usage-display)."""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.cost_tracker import CostTracker


async def test_sums_multiple_calls_for_same_trace(tmp_db: DbPool) -> None:
    await tmp_db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, "
        "cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "gpt-fast", 100, 20, 0.001, "trace-1", "2026-07-12T00:00:00"),
    )
    await tmp_db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, "
        "cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "gpt-main", 500, 300, 0.01, "trace-1", "2026-07-12T00:00:01"),
    )

    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
    totals = await tracker.get_turn_token_totals("trace-1")

    assert totals == (600, 320)


async def test_no_rows_returns_none(tmp_db: DbPool) -> None:
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    totals = await tracker.get_turn_token_totals("no-such-trace")

    assert totals is None
