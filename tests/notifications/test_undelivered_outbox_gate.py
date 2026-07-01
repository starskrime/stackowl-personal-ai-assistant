"""PA5(b) gate — the undelivered_outbox silent-delivery ratchet.

Asserts the 3 silent-drop seams (deliverer transport-failed, router suppressed,
morning-brief no-deliverer) now write a durable `undelivered_outbox` row instead
of dropping the body, that the row surfaces exactly once as a next-contact
banner, and that paths with their OWN correct recovery (F-62 pending job,
quiet-hours/batched deferral) do NOT create outbox rows — proving the seams
fire only on genuine silent drops. Every assertion reads the STORE back (DB
read-back), never a mock/log assertion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.digest_job import NotificationDigestJob
from stackowl.notifications.proactive_job import ProactiveJobDeliverer
from stackowl.notifications.router import Notification, NotificationRouter
from stackowl.notifications.undelivered_outbox import (
    ALLOWED_REASONS,
    MAX_BANNER_BODY_CHARS,
    UndeliveredOutbox,
    render_banner,
)
from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import assemble
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler_helpers import insert_job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


def _settings(default_channel: str = "cli") -> Settings:
    ns = SimpleNamespace(
        notifications=NotificationSettings(default_channel=default_channel)
    )
    return cast(Settings, ns)


class _AlwaysFailAdapter:
    """A channel adapter whose send_text always raises (permanent transport failure)."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:
        raise RuntimeError("transport permanently unavailable")


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


async def test_transport_failed_writes_durable_row_with_body_and_reason(
    tmp_db: DbPool,
) -> None:
    """deliverer.py terminal 'failed' (retry+reroute exhausted) → durable NACK."""
    TestModeGuard.deactivate()
    ChannelRegistry.instance().register(_AlwaysFailAdapter("cli"))

    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    deliverer = ProactiveDeliverer(
        router=router,
        registry=ChannelRegistry.instance(),
        settings=_settings(),
        outbox=UndeliveredOutbox(tmp_db),
    )
    note = Notification(
        message="you have a meeting at 3pm",
        urgency="critical",  # bypasses quiet/focus routing — always 'delivered' decision
        category="reminder",
        target=555444,
    )
    status = await deliverer.deliver(note)
    assert status == "failed"

    rows = await tmp_db.fetch_all(
        "SELECT identity_key, body, reason, channel FROM undelivered_outbox", ()
    )
    assert len(rows) == 1
    assert rows[0]["reason"] == "transport_failed"
    assert rows[0]["body"] == "you have a meeting at 3pm"
    assert rows[0]["identity_key"] == "555444"
    assert rows[0]["channel"] == "cli"


async def test_suppressed_router_path_writes_durable_row_with_body(
    tmp_db: DbPool,
) -> None:
    """router.py 'suppressed' branch → durable NACK (body retained, not just a hash)."""
    TestModeGuard.deactivate()
    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    router.set_focus_mode("hard")  # hard focus + low urgency → suppressed
    note = Notification(
        message="fyi: low priority update",
        urgency="low",
        category="fyi",
        target=777888,
    )
    decision = await router.deliver(note)
    assert decision == "suppressed"

    rows = await tmp_db.fetch_all(
        "SELECT identity_key, body, reason FROM undelivered_outbox WHERE reason = 'suppressed'",
        (),
    )
    assert len(rows) == 1
    assert rows[0]["body"] == "fyi: low priority update"
    assert rows[0]["identity_key"] == "777888"


async def test_next_contact_banner_surfaces_once_then_clears(tmp_db: DbPool) -> None:
    """Seed a pending row → drive an inbound turn → banner shows once, surfaced_at set."""
    outbox = UndeliveredOutbox(tmp_db)
    ok = await outbox.record_undelivered(
        identity_key="turn-user-1",
        body="the thing you asked me to send earlier",
        reason="transport_failed",
        channel="cli",
        category="reminder",
        urgency="normal",
        job_id=None,
    )
    assert ok is True

    set_services(StepServices(db_pool=tmp_db))
    state = PipelineState(
        trace_id="t1",
        session_id="turn-user-1",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="assemble",
    )
    out = await assemble.run(state)
    assert "the thing you asked me to send earlier" in (out.system_prompt or "")

    row = (
        await tmp_db.fetch_all(
            "SELECT surfaced_at FROM undelivered_outbox WHERE identity_key = ?",
            ("turn-user-1",),
        )
    )[0]
    assert row["surfaced_at"] is not None

    # A second turn for the same identity must NOT re-surface it.
    out2 = await assemble.run(state)
    assert "the thing you asked me to send earlier" not in (out2.system_prompt or "")


async def test_delegated_child_turn_does_not_surface_banner(tmp_db: DbPool) -> None:
    """A delegated (non-top-level) turn must never surface the banner."""
    outbox = UndeliveredOutbox(tmp_db)
    await outbox.record_undelivered(
        identity_key="turn-user-2",
        body="should not surface on a delegated turn",
        reason="suppressed",
        channel="cli",
        category="c",
        urgency="normal",
        job_id=None,
    )
    set_services(StepServices(db_pool=tmp_db))
    state = PipelineState(
        trace_id="t2",
        session_id="turn-user-2",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="assemble",
        delegation_depth=1,
    )
    out = await assemble.run(state)
    assert "should not surface on a delegated turn" not in (out.system_prompt or "")
    row = (
        await tmp_db.fetch_all(
            "SELECT surfaced_at FROM undelivered_outbox WHERE identity_key = ?",
            ("turn-user-2",),
        )
    )[0]
    assert row["surfaced_at"] is None  # left pending for a future real turn


async def test_f62_pending_job_does_not_create_outbox_row(tmp_db: DbPool) -> None:
    """F-62: a handler-not-registered job self-recovers via 'pending' — no NACK row."""
    HandlerRegistry.reset()
    from stackowl.scheduler.scheduler import JobScheduler

    sched = JobScheduler(db=tmp_db, handler_registry=HandlerRegistry.instance())
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    job = Job(
        job_id=f"late-{uuid.uuid4().hex[:6]}",
        handler_name="not_registered_handler",
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=past,
        status="pending",
    )
    await insert_job(tmp_db, job)
    await sched._poll()

    rows = await tmp_db.fetch_all("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,))
    assert rows[0]["status"] == "pending"  # self-recovers, not a NACK
    outbox_rows = await tmp_db.fetch_all("SELECT id FROM undelivered_outbox", ())
    assert outbox_rows == []
    HandlerRegistry.reset()


async def test_quiet_hours_batched_deferral_does_not_create_outbox_row(
    tmp_db: DbPool,
) -> None:
    """A batched (quiet-hours/focus-deferred) notification already has its own
    correct recovery (notification_queue + digest flush) — no outbox row."""
    TestModeGuard.deactivate()
    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    router.set_focus_mode("soft")  # any non-critical urgency → batched, never suppressed
    note = Notification(
        message="deferred to the digest window",
        urgency="normal",
        category="c",
        target=333222,
    )
    decision = await router.deliver(note)
    assert decision == "batched"

    queue_rows = await tmp_db.fetch_all("SELECT message FROM notification_queue", ())
    assert len(queue_rows) == 1  # its own durable recovery path

    outbox_rows = await tmp_db.fetch_all("SELECT id FROM undelivered_outbox", ())
    assert outbox_rows == []


def _scheduled_job(
    job_id: str, target_channels: list[str], target_addresses: dict[str, str | int]
) -> Job:
    return Job(
        job_id=job_id,
        handler_name="morning_brief",
        schedule="daily@08:00",
        idempotency_key=f"key-{job_id}",
        last_run_at=None,
        next_run_at="2026-06-30T08:00:00Z",
        status="pending",
        target_channels=target_channels,
        target_addresses=target_addresses,
    )


async def test_undeliverable_rollup_writes_durable_row(tmp_db: DbPool) -> None:
    """PB7b gap (c): an unresolvable channel is a silent drop (success=True,
    zero seams) unless the shared `deliver_for_job` chokepoint records it."""
    TestModeGuard.deactivate()
    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    deliverer = ProactiveDeliverer(
        router=router,
        registry=ChannelRegistry.instance(),
        settings=_settings(),
        outbox=UndeliveredOutbox(tmp_db),
    )
    job_deliverer = ProactiveJobDeliverer(deliverer, DeliveryLedger(tmp_db))
    job = _scheduled_job("brief-undeliverable-1", ["telegram"], {})

    outcome = await job_deliverer.deliver_for_job(
        job, message="brief body", category="morning_brief"
    )
    assert outcome.rollup == "undeliverable"

    rows = await UndeliveredOutbox(tmp_db).list_pending()
    assert len(rows) == 1
    assert rows[0]["reason"] == "undeliverable"
    assert rows[0]["body"] == "brief body"
    assert rows[0]["channel"] == "telegram"
    assert rows[0]["job_id"] == job.job_id


async def test_undeliverable_channel_alongside_resolvable_no_double_write(
    tmp_db: DbPool,
) -> None:
    """One resolvable (failing) + one unresolvable channel -> exactly one row
    per reason, no duplication between the deliverer seam and the chokepoint."""
    TestModeGuard.deactivate()
    ChannelRegistry.instance().register(_AlwaysFailAdapter("cli"))
    router = NotificationRouter(
        db=tmp_db, settings=_settings(), clock=lambda: datetime(2026, 6, 30, tzinfo=UTC)
    )
    deliverer = ProactiveDeliverer(
        router=router,
        registry=ChannelRegistry.instance(),
        settings=_settings(),
        outbox=UndeliveredOutbox(tmp_db),
    )
    job_deliverer = ProactiveJobDeliverer(deliverer, DeliveryLedger(tmp_db))
    job = _scheduled_job(
        "brief-mixed-1", ["telegram", "cli"], {"cli": 999}
    )

    outcome = await job_deliverer.deliver_for_job(
        job, message="mixed body", category="morning_brief", urgency="critical"
    )
    assert outcome.undeliverable == ("telegram",)

    rows = await tmp_db.fetch_all(
        "SELECT reason, channel FROM undelivered_outbox ORDER BY reason", ()
    )
    assert len(rows) == 2
    reasons = {(r["reason"], r["channel"]) for r in rows}
    assert reasons == {("transport_failed", "cli"), ("undeliverable", "telegram")}


async def test_undeliverable_reason_ratchet() -> None:
    """The vocabulary must land before the write — the ratchet asserts it's there."""
    assert "undeliverable" in ALLOWED_REASONS


async def test_undeliverable_reason_accepted_not_skipped(tmp_db: DbPool) -> None:
    ok = await UndeliveredOutbox(tmp_db).record_undelivered(
        identity_key=DEFAULT_PRINCIPAL_ID,
        body="a scheduled body with no recipient",
        reason="undeliverable",
        channel="telegram",
        category="morning_brief",
        urgency="normal",
        job_id="job-1",
    )
    assert ok is True


async def test_undeliverable_row_surfaces_once_pa5b_parity(tmp_db: DbPool) -> None:
    """Mirror the PB7a gate parity test freshly for the scheduled-job path:
    write -> list_pending read-back -> mark_surfaced -> second read empty."""
    outbox = UndeliveredOutbox(tmp_db)
    ok = await outbox.record_undelivered(
        identity_key=DEFAULT_PRINCIPAL_ID,
        body="undeliverable scheduled body",
        reason="undeliverable",
        channel="telegram",
        category="morning_brief",
        urgency="normal",
        job_id="job-2",
    )
    assert ok is True

    pending = await outbox.list_pending()
    assert len(pending) == 1
    row_id = pending[0]["id"]

    await outbox.mark_surfaced([row_id])

    pending_after = await outbox.list_pending()
    assert pending_after == []


class _AlwaysFailDeliverer:
    """A digest-injected deliverer whose transport always fails."""

    async def transport(self, channel: str, message: str) -> str:
        return "failed"


def _digest_job() -> Job:
    return Job(
        job_id="digest-dead-letter-1",
        handler_name="notification_digest",
        schedule="every 1m",
        idempotency_key="k-dead-letter",
        last_run_at=None,
        next_run_at="2026-06-30T08:00:00Z",
        status="pending",
    )


async def test_digest_dead_letter_writes_durable_row(tmp_db: DbPool) -> None:
    """The digest bypasses deliver()/router (its own `transport()` call), so its
    dead-letter branch (past `_MAX_FLUSH_ATTEMPTS`) had no seam before PB7b — the
    body was gone the moment the queue row was deleted. Assert the NACK now
    lands and the queue row is still dead-lettered (deleted)."""
    TestModeGuard.deactivate()
    nid = "note-dead-letter-1"
    due = (datetime(2026, 6, 30, tzinfo=UTC) - timedelta(minutes=1)).isoformat()
    await tmp_db.execute(
        "INSERT INTO notification_queue "
        "(notification_id, message_hash, urgency, category, channel, job_id, "
        "scheduled_for, message, attempts) VALUES (?,?,?,?,?,?,?,?,?)",
        (nid, "hash16", "normal", "digest", "cli", None, due, "digest body lost otherwise", 4),
    )
    handler = NotificationDigestJob(tmp_db, _AlwaysFailDeliverer())  # type: ignore[arg-type]

    await handler.execute(_digest_job())

    queue_rows = await tmp_db.fetch_all(
        "SELECT notification_id FROM notification_queue WHERE notification_id = ?", (nid,)
    )
    assert queue_rows == []  # dead-lettered (deleted)

    outbox_rows = await UndeliveredOutbox(tmp_db).list_pending()
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["reason"] == "transport_failed"
    assert outbox_rows[0]["body"] == "digest body lost otherwise"
    assert outbox_rows[0]["channel"] == "cli"


# --- PB7c: owner-scoped read fixes cross-channel banner surfacing ----------


async def test_list_pending_ignores_identity_key_returns_all_owner_rows(
    tmp_db: DbPool,
) -> None:
    """Two rows for the same owner but DIFFERENT identity_key values (one
    telegram-chat-id-shaped, one DEFAULT_PRINCIPAL_ID/slack-shaped) must BOTH
    surface — proving the old exact-match-on-identity_key behavior is gone."""
    outbox = UndeliveredOutbox(tmp_db)
    ok1 = await outbox.record_undelivered(
        identity_key="123456789",  # telegram-chat-id-shaped
        body="telegram-keyed row",
        reason="transport_failed",
        channel="telegram",
    )
    ok2 = await outbox.record_undelivered(
        identity_key=DEFAULT_PRINCIPAL_ID,  # slack/no-resolvable-recipient-shaped
        body="default-principal-keyed row",
        reason="undeliverable",
        channel="slack",
    )
    assert ok1 is True
    assert ok2 is True

    rows = await outbox.list_pending()
    assert len(rows) == 2
    bodies = {r["body"] for r in rows}
    assert bodies == {"telegram-keyed row", "default-principal-keyed row"}


async def test_banner_surfaces_row_written_under_different_identity_key(
    tmp_db: DbPool,
) -> None:
    """A row written under an identity_key that does NOT match the turn's
    session_id/identity_key must still surface on a real top-level turn —
    the "Slack row surfaces on a telegram turn" proof without a Slack adapter."""
    outbox = UndeliveredOutbox(tmp_db)
    ok = await outbox.record_undelivered(
        identity_key="some-other-channels-address",
        body="dropped on a different channel entirely",
        reason="suppressed",
        channel="slack",
    )
    assert ok is True

    set_services(StepServices(db_pool=tmp_db))
    state = PipelineState(
        trace_id="t-pb7c-1",
        session_id="turn-user-pb7c",  # does not match "some-other-channels-address"
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="assemble",
    )
    out = await assemble.run(state)
    assert "dropped on a different channel entirely" in (out.system_prompt or "")

    row = (
        await tmp_db.fetch_all(
            "SELECT surfaced_at FROM undelivered_outbox WHERE identity_key = ?",
            ("some-other-channels-address",),
        )
    )[0]
    assert row["surfaced_at"] is not None


async def test_render_banner_truncates_oversized_body() -> None:
    """render_banner bounds row COUNT via DEFAULT_LIST_LIMIT but not per-row
    body size — a single multi-KB dropped body must still be truncated."""
    oversized_body = "x" * (MAX_BANNER_BODY_CHARS + 200)
    rows = [
        {
            "category": "digest",
            "reason": "transport_failed",
            "body": oversized_body,
        }
    ]
    rendered = render_banner(rows)
    assert "x" * (MAX_BANNER_BODY_CHARS + 200) not in rendered
    assert rendered.rstrip().endswith("…")
    body_line = rendered.splitlines()[1]
    assert len(body_line) <= MAX_BANNER_BODY_CHARS + len("- [digest/transport_failed] ") + 1
