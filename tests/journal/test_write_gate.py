"""The journal write-gate -- Spec 2.3's pause switch on ``journal.record()``.

``record()`` refuses with :class:`JournalWritesPausedError` (carrying a
``.remedy``) while the gate is paused, and BEFORE it ever resolves the event
type against the registry (an unregistered-type error must never mask a
paused gate). It succeeds again once resumed.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalWritesPausedError
from stackowl.journal import ActorKind, JournalEvent, Outcome, record
from stackowl.journal.task_events import TaskEnqueuedAttrs
from stackowl.journal.write_gate import pause_writes, reset_for_tests, resume_writes, writes_paused

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_write_gate() -> Generator[None]:
    reset_for_tests()
    yield
    reset_for_tests()


def _event(target_id: str) -> JournalEvent:
    return JournalEvent(
        type="task.enqueued",
        schema_version=1,
        actor_kind=ActorKind.AUTONOMOUS,
        actor_id="principal-default",
        target_kind=ActorKind.OWNER,
        target_id=target_id,
        outcome=Outcome.PENDING,
        attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
    )


class TestTheGateFlag:
    def test_writes_are_not_paused_by_default(self) -> None:
        assert writes_paused() is False

    def test_pause_then_resume_round_trips(self) -> None:
        pause_writes("test reason")
        assert writes_paused() is True
        resume_writes()
        assert writes_paused() is False


class TestRecordHonoursTheGate:
    async def test_record_raises_while_paused(self, tmp_db: DbPool) -> None:
        pause_writes("gateway-core link lost")
        with pytest.raises(JournalWritesPausedError) as exc_info:
            async with tmp_db.transaction() as conn:
                await record(conn, _event("wg-1"))
        assert exc_info.value.remedy

    async def test_record_succeeds_once_resumed(self, tmp_db: DbPool) -> None:
        pause_writes("gateway-core link lost")
        resume_writes()
        async with tmp_db.transaction() as conn:
            event_id = await record(conn, _event("wg-2"))
        assert event_id

    async def test_a_paused_gate_is_checked_before_the_registry_lookup(
        self, tmp_db: DbPool
    ) -> None:
        """An UNREGISTERED type must not mask a paused gate -- the gate check
        runs first, so this raises JournalWritesPausedError, never
        JournalEventTypeUnregisteredError."""
        pause_writes("gateway-core link lost")
        bad_event = JournalEvent(
            type="task.definitely_not_registered",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="wg-3",
            outcome=Outcome.OK,
            attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
        )
        with pytest.raises(JournalWritesPausedError):
            async with tmp_db.transaction() as conn:
                await record(conn, bad_event)
