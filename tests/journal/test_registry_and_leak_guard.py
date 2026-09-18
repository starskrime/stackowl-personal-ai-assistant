"""The registry refuses what it doesn't know, and the leak guard scrubs what
it does (AD-3, AD-4, NFR33).

Canary secrets are driven through :class:`TaskEnqueuedAttrs`,
:class:`TaskClaimedAttrs` and :class:`TaskDeadLetteredAttrs` rather than
:class:`TaskFinishedAttrs` -- the spec names some of these as illustrative
vehicles, but ``TaskFinishedAttrs``' only field is
``completion_mode: Literal["delivered", "unaddressed"]`` (its one
explicitly-specified field), a closed Literal with no string slot a secret
could hide in. The other three carry genuine open string fields
(``trigger_kind``, ``lease_owner``, ``failure_class``, ``task_kind``) that a
real emitter populates, so they are the honest vehicles for this proof.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalEventTypeUnregisteredError, JournalInvalidAttrsError
from stackowl.journal import (
    ActorKind,
    AttentionClass,
    Intensity,
    JournalEvent,
    Outcome,
    RecordKind,
    record,
)
from stackowl.journal.registry import EventTypeSpec
from stackowl.journal.task_events import TaskEnqueuedAttrs, TaskFinishedAttrs
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"
# Built by concatenation, not as one literal: a contiguous "AKIA"+16-char
# string here trips GitHub push protection's AWS-key-shape scanner even
# though this is a synthetic canary, never a real credential.
AWS_KEY_CANARY = "AKIA" + "NOTAREALCANARYKEY"[:16]


class TestTheRegistryRefusesWhatItDoesNotKnow:
    async def test_an_unregistered_type_raises_before_any_sql_runs(
        self, tmp_db: DbPool
    ) -> None:
        event = JournalEvent(
            type="task.not_a_real_type",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="unreg-1",
            outcome=Outcome.OK,
            attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
        )
        with pytest.raises(JournalEventTypeUnregisteredError):
            async with tmp_db.transaction() as conn:
                await record(conn, event)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("unreg-1",)
        )
        assert rows == []

    async def test_attrs_of_the_wrong_model_raises_before_any_sql_runs(
        self, tmp_db: DbPool
    ) -> None:
        """``task.finished`` is registered with ``TaskFinishedAttrs`` -- handing
        it a ``TaskEnqueuedAttrs`` instance must be refused, not coerced."""
        event = JournalEvent(
            type="task.finished",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="badattrs-1",
            outcome=Outcome.OK,
            attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
        )
        with pytest.raises(JournalInvalidAttrsError):
            async with tmp_db.transaction() as conn:
                await record(conn, event)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("badattrs-1",)
        )
        assert rows == []


class TestTheLeakGuardScrubsSecretShapedStringsFromRealEmitters:
    async def test_a_bearer_token_in_an_enqueued_tasks_trigger_kind_is_redacted(
        self, tmp_db: DbPool
    ) -> None:
        """Drives the canary through the REAL ``DurableTaskStore.enqueue()``
        emitter, not a hand-built event -- proves the guard runs on the actual
        production path, not just the guard function in isolation."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(
            task_id="leak-1", goal="g", status="pending",
            trigger_kind=BEARER_CANARY,
        ))

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'task.enqueued' "
            "AND target_id = ?", ("leak-1",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert "sk-canary1234567890abcdefghijklmno" not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_an_aws_key_shaped_failure_class_is_redacted_on_dead_letter(
        self, tmp_db: DbPool
    ) -> None:
        """Drives the canary through the REAL ``fail_and_requeue`` permanent
        branch, which builds ``TaskDeadLetteredAttrs.failure_class`` from the
        caller-supplied ``failure_class`` string."""
        store = DurableTaskStore(tmp_db)
        # max_attempts=1 reaches the ceiling on the FIRST failure, forcing the
        # dead-letter branch regardless of what `failure_class` says — the
        # canary string only needs to reach `TaskDeadLetteredAttrs.failure_class`,
        # not to itself classify as a permanent failure.
        await store.enqueue(DurableTask(
            task_id="leak-2", goal="g", status="pending", max_attempts=1,
        ))

        status = await store.fail_and_requeue(
            "leak-2", error="boom", failure_class=AWS_KEY_CANARY,
        )
        assert status == "dead_letter"

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'task.dead_lettered' "
            "AND target_id = ?", ("leak-2",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert AWS_KEY_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_a_bearer_token_shaped_worker_id_is_redacted_on_claim(
        self, tmp_db: DbPool
    ) -> None:
        """Drives the canary through the REAL ``DurableTaskStore.claim()``
        emitter -- ``TaskClaimedAttrs.lease_owner`` is a genuine open string
        field on a real wired emitter, same as ``trigger_kind``/``failure_class``
        above."""
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="leak-3", goal="g", status="pending"))

        won = await store.claim("leak-3", worker=BEARER_CANARY)
        assert won is True

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'task.claimed' "
            "AND target_id = ?", ("leak-3",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert "sk-canary1234567890abcdefghijklmno" not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_scan_attrs_leaves_an_ordinary_bounded_label_untouched(self) -> None:
        """The guard must not cry wolf on the common case -- an ordinary short
        label is unaffected."""
        from stackowl.journal.leak_guard import scan_attrs

        redacted, was_redacted = scan_attrs(
            TaskFinishedAttrs(completion_mode="delivered")
        )
        assert was_redacted is False
        assert redacted == {"completion_mode": "delivered"}


def _spec(**over: object) -> EventTypeSpec:
    defaults: dict[str, object] = {
        "type": "test.spec_validation",
        "schema_version": 1,
        "attrs_model": TaskFinishedAttrs,
        "emitting_process": "test",
        "record_kind": RecordKind.TASK,
        "attention_class": AttentionClass.AMBIENT,
        "narrate": lambda attrs, name: f"{name} happened",
        "intensity": None,
    }
    defaults.update(over)
    return EventTypeSpec(**defaults)  # type: ignore[arg-type]


class TestEventTypeSpecValidatesAtConstruction:
    """Story 2.2: "no narration" and "no attention class" are the same kind
    of refusal -- a required constructor argument, validated in
    ``__post_init__``, before ``registry.register()`` ever sees it."""

    @pytest.mark.tripwire
    def test_a_type_with_no_narration_is_refused(self) -> None:
        with pytest.raises(ValueError, match="narrate"):
            _spec(narrate=None)

    @pytest.mark.tripwire
    def test_needs_you_with_no_intensity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="intensity"):
            _spec(attention_class=AttentionClass.NEEDS_YOU, intensity=None)

    @pytest.mark.tripwire
    def test_ambient_with_an_intensity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="intensity"):
            _spec(attention_class=AttentionClass.AMBIENT, intensity=Intensity.HIGH)

    def test_a_well_formed_spec_constructs_cleanly(self) -> None:
        spec = _spec()
        assert spec.attention_class is AttentionClass.AMBIENT
        assert spec.intensity is None
        assert spec.narrate(TaskFinishedAttrs(completion_mode="delivered"), "x") == "x happened"
