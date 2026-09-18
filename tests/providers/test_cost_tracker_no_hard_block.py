"""DEBT-7 part 1 — the budget signal is INFORMATIVE ONLY.

Bakir's constraint (2026-07-26): the budget alert must never block, gate,
throttle or abort a turn. The subscribers are named ``budget_80pct_alert`` and
``budget_exceeded``, and "exceeded" reads like a limit that should stop
something. It must not — the publisher tells him what a conversation is
costing; the decision about what to do with that stays his.

``CostTracker.record()`` used to raise ``ProviderError`` on every call after the
daily cap was crossed, logging "budget already exceeded — blocking call". That
behaviour was dormant only because no ``budget.daily_limit_usd`` is configured,
so enabling the alert without removing it would have shipped exactly the
failure mode the constraint forbids.

The SOFT per-turn pause (``per_turn_pause_usd``) is untouched and remains the
one mechanism permitted to interrupt — it ASKS the user and never raises.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.cost_tracker import CostTracker


async def test_record_never_raises_once_the_daily_budget_is_exceeded(tmp_db: DbPool) -> None:
    """A call made after the cap is crossed is still recorded, not refused."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=0.01)

    # Spend far past the cap. This arms the exceeded state for today.
    await tracker.record(
        provider_name="acme", model="acme-v1",
        input_tokens=10_000_000, output_tokens=0, duration_ms=1.0, trace_id="t1",
    )
    # The NEXT call is the one that used to raise ProviderError.
    await tracker.record(
        provider_name="acme", model="acme-v1",
        input_tokens=1, output_tokens=1, duration_ms=1.0, trace_id="t2",
    )

    summary = await tracker.daily_total()
    assert summary.call_count == 2  # both recorded; neither was refused


async def test_the_exceeded_event_still_fires(tmp_db: DbPool) -> None:
    """Removing the block must not remove the SIGNAL — that is the whole point
    of DEBT-7. The event is what Bakir asked for; the raise is what he ruled out."""
    bus = EventBus()
    seen: list[object] = []
    bus.subscribe("budget_exceeded", lambda payload: seen.append(payload))
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=0.01)

    await tracker.record(
        provider_name="acme", model="acme-v1",
        input_tokens=10_000_000, output_tokens=0, duration_ms=1.0, trace_id="t1",
    )

    assert len(seen) == 1


async def test_many_calls_past_the_cap_all_succeed(tmp_db: DbPool) -> None:
    """Not just the second call — a conversation that runs well past the cap
    keeps working. A cost signal that silently refused to answer would be a
    worse failure than the missing signal it replaced."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=0.01)

    for i in range(5):
        await tracker.record(
            provider_name="acme", model="acme-v1",
            input_tokens=1_000_000, output_tokens=100, duration_ms=1.0,
            trace_id=f"t{i}",
        )

    summary = await tracker.daily_total()
    assert summary.call_count == 5


async def test_a_budget_warning_opens_a_durable_alert_item(tmp_db: DbPool) -> None:
    """Story 3.1 (AD-28) -- the 80%-crossed warning above is additive: the
    EventBus emit still fires (asserted elsewhere), AND now also durably
    journals ``budget.warning``, which opens an `alert`-kind Needs-you item.
    $15/M input + $75/M output for claude-opus-4-7 (test_story_3_2_3.py's own
    pricing fixture): 900in+900out costs $0.081 -- exactly 81% of a $0.10
    limit, crossing 80% for the first time without also exceeding the cap
    (which would take the OTHER branch)."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=0.10)

    await tracker.record(
        provider_name="anth", model="claude-opus-4-7",
        input_tokens=900, output_tokens=900, duration_ms=1.0, trace_id="t1",
    )

    rows = await tmp_db.fetch_all(
        "SELECT * FROM needs_you WHERE dedupe_key LIKE 'alert:owner:%'", (),
    )
    assert len(rows) == 1
    assert rows[0]["intensity"] == "normal"

    opened_rows = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'needs_you.opened'", (),
    )
    assert len(opened_rows) == 1

    warning_rows = await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'budget.warning'", (),
    )
    assert len(warning_rows) == 1


async def test_a_second_warning_the_same_day_opens_no_duplicate_item(
    tmp_db: DbPool,
) -> None:
    """``_warned_dates`` already deduped the EventBus emit to once per day;
    the durable item must not somehow open twice even if that dedup were
    ever bypassed -- the partial unique index is what actually enforces it."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=0.10)

    await tracker.record(
        provider_name="anth", model="claude-opus-4-7",
        input_tokens=900, output_tokens=900, duration_ms=1.0, trace_id="t1",
    )
    # Force a second _check_budget pass past the in-memory dedup, mirroring
    # how this module's own dedup-bypassing tests reach the warn branch
    # more than once.
    tracker._warned_dates.clear()
    await tracker.record(
        provider_name="anth", model="claude-opus-4-7",
        input_tokens=1, output_tokens=1, duration_ms=1.0, trace_id="t2",
    )

    rows = await tmp_db.fetch_all(
        "SELECT * FROM needs_you WHERE dedupe_key LIKE 'alert:owner:%'", (),
    )
    assert len(rows) == 1, "the partial unique index must allow only ONE open alert"


async def test_a_failing_journal_write_never_costs_the_caller_its_cost_record(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review-pass hardening (verification-gap finding): ``_check_budget``'s
    ``try/except`` around the new journal write is the ONLY thing standing
    between a journal-side failure and reintroducing the exact DEBT-7
    regression this file exists to prevent. Forces the journal write itself
    to raise and proves ``record()`` still completes with the cost row
    intact -- not just the happy path every other test above exercises."""
    import stackowl.providers.cost_tracker as cost_tracker_module

    async def _boom(conn: object, event: object) -> str:
        raise RuntimeError("simulated journal.record failure")

    monkeypatch.setattr(cost_tracker_module, "journal_record", _boom)

    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=0.10)

    # Must not raise, even though the journal write inside _check_budget
    # will. The cost row is the thing DEBT-7 says must never be endangered.
    rec = await tracker.record(
        provider_name="anth", model="claude-opus-4-7",
        input_tokens=900, output_tokens=900, duration_ms=1.0, trace_id="t1",
    )
    assert rec.cost_usd > 0

    summary = await tracker.daily_total()
    assert summary.call_count == 1

    # And, because the journal write failed, no needs_you item exists --
    # proves this test actually engaged the failure path rather than
    # silently no-op'ing past a monkeypatch that never took effect.
    rows = await tmp_db.fetch_all("SELECT * FROM needs_you", ())
    assert rows == []
