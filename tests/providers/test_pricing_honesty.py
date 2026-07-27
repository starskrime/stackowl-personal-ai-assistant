"""DEBT-15 — a fallback-derived cost must not present itself as a price.

`neraai-v1-raw`, the only enabled model on this deployment, is absent from
pricing.yaml. Every cost figure it produces therefore comes from the
conservative `unknown_cloud_per_1m_usd` fallback (15.0/1M by default), and the
platform prints it as `$0.000225` — indistinguishable from a real price. 1531
`UNKNOWN CLOUD MODEL` warnings landed in a single day.

That matters in four places, not one:
  * the `[cost]` log line, which reads as a measurement
  * `cost_records.cost_usd`, which D01.6's metric 3 aggregates — its captured
    baseline headline of "$2.85 for five messages" is fallback-derived
  * `turn_cost_usd` -> `pipeline/budget/governor.py` -> `BudgetBreach`, so a
    SOFT per-turn pause can interrupt the user on placeholder dollars
  * the conversation cost report added for DEBT-7

Bakir's decision (2026-07-26): make the FALLBACK honest. Do not invent prices —
the real per-token rates are not known here and must not be guessed.

The distinction these tests pin: a LOCAL model priced at $0 is a real answer
(self-hosted really is free), while an unknown CLOUD model's figure is a guess.
Only the second is "estimated".
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.cost_tracker import CostTracker
from stackowl.providers.pricing.loader import PricingLoader

KNOWN_CLOUD = "gpt-4o"          # present in pricing.yaml
UNKNOWN_CLOUD = "neraai-v1-raw"  # absent — the live model that surfaced this


def test_a_model_in_the_table_is_priced() -> None:
    assert PricingLoader().is_priced(KNOWN_CLOUD) is True


def test_an_unknown_cloud_model_is_not_priced() -> None:
    """The figure exists and is deliberately conservative, but it is a GUESS."""
    assert PricingLoader().is_priced(UNKNOWN_CLOUD) is False


def test_an_unknown_local_model_IS_priced() -> None:
    """A self-hosted backend really is free, so $0 is a real answer rather than
    a placeholder. Marking it "estimated" would cry wolf on every local call."""
    assert PricingLoader().is_priced("some-local-llama", is_local=True) is True


def test_the_estimate_itself_is_unchanged() -> None:
    """Honesty is a LABEL on the number, not a change to it. The conservative
    fallback still bills high so an unpriced paid model can never silently
    read $0 (F128) — that guarantee must survive this."""
    loader = PricingLoader()
    cost = loader.estimate(UNKNOWN_CLOUD, 1_000_000, 0)
    assert cost > 0, "an unpriced CLOUD model must never silently bill $0"


async def test_an_unpriced_call_is_recorded_as_estimated(tmp_db: DbPool) -> None:
    """Persisted, because D01.6 aggregates these rows. A SUM over a mix of real
    and guessed dollars cannot be made honest after the fact unless each row
    says which it was."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    await tracker.record(
        provider_name="NeraAiRaw", model=UNKNOWN_CLOUD,
        input_tokens=1000, output_tokens=100, duration_ms=1.0, trace_id="t1",
    )

    rows = await tmp_db.fetch_all("SELECT priced FROM cost_records")
    assert [r["priced"] for r in rows] == [0]


async def test_a_priced_call_is_recorded_as_priced(tmp_db: DbPool) -> None:
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    await tracker.record(
        provider_name="openai", model=KNOWN_CLOUD,
        input_tokens=1000, output_tokens=100, duration_ms=1.0, trace_id="t1",
    )

    rows = await tmp_db.fetch_all("SELECT priced FROM cost_records")
    assert [r["priced"] for r in rows] == [1]


async def test_history_written_before_this_change_stays_unknown(tmp_db: DbPool) -> None:
    """A pre-migration row's provenance is genuinely unknown, so the column is
    NULL rather than a confident 0 or 1. Backfilling either way would invent a
    fact — the same mistake this whole debt is about."""
    await tmp_db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, "
        "output_tokens, cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("legacy", "whatever", 1, 1, 0.01, "old", "2026-07-01T00:00:00"),
    )

    rows = await tmp_db.fetch_all("SELECT priced FROM cost_records")
    assert rows[0]["priced"] is None


# --- the user-facing surface: DEBT-7's conversation cost report --------------


async def test_the_conversation_report_hedges_an_estimated_total(tmp_db: DbPool) -> None:
    """This message can be DELIVERED to the user over Telegram, so it is the
    surface where a fabricated-looking precision does the most damage. A total
    built from unpriced calls must not read as a receipt."""
    import asyncio

    from stackowl.providers.conversation_cost_report import (
        COST_REPORT_EVENT,
        register_conversation_cost_consumer,
    )
    from stackowl.sessions.store import SessionStore

    bus = EventBus()
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=None)
    await tracker.record(
        provider_name="NeraAiRaw", model=UNKNOWN_CLOUD,
        input_tokens=1000, output_tokens=100, duration_ms=1.0, trace_id="t1",
        session_key="lane", session_id="INC_A",
    )
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda p: seen.append(p))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT,
             {"session_key": "lane", "old_session_id": "INC_A"})
    await asyncio.sleep(0.3)

    assert len(seen) == 1
    assert seen[0]["estimated"] is True
    assert "approx" in seen[0]["message"].lower()


async def test_a_fully_priced_conversation_reports_plainly(tmp_db: DbPool) -> None:
    """The hedge must be earned. A conversation on priced models states its cost
    without qualification — otherwise the caveat becomes noise everyone ignores."""
    import asyncio

    from stackowl.providers.conversation_cost_report import (
        COST_REPORT_EVENT,
        register_conversation_cost_consumer,
    )
    from stackowl.sessions.store import SessionStore

    bus = EventBus()
    tracker = CostTracker(db=tmp_db, event_bus=bus, daily_limit_usd=None)
    await tracker.record(
        provider_name="openai", model=KNOWN_CLOUD,
        input_tokens=1000, output_tokens=100, duration_ms=1.0, trace_id="t1",
        session_key="lane", session_id="INC_A",
    )
    seen: list[dict] = []
    bus.subscribe(COST_REPORT_EVENT, lambda p: seen.append(p))
    register_conversation_cost_consumer(bus, tracker)

    bus.emit(SessionStore.ROLLOVER_EVENT,
             {"session_key": "lane", "old_session_id": "INC_A"})
    await asyncio.sleep(0.3)

    assert len(seen) == 1
    assert seen[0]["estimated"] is False
    assert "approx" not in seen[0]["message"].lower()
