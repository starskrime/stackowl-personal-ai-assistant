"""Story 2.9 -- ``ProactiveDeliverer.deliver()``/``transport()`` record
``delivery.attempted``/``provider.rerouted`` journal events at their real
call sites, driven against a REAL ``tmp_db``-backed ``db_pool``, a fake
router and fake channel adapters -- proving the wiring, not just the helper
in isolation (``tests/journal/test_delivery_events.py`` already covers that).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import DeliveryStatus, Notification, NotificationRouter

pytestmark = pytest.mark.asyncio


def _settings(default_channel: str = "cli", fallback_channel: str = "") -> Settings:
    ns = SimpleNamespace(
        notifications=NotificationSettings(
            default_channel=default_channel, fallback_channel=fallback_channel,
        )
    )
    return cast(Settings, ns)


class _RecordingAdapter:
    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.sent: list[str] = []
        self.fail_times = 0

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient send failure")
        self.sent.append(text)


class _StubRouter:
    def __init__(self, decision: DeliveryStatus) -> None:
        self._decision = decision

    async def deliver(self, notification: Notification) -> DeliveryStatus:
        return self._decision


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


def _deliverer(
    tmp_db: DbPool, decision: DeliveryStatus, *,
    default_channel: str = "cli", fallback_channel: str = "",
) -> tuple[ProactiveDeliverer, ChannelRegistry]:
    router = _StubRouter(decision)
    registry = ChannelRegistry.instance()
    dlv = ProactiveDeliverer(
        router=cast(NotificationRouter, router),
        registry=registry,
        settings=_settings(default_channel, fallback_channel),
        db_pool=tmp_db,
    )
    return dlv, registry


def _note(
    channel: str | None = "cli", *, category: str = "test",
    job_id: str | None = None, notification_id: str | None = None,
) -> Notification:
    return Notification(
        message="hello body", urgency="normal", category=category,
        channel_name=channel, job_id=job_id, notification_id=notification_id,
    )


async def _delivery_rows(tmp_db: DbPool) -> list[dict]:
    return await tmp_db.fetch_all("SELECT * FROM delivery_records")


async def _journal_rows(tmp_db: DbPool, event_type: str) -> list[dict]:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = ?", (event_type,)
    )


class TestDelivered:
    async def test_records_one_delivery_attempted_ok(self, tmp_db: DbPool) -> None:
        dlv, registry = _deliverer(tmp_db, "delivered")
        registry.register(_RecordingAdapter("cli"))

        status = await dlv.deliver(_note("cli", job_id="j1", notification_id="n1"))

        assert status == "delivered"
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["channel"] == "cli"
        assert attrs["delivery_status"] == "delivered"
        assert attrs["job_id"] == "j1"
        assert attrs["notification_id"] == "n1"
        delivery_rows = await _delivery_rows(tmp_db)
        assert len(delivery_rows) == 1


class TestFailedNoReroute:
    async def test_records_one_delivery_attempted_failed(self, tmp_db: DbPool) -> None:
        dlv, registry = _deliverer(tmp_db, "delivered")  # router says deliver; send fails
        adapter = _RecordingAdapter("cli")
        adapter.fail_times = 99
        registry.register(adapter)

        status = await dlv.deliver(_note("cli"))

        assert status == "failed"
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        # No reroute configured -> no provider.rerouted row.
        assert await _journal_rows(tmp_db, "provider.rerouted") == []


class TestFailedWithSuccessfulReroute:
    async def test_records_one_delivery_attempted_ok_and_one_provider_rerouted(
        self, tmp_db: DbPool,
    ) -> None:
        dlv, registry = _deliverer(
            tmp_db, "delivered", fallback_channel="telegram",
        )
        primary = _RecordingAdapter("cli")
        primary.fail_times = 99
        fallback = _RecordingAdapter("telegram")
        registry.register(primary)
        registry.register(fallback)

        status = await dlv.deliver(_note("cli", job_id="j2", notification_id="n2"))

        assert status == "delivered"
        delivery_attempted_rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(delivery_attempted_rows) == 1
        assert delivery_attempted_rows[0]["outcome"] == "ok"
        # channel stays the ORIGINALLY-addressed one, never the fallback.
        attrs = json.loads(delivery_attempted_rows[0]["attrs"])
        assert attrs["channel"] == "cli"

        rerouted_rows = await _journal_rows(tmp_db, "provider.rerouted")
        assert len(rerouted_rows) == 1
        assert rerouted_rows[0]["outcome"] == "ok"
        rerouted_attrs = json.loads(rerouted_rows[0]["attrs"])
        assert rerouted_attrs["from_channel"] == "cli"
        assert rerouted_attrs["to_channel"] == "telegram"


class TestReroutedButStillFails:
    async def test_no_provider_rerouted_row_delivery_attempted_still_failed(
        self, tmp_db: DbPool,
    ) -> None:
        dlv, registry = _deliverer(
            tmp_db, "delivered", fallback_channel="telegram",
        )
        primary = _RecordingAdapter("cli")
        primary.fail_times = 99
        fallback = _RecordingAdapter("telegram")
        fallback.fail_times = 99
        registry.register(primary)
        registry.register(fallback)

        status = await dlv.deliver(_note("cli"))

        assert status == "failed"
        assert await _journal_rows(tmp_db, "provider.rerouted") == []
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"


class TestBatchedAndSuppressed:
    async def test_batched_records_pending(self, tmp_db: DbPool) -> None:
        dlv, registry = _deliverer(tmp_db, "batched")
        registry.register(_RecordingAdapter("cli"))

        status = await dlv.deliver(_note("cli"))

        assert status == "batched"
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "pending"

    async def test_suppressed_records_parked(self, tmp_db: DbPool) -> None:
        dlv, registry = _deliverer(tmp_db, "suppressed")
        registry.register(_RecordingAdapter("cli"))

        status = await dlv.deliver(_note("cli"))

        assert status == "suppressed"
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "parked"


class TestDigestFlushTransport:
    async def test_transport_records_delivery_attempted_with_no_category_or_job_id(
        self, tmp_db: DbPool,
    ) -> None:
        dlv, registry = _deliverer(tmp_db, "suppressed")  # router decision irrelevant
        registry.register(_RecordingAdapter("cli"))

        status = await dlv.transport("cli", "queued body")

        assert status == "delivered"
        rows = await _journal_rows(tmp_db, "delivery.attempted")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["category"] is None
        assert attrs["job_id"] is None


class TestNoDbPoolIsByteIdentical:
    async def test_deliverer_without_db_pool_still_delivers_and_records_nothing(
        self,
    ) -> None:
        router = _StubRouter("delivered")
        registry = ChannelRegistry.instance()
        dlv = ProactiveDeliverer(
            router=cast(NotificationRouter, router),
            registry=registry,
            settings=_settings(),
        )
        registry.register(_RecordingAdapter("cli"))

        status = await dlv.deliver(_note("cli"))

        assert status == "delivered"  # unaffected by the missing db_pool
