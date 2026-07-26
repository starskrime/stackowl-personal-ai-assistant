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
