"""AD-4's record readers (Story 2.10): one round trip per registered
``(RecordKind, carrier)`` reader -- a real row inserted through the owning
module's own production writer (or, where no journal-owned writer exists yet
for the domain, the exact real production INSERT the wired emitter itself
runs -- ``heal_attempts``/``health_status_changes``, both still written
inline by ``scheduler/handlers/health_sweep.py`` rather than through a
``journal/*_events.py`` helper), read back through the registered reader, and
asserted typed.

Also covers the three cross-cutting rules every reader shares: a missing
target opens :class:`~stackowl.journal.records.ExpiredRecord`, never raises;
a caller whose ``owner_id`` is not the platform owner is refused, loudly; a
duplicate ``(RecordKind, carrier)`` registration is refused.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalRecordReaderRefusedError
from stackowl.infra.trace import TraceContext
from stackowl.journal.channel_events import (
    ChannelIngressRecordView,
    read_channel_ingress_record,
    record_channel_message_received,
)
from stackowl.journal.consent_events import (
    ConsentDecisionRecordView,
    read_consent_decision_record,
    record_consent_decision,
)
from stackowl.journal.delivery_events import (
    DeliveryRecordView,
    read_delivery_attempted_record,
    read_provider_rerouted_record,
    record_delivery_attempted,
    record_provider_rerouted,
)
from stackowl.journal.enums import ActorKind, RecordKind
from stackowl.journal.heal_events import HealAttemptRecordView, read_heal_attempt_record
from stackowl.journal.health_events import (
    HealthStatusChangeRecordView,
    read_health_status_change_record,
)
from stackowl.journal.job_events import JobRecordView, read_job_record
from stackowl.journal.memory_events import (
    MemoryWrittenRecordView,
    ReflectionRecordView,
    read_memory_written_record,
    read_reflection_record,
    record_md_memory_write,
)
from stackowl.journal.records import ExpiredRecord, RecordReaderRegistry
from stackowl.journal.task_events import TaskRecordView, read_task_record
from stackowl.journal.turn_events import TurnActionRecordView, read_turn_action_record, record_model_call
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.paths import StackowlHome
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler_helpers import insert_job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio

_WRONG_OWNER = "not-the-owner"


def test_the_copied_default_principal_id_has_not_drifted() -> None:
    """``journal/records.py::_DEFAULT_PRINCIPAL_ID`` is COPIED, not imported,
    from ``tenancy.DEFAULT_PRINCIPAL_ID`` (AD-7: ``journal/`` imports nothing
    from any subsystem). This is the direct proof that the copy still
    matches the real constant -- every round-trip test above only proves it
    indirectly, by working."""
    from stackowl.journal import records as records_module

    assert records_module._DEFAULT_PRINCIPAL_ID == DEFAULT_PRINCIPAL_ID


class TestTaskRecordReader:
    async def test_round_trip_via_the_real_enqueue_writer(self, tmp_db: DbPool) -> None:
        await DurableTaskStore(tmp_db).enqueue(
            DurableTask(task_id="reader-task-1", goal="do the thing", status="pending"),
        )

        result = await read_task_record(
            tmp_db, {"table": "tasks", "task_id": "reader-task-1"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, TaskRecordView)
        assert result.task_id == "reader-task-1"
        assert result.goal == "do the thing"
        assert result.status == "pending"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_task_record(
            tmp_db, {"table": "tasks", "task_id": "no-such-task"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.TASK


class TestJobRecordReader:
    async def test_round_trip_via_a_real_jobs_row(self, tmp_db: DbPool) -> None:
        job = Job(
            job_id="reader-job-1", handler_name="a_handler", schedule="@daily",
            idempotency_key="reader-job-1-key", last_run_at=None,
            next_run_at="2026-09-19T00:00:00+00:00", status="pending",
        )
        await insert_job(tmp_db, job)

        result = await read_job_record(
            tmp_db, {"table": "jobs", "job_id": "reader-job-1"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, JobRecordView)
        assert result.job_id == "reader-job-1"
        assert result.handler_name == "a_handler"
        assert result.status == "pending"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_job_record(
            tmp_db, {"table": "jobs", "job_id": "no-such-job"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.JOB


class TestHealAttemptRecordReader:
    async def test_round_trip_via_the_real_heal_attempts_insert_shape(
        self, tmp_db: DbPool,
    ) -> None:
        """Mirrors ``scheduler/handlers/health_sweep.py::_record_heal_attempted``'s
        own INSERT exactly -- the health sweep has no separate journal-owned
        recording helper to call (its recording lives inline on the wired
        ``HealthSweepHandler`` instance, which this narrow reader test does
        not need to instantiate)."""
        row_id = str(uuid.uuid4())
        now_iso = datetime.now(UTC).isoformat()
        await tmp_db.execute(
            "INSERT INTO heal_attempts "
            "(id, subsystem, status, attempt_count, created_at, updated_at) "
            "VALUES (?, ?, 'attempted', 1, ?, ?)",
            (row_id, "reader-subsystem", now_iso, now_iso),
        )

        result = await read_heal_attempt_record(
            tmp_db, {"table": "heal_attempts", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, HealAttemptRecordView)
        assert result.id == row_id
        assert result.subsystem == "reader-subsystem"
        assert result.status == "attempted"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_heal_attempt_record(
            tmp_db, {"table": "heal_attempts", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.HEAL


class TestHealthStatusChangeRecordReader:
    async def test_round_trip_via_the_real_health_status_changes_insert_shape(
        self, tmp_db: DbPool,
    ) -> None:
        """Mirrors ``health_sweep.py::_record_health_changes``'s own INSERT
        exactly -- same "no separate helper to call" shape as heal_attempts
        above."""
        row_id = str(uuid.uuid4())
        now_iso = datetime.now(UTC).isoformat()
        await tmp_db.execute(
            "INSERT INTO health_status_changes "
            "(id, subsystem, previous_status, new_status, error_code, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, "reader-subsystem", "ok", "degraded", "timeout", now_iso),
        )

        result = await read_health_status_change_record(
            tmp_db, {"table": "health_status_changes", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, HealthStatusChangeRecordView)
        assert result.id == row_id
        assert result.previous_status == "ok"
        assert result.new_status == "degraded"
        assert result.error_code == "timeout"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_health_status_change_record(
            tmp_db, {"table": "health_status_changes", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.HEALTH


class TestTurnActionRecordReader:
    async def test_round_trip_via_the_real_record_model_call_writer(
        self, tmp_db: DbPool,
    ) -> None:
        token = TraceContext.start(trace_id="reader-trace-1", owl_name="scout")
        try:
            await record_model_call(
                tmp_db, provider="anthropic", duration_ms=42.0, ok=True, error_code=None,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT id FROM turn_action_records WHERE trace_id = ?", ("reader-trace-1",),
        )
        row_id = rows[0]["id"]

        result = await read_turn_action_record(
            tmp_db, {"table": "turn_action_records", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, TurnActionRecordView)
        assert result.id == row_id
        assert result.trace_id == "reader-trace-1"
        assert result.kind == "model.called"
        assert result.identifier == "anthropic"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_turn_action_record(
            tmp_db, {"table": "turn_action_records", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.TURN


class TestConsentDecisionRecordReader:
    async def test_round_trip_via_the_real_record_consent_decision_writer(
        self, tmp_db: DbPool,
    ) -> None:
        await record_consent_decision(
            tmp_db, tool_name="reader-tool", channel="cli", session_key="s-1",
            category=None, reason="owner asked", scope=None, allowed=True,
        )

        rows = await tmp_db.fetch_all(
            "SELECT id FROM consent_decision_records WHERE tool_name = ?", ("reader-tool",),
        )
        row_id = rows[0]["id"]

        result = await read_consent_decision_record(
            tmp_db, {"table": "consent_decision_records", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ConsentDecisionRecordView)
        assert result.id == row_id
        assert result.tool_name == "reader-tool"
        assert result.decision == "allow"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_consent_decision_record(
            tmp_db, {"table": "consent_decision_records", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.CONSENT


class TestDeliveryAndProviderRecordReaders:
    async def test_delivery_attempted_round_trip(self, tmp_db: DbPool) -> None:
        await record_delivery_attempted(
            tmp_db, channel="telegram", delivery_status="delivered",
            category=None, job_id=None, notification_id="notif-reader-1",
        )

        rows = await tmp_db.fetch_all(
            "SELECT id FROM delivery_records WHERE notification_id = ?", ("notif-reader-1",),
        )
        row_id = rows[0]["id"]

        result = await read_delivery_attempted_record(
            tmp_db, {"table": "delivery_records", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, DeliveryRecordView)
        assert result.id == row_id
        assert result.kind == "delivery.attempted"
        assert result.channel == "telegram"

    async def test_provider_rerouted_round_trip(self, tmp_db: DbPool) -> None:
        await record_provider_rerouted(
            tmp_db, from_channel="cli", to_channel="telegram",
            notification_id="notif-reader-2",
        )

        rows = await tmp_db.fetch_all(
            "SELECT id FROM delivery_records WHERE notification_id = ?", ("notif-reader-2",),
        )
        row_id = rows[0]["id"]

        result = await read_provider_rerouted_record(
            tmp_db, {"table": "delivery_records", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, DeliveryRecordView)
        assert result.id == row_id
        assert result.kind == "provider.rerouted"
        assert result.channel == "telegram"

    async def test_delivery_missing_row_opens_expired(self, tmp_db: DbPool) -> None:
        result = await read_delivery_attempted_record(
            tmp_db, {"table": "delivery_records", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.DELIVERY

    async def test_provider_missing_row_opens_expired(self, tmp_db: DbPool) -> None:
        result = await read_provider_rerouted_record(
            tmp_db, {"table": "delivery_records", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.PROVIDER


class TestChannelIngressRecordReader:
    async def test_round_trip_via_the_real_writer(self, tmp_db: DbPool) -> None:
        await record_channel_message_received(
            tmp_db, channel="telegram", session_key="reader-session-1",
            trace_id="reader-trace-2",
        )

        rows = await tmp_db.fetch_all(
            "SELECT id FROM channel_ingress_records WHERE session_key = ?",
            ("reader-session-1",),
        )
        row_id = rows[0]["id"]

        result = await read_channel_ingress_record(
            tmp_db, {"table": "channel_ingress_records", "id": row_id},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ChannelIngressRecordView)
        assert result.id == row_id
        assert result.channel == "telegram"
        assert result.session_key == "reader-session-1"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_channel_ingress_record(
            tmp_db, {"table": "channel_ingress_records", "id": "no-such-row"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.CHANNEL


class TestReflectionRecordReader:
    async def test_round_trip_via_the_real_reflection_store_writer(
        self, tmp_db: DbPool,
    ) -> None:
        await ReflectionStore(tmp_db).write(
            trace_id="reader-trace-3", owl_name="scout", summary="it worked",
            suggested_strategy="do it again", failure_class=None,
            quality_score=0.9, embedding=None, embedding_model=None,
        )

        result = await read_reflection_record(
            tmp_db, {"table": "reflections", "trace_id": "reader-trace-3"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ReflectionRecordView)
        assert result.trace_id == "reader-trace-3"
        assert result.owl_name == "scout"
        assert result.summary == "it worked"

    async def test_a_missing_row_opens_expired_never_raises(self, tmp_db: DbPool) -> None:
        result = await read_reflection_record(
            tmp_db, {"table": "reflections", "trace_id": "no-such-trace"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.MEMORY


class TestMemoryWrittenRecordReader:
    async def test_round_trip_via_the_real_md_writer(
        self, tmp_db: DbPool, tmp_path, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact the reader must see\n", encoding="utf-8")

        await record_md_memory_write(
            tmp_db, target="user", path=path, op="add", changed_index=0,
            durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
        )

        result = await read_memory_written_record(
            None, {"path": "memory/USER.md", "anchor": "entry-0"},
            owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, MemoryWrittenRecordView)
        assert result.anchor == "entry-0"
        assert "a fact the reader must see" in result.text

    async def test_a_missing_file_opens_expired_never_raises(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

        result = await read_memory_written_record(
            None, {"path": "memory/does-not-exist.md"}, owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)
        assert result.record_kind == RecordKind.MEMORY

    async def test_a_locator_escaping_the_home_directory_opens_expired(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """md readers only ever open a file for read from a StackowlHome-
        relative path -- a locator trying to climb out of home must never
        resolve to a real file outside it."""
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        outside = tmp_path.parent / "outside-home.md"
        outside.write_text("must never be readable through the reader\n", encoding="utf-8")

        result = await read_memory_written_record(
            None, {"path": "../outside-home.md"}, owner_id=DEFAULT_PRINCIPAL_ID,
        )

        assert isinstance(result, ExpiredRecord)


class TestEveryReaderRefusesTheWrongOwner:
    """One representative reader proves the shared authority check
    (:func:`~stackowl.journal.records.refuse_unless_owner`) actually runs --
    every other reader calls the identical function."""

    async def test_task_reader_refuses_a_non_owner(self, tmp_db: DbPool) -> None:
        with pytest.raises(JournalRecordReaderRefusedError):
            await read_task_record(
                tmp_db, {"table": "tasks", "task_id": "irrelevant"},
                owner_id=_WRONG_OWNER,
            )

    async def test_memory_written_reader_refuses_a_non_owner(self, tmp_path) -> None:  # noqa: ANN001
        with pytest.raises(JournalRecordReaderRefusedError):
            await read_memory_written_record(
                None, {"path": "memory/USER.md"}, owner_id=_WRONG_OWNER,
            )


class TestDuplicateReaderRegistrationIsRefused:
    def test_the_second_registration_for_the_same_key_raises(self) -> None:
        registry = RecordReaderRegistry()

        async def _dummy(db_pool, locator, *, owner_id):  # noqa: ANN001, ARG001
            raise AssertionError("never called")

        registry.register(RecordKind.TASK, "sqlite", _dummy)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(RecordKind.TASK, "sqlite", _dummy)
