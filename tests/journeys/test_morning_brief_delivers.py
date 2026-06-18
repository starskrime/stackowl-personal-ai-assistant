"""C1 / F101 + F109 — MorningBrief actually delivers, honestly, from durable state.

The merge-gate journey: a seeded ``morning_brief`` job, when run by the REAL
scheduler poll on a FRESH process (no live session — the telegram-like adapter's
``_last_chat_id`` is ``None``), results in a REAL transport ``send_text`` to the
DURABLY-resolved target (``chat_id=12345`` from ``target_addresses``), carrying the
rendered brief. And the honest-status half (F109): when the only target channel is
UNRESOLVED, NO ``send_text`` happens and the job result status is NOT ``delivered``.

Mocks ONLY the channel transport (a recording adapter) + there is no AI provider in
the brief path (sections assemble deterministically from the DB). Everything else —
the poller CAS, the DeliverySpec resolver, the DeliveryLedger, the
ProactiveDeliverer seam, the NotificationRouter decision — is the real wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.proactive_job import occurrence_key
from stackowl.notifications.router import NotificationRouter
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.asyncio


class _RecordingTelegramAdapter:
    """A telegram-like adapter: ``send_text`` accepts an explicit ``chat_id``.

    Models a FRESH process — ``_last_chat_id`` is ``None``, so a target-less send
    would reach nobody. The durable target must be threaded through to ``chat_id``.
    """

    def __init__(self, name: str = "telegram") -> None:
        self._name = name
        self._last_chat_id: int | None = None
        self.sends: list[tuple[str, Any]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        # A real telegram adapter would raise/no-op with no chat — model that the
        # explicit target is REQUIRED on a fresh process.
        if chat_id is None and self._last_chat_id is None:
            raise RuntimeError("no chat target (fresh process, _last_chat_id is None)")
        self.sends.append((text, chat_id if chat_id is not None else self._last_chat_id))


def _settings() -> Settings:
    from stackowl.config.settings import BriefSettings, SystemSettings

    return Settings(
        notifications=NotificationSettings(),
        brief=BriefSettings(channels=["telegram"]),
        system=SystemSettings(timezone="UTC"),
    )


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _clean() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()
    yield
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()


def _wire_handler(
    db: DbPool, settings: Settings, adapter: _RecordingTelegramAdapter
) -> tuple[JobScheduler, MorningBriefHandler]:
    ChannelRegistry.instance().register(cast(Any, adapter))
    router = NotificationRouter(db=db, settings=settings)
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings
    )
    ledger = DeliveryLedger(db=db)
    scheduler = JobScheduler(db=db)

    class _StubBridge:
        async def recall(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

        async def list_staged(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

    handler = MorningBriefHandler(
        memory_bridge=cast(Any, _StubBridge()),
        scheduler=scheduler,
        db=db,
        event_bus=EventBus(),
        settings=settings,
        proactive_deliverer=deliverer,
        delivery_ledger=ledger,
    )
    HandlerRegistry.instance().register(handler)
    return scheduler, handler


async def _seed_due_brief_job(
    db: DbPool,
    *,
    target_channels: list[str],
    target_addresses: dict[str, str | int],
) -> Job:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id="morning_brief-fresh01",
        handler_name="morning_brief",
        schedule="daily@08:00",
        idempotency_key="morning_brief:daily",
        last_run_at=None,
        next_run_at=past,
        status="pending",
        target_channels=target_channels,
        target_addresses=target_addresses,
    )
    await insert_job(db, job)
    return job


async def test_fresh_process_delivers_brief_to_durable_target(
    migrated_db: DbPool,
) -> None:
    """The P0 win: a fresh-process poll delivers the brief to the persisted chat.

    No live session, ``_last_chat_id`` is ``None`` — yet the brief reaches
    ``chat_id=12345`` because the recipient is resolved from durable job state.
    """
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler, _handler = _wire_handler(migrated_db, settings, adapter)
    await _seed_due_brief_job(
        migrated_db,
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )

    await scheduler._poll()

    assert len(adapter.sends) == 1, "the brief must reach the durable target exactly once"
    text, chat_id = adapter.sends[0]
    assert chat_id == 12345, "delivered to the durably-resolved chat, not _last_chat_id"
    assert text, "the rendered brief body is transported verbatim"

    rows = await migrated_db.fetch_all(
        "SELECT state FROM delivery_attempts WHERE job_id = ? AND channel = ?",
        ("morning_brief-fresh01", "telegram"),
    )
    assert rows and rows[0]["state"] == "delivered", "ledger records the real success"


async def test_unresolved_target_records_no_delivered(migrated_db: DbPool) -> None:
    """F109: an UNRESOLVED channel never sends and is never recorded delivered."""
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler, _handler = _wire_handler(migrated_db, settings, adapter)
    # Channel listed but NO durable address for it -> undeliverable.
    await _seed_due_brief_job(
        migrated_db,
        target_channels=["telegram"],
        target_addresses={},
    )

    await scheduler._poll()

    assert adapter.sends == [], "nothing is sent when the target is unresolved"
    rows = await migrated_db.fetch_all(
        "SELECT state FROM delivery_attempts WHERE job_id = ?",
        ("morning_brief-fresh01",),
    )
    assert all(r["state"] != "delivered" for r in rows), "never a fake 'delivered'"
    # The job_results rollup must not assert a delivery either.
    res = await migrated_db.fetch_all(
        "SELECT status FROM job_results WHERE job_id = ? ORDER BY run_at DESC LIMIT 1",
        ("morning_brief-fresh01",),
    )
    assert not res or res[0]["status"] != "delivered", "honest status: not delivered"


async def test_replay_of_delivered_occurrence_suppresses_second_send(
    migrated_db: DbPool,
) -> None:
    """F103 exactly-once: a crash-replay of an ALREADY-delivered occurrence sends 0.

    A prior delivery left a ``delivered`` ledger row for this exact
    ``(job_id, occurrence_key, channel)``. When the SAME occurrence is serviced
    again (a crash-replay before ``next_run_at`` advanced), the DeliveryLedger
    claim loses, so the transport is NOT called a second time and the channel is
    accounted as a suppressed replay — never a duplicate proactive message.
    """
    settings = _settings()
    adapter = _RecordingTelegramAdapter()
    scheduler, _handler = _wire_handler(migrated_db, settings, adapter)
    job = await _seed_due_brief_job(
        migrated_db,
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )

    # Seed a pre-existing DELIVERED ledger row for this exact occurrence+channel,
    # standing in for a delivery that completed before a crash/replay.
    ledger = DeliveryLedger(db=migrated_db)
    occ_key = occurrence_key(job)
    won = await ledger.claim_dispatch("morning_brief-fresh01", occ_key, "telegram")
    assert won, "precondition: the seeded claim wins (no row existed yet)"
    await ledger.mark("morning_brief-fresh01", occ_key, "telegram", "delivered")

    # Now the scheduler polls the SAME due occurrence (next_run_at unchanged).
    await scheduler._poll()

    # The seeded delivery was a ledger-only standin (no transport call), so the
    # ONLY way adapter.sends grows is a duplicate send — which must not happen.
    assert len(adapter.sends) == 0, "the already-delivered occurrence must NOT send again"

    rows = await migrated_db.fetch_all(
        "SELECT state FROM delivery_attempts WHERE job_id = ? AND channel = ?",
        ("morning_brief-fresh01", "telegram"),
    )
    assert len(rows) == 1 and rows[0]["state"] == "delivered", (
        "the single delivered row stands; the replay added no row and no send"
    )
