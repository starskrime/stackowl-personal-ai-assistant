"""``delivery.attempted``/``provider.rerouted`` are registered correctly
(Story 2.9), and ``record_delivery_attempted``/``record_provider_rerouted``
are atomic with their own transaction (AD-24), no-op with no ``db_pool``, and
redact secrets through a REAL emitter (NFR33) -- the same proof shape
``tests/journal/test_consent_events.py``/``test_turn_events.py`` use for
Story 2.7/2.8's types.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import AttentionClass, RecordKind, classify
from stackowl.journal.delivery_events import (
    _DELIVERY_OUTCOME_MAP,
    DeliveryAttemptedAttrs,
    ProviderReroutedAttrs,
    record_delivery_attempted,
    record_provider_rerouted,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry

pytestmark = pytest.mark.asyncio

# Synthetic canary only -- never a real credential.
BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


class TestTheTypesAreRegistered:
    def test_delivery_attempted_is_delivery_ambient(self) -> None:
        spec = get_registry().get("delivery.attempted")
        assert spec.attrs_model is DeliveryAttemptedAttrs
        assert spec.record_kind is RecordKind.DELIVERY
        assert spec.emitting_process == "notifications.deliverer"
        assert classify("delivery.attempted") == (AttentionClass.AMBIENT, None)

    def test_provider_rerouted_is_provider_ambient(self) -> None:
        spec = get_registry().get("provider.rerouted")
        assert spec.attrs_model is ProviderReroutedAttrs
        assert spec.record_kind is RecordKind.PROVIDER
        assert spec.emitting_process == "notifications.deliverer"
        assert classify("provider.rerouted") == (AttentionClass.AMBIENT, None)


class TestNarration:
    def test_delivery_attempted_names_channel_and_status(self) -> None:
        spec = get_registry().get("delivery.attempted")
        text = narrate_full(
            spec,
            DeliveryAttemptedAttrs(
                channel="telegram", delivery_status="delivered",
                category=None, job_id=None, notification_id=None,
            ),
            "x",
        )
        assert "telegram" in text
        assert "delivered" in text

    def test_provider_rerouted_names_both_channels(self) -> None:
        spec = get_registry().get("provider.rerouted")
        text = narrate_full(
            spec, ProviderReroutedAttrs(from_channel="cli", to_channel="telegram"), "x",
        )
        assert "cli" in text
        assert "telegram" in text


class TestAttrsNeverCarryFreeText:
    def test_an_undeclared_field_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DeliveryAttemptedAttrs(
                channel="cli", delivery_status="delivered",
                category=None, job_id=None, notification_id=None,
                message="free text must never fit here",  # type: ignore[call-arg]
            )

    def test_delivery_status_only_accepts_the_closed_vocabulary(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DeliveryAttemptedAttrs(
                channel="cli", delivery_status="maybe",  # type: ignore[arg-type]
                category=None, job_id=None, notification_id=None,
            )


class TestTheOutcomeMapping:
    """The 4-way `delivery_status` → envelope `outcome` collapse (Boundaries)."""

    def test_delivered_maps_to_ok(self) -> None:
        from stackowl.journal.enums import Outcome
        assert _DELIVERY_OUTCOME_MAP["delivered"] is Outcome.OK

    def test_failed_maps_to_failed(self) -> None:
        from stackowl.journal.enums import Outcome
        assert _DELIVERY_OUTCOME_MAP["failed"] is Outcome.FAILED

    def test_batched_maps_to_pending(self) -> None:
        from stackowl.journal.enums import Outcome
        assert _DELIVERY_OUTCOME_MAP["batched"] is Outcome.PENDING

    def test_suppressed_maps_to_parked(self) -> None:
        from stackowl.journal.enums import Outcome
        assert _DELIVERY_OUTCOME_MAP["suppressed"] is Outcome.PARKED


class TestANoneDbPoolIsASilentNoOp:
    async def test_delivery_attempted_no_db_pool(self) -> None:
        await record_delivery_attempted(
            None, channel="cli", delivery_status="delivered",
            category=None, job_id=None, notification_id=None,
        )

    async def test_provider_rerouted_no_db_pool(self) -> None:
        await record_provider_rerouted(None, from_channel="cli", to_channel="telegram")


class TestTheHelperCommitsExactlyOneRowInEachTable:
    async def test_delivered(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="telegram", delivery_status="delivered",
            category="digest", job_id="job-1", notification_id="notif-1",
        )
        delivery_rows = await tmp_db.fetch_all(
            "SELECT * FROM delivery_records WHERE channel = ?", ("telegram",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'delivery.attempted' "
            "AND target_id = ?",
            ("telegram",),
        )
        assert len(delivery_rows) == 1
        assert len(journal_rows) == 1
        row = journal_rows[0]
        assert row["outcome"] == "ok"
        assert row["attention"] == "ambient"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "delivery_records"
        assert ref["locator"]["id"] == delivery_rows[0]["id"]
        assert delivery_rows[0]["notification_id"] == "notif-1"
        attrs = json.loads(row["attrs"])
        assert attrs["delivery_status"] == "delivered"
        assert attrs["category"] == "digest"
        assert attrs["job_id"] == "job-1"

    async def test_failed(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="cli", delivery_status="failed",
            category=None, job_id=None, notification_id=None,
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'delivery.attempted' "
            "AND target_id = ?",
            ("cli",),
        )
        assert journal_rows[0]["outcome"] == "failed"

    async def test_batched(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="cli", delivery_status="batched",
            category=None, job_id=None, notification_id=None,
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT outcome FROM journal_events WHERE type = 'delivery.attempted'",
        )
        assert journal_rows[0]["outcome"] == "pending"

    async def test_suppressed(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="cli", delivery_status="suppressed",
            category=None, job_id=None, notification_id=None,
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT outcome FROM journal_events WHERE type = 'delivery.attempted'",
        )
        assert journal_rows[0]["outcome"] == "parked"

    async def test_provider_rerouted(self, tmp_db: DbPool) -> None:
        await record_provider_rerouted(tmp_db, from_channel="cli", to_channel="telegram")

        provider_rows = await tmp_db.fetch_all(
            "SELECT * FROM delivery_records WHERE kind = 'provider.rerouted'",
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'provider.rerouted'",
        )
        assert len(provider_rows) == 1
        assert len(journal_rows) == 1
        assert journal_rows[0]["outcome"] == "ok"
        attrs = json.loads(journal_rows[0]["attrs"])
        assert attrs["from_channel"] == "cli"
        assert attrs["to_channel"] == "telegram"
        assert provider_rows[0]["channel"] == "telegram"


class TestTheHelperIsAtomicWithItsOwnTransaction:
    async def test_a_journal_record_failure_leaves_neither_row(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import delivery_events as delivery_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(delivery_events_module, "journal_record", _boom)

        # Never raises out (B5).
        await record_delivery_attempted(
            tmp_db, channel="rollback-channel", delivery_status="delivered",
            category=None, job_id=None, notification_id=None,
        )

        delivery_rows = await tmp_db.fetch_all(
            "SELECT * FROM delivery_records WHERE channel = ?", ("rollback-channel",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'delivery.attempted' "
            "AND target_id = ?",
            ("rollback-channel",),
        )
        assert delivery_rows == []
        assert journal_rows == []

    async def test_a_raw_insert_failure_degrades_health_directly(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import stackowl.journal.delivery_events as delivery_events_module
        from stackowl.journal import health as journal_health

        monkeypatch.setattr(
            delivery_events_module, "_INSERT_DELIVERY_RECORD_SQL",
            "INSERT INTO no_such_table (id) VALUES (?)",
        )
        await record_delivery_attempted(
            tmp_db, channel="broken-insert-channel", delivery_status="delivered",
            category=None, job_id=None, notification_id=None,
        )
        snap = journal_health.JournalHealthContributor()
        status = await snap.health_check()
        assert status.status == "degraded"


class TestCanarySecretsAreRedactedThroughARealEmitter:
    """NFR33: driven through the REAL recording helper -- no canary ever
    appears unredacted in ``delivery_records``/``journal_events.attrs``."""

    async def test_delivery_attempted_channel(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel=BEARER_CANARY, delivery_status="delivered",
            category=None, job_id=None, notification_id=None,
        )
        rows = await tmp_db.fetch_all(
            "SELECT channel FROM delivery_records",
        )
        assert len(rows) == 1
        assert BEARER_CANARY not in rows[0]["channel"]

        journal_rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'delivery.attempted'",
        )
        stored = journal_rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_delivery_attempted_notification_id(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="cli", delivery_status="delivered",
            category=None, job_id=None, notification_id=BEARER_CANARY,
        )
        rows = await tmp_db.fetch_all(
            "SELECT notification_id FROM delivery_records",
        )
        assert BEARER_CANARY not in (rows[0]["notification_id"] or "")

    async def test_provider_rerouted(self, tmp_db: DbPool) -> None:
        await record_provider_rerouted(
            tmp_db, from_channel="cli", to_channel=BEARER_CANARY,
        )
        rows = await tmp_db.fetch_all(
            "SELECT channel FROM delivery_records WHERE kind = 'provider.rerouted'",
        )
        assert BEARER_CANARY not in rows[0]["channel"]
