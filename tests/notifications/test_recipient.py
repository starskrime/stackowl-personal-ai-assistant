"""DeliverySpec resolver (C1 / F101-F104) — recipient from DURABLE job state.

Given a cron-born Job, resolve the [(channel, native_target)] pairs from the
job's persisted ``target_channels`` / ``target_addresses`` columns — NOT from a
live session or request context (there is none at poll time). An unresolved
channel yields NO pair; the caller records it loudly as undeliverable, never
``delivered`` and never touches telegram's ``_last_chat_id``.
"""

from __future__ import annotations

from stackowl.notifications.recipient import DeliverySpec
from stackowl.scheduler.job import Job


def _job(**over: object) -> Job:
    base: dict[str, object] = dict(
        job_id="j1",
        handler_name="morning_brief",
        schedule="daily@08:00",
        idempotency_key="k1",
        last_run_at=None,
        next_run_at="2026-01-01T08:00:00+00:00",
        status="pending",
    )
    base.update(over)
    return Job(**base)  # type: ignore[arg-type]


def test_resolves_telegram_int_from_durable_state() -> None:
    job = _job(target_channels=["telegram"], target_addresses={"telegram": 12345})
    pairs = DeliverySpec.from_job(job).pairs()
    assert pairs == [("telegram", 12345)]


def test_resolves_slack_str_from_durable_state() -> None:
    job = _job(target_channels=["slack"], target_addresses={"slack": "C0ABC"})
    pairs = DeliverySpec.from_job(job).pairs()
    assert pairs == [("slack", "C0ABC")]


def test_resolves_multi_channel_each_native_type() -> None:
    job = _job(
        target_channels=["telegram", "slack"],
        target_addresses={"telegram": 999, "slack": "C9"},
    )
    pairs = DeliverySpec.from_job(job).pairs()
    assert ("telegram", 999) in pairs
    assert ("slack", "C9") in pairs
    assert len(pairs) == 2


def test_no_durable_target_yields_no_pairs() -> None:
    """A legacy/customer job with no targets resolves nothing (caller -> undeliverable)."""
    job = _job()
    spec = DeliverySpec.from_job(job)
    assert spec.pairs() == []


def test_channel_listed_but_address_missing_is_unresolved() -> None:
    """A channel with no address yields no pair (loud undeliverable upstream), not a guess."""
    job = _job(target_channels=["telegram", "slack"], target_addresses={"telegram": 7})
    pairs = DeliverySpec.from_job(job).pairs()
    assert pairs == [("telegram", 7)]
    unresolved = DeliverySpec.from_job(job).unresolved_channels()
    assert unresolved == ["slack"]
