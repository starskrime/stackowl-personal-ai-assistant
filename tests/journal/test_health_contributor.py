"""A ``journal.record()`` failure is never swallowed -- it degrades
:class:`JournalHealthContributor` with a remedy (NFR17).
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalEventTypeUnregisteredError
from stackowl.journal import ActorKind, JournalEvent, Outcome, record
from stackowl.journal.health import JournalHealthContributor
from stackowl.journal.task_events import TaskEnqueuedAttrs

pytestmark = pytest.mark.asyncio

# The `_reset_journal_health` autouse fixture lives in `tests/journal/conftest.py`
# and applies to every test in this package.


def _bad_event() -> JournalEvent:
    return JournalEvent(
        type="task.definitely_not_registered",
        schema_version=1,
        actor_kind=ActorKind.AUTONOMOUS,
        actor_id="principal-default",
        target_kind=ActorKind.OWNER,
        target_id="health-1",
        outcome=Outcome.OK,
        attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
    )


class TestARecordFailureDegradesTheHealthContributor:
    async def test_a_healthy_journal_reports_ok(self) -> None:
        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"
        assert status.remedy is None

    async def test_an_unregistered_type_failure_degrades_with_a_remedy(
        self, tmp_db: DbPool
    ) -> None:
        with pytest.raises(JournalEventTypeUnregisteredError):
            async with tmp_db.transaction() as conn:
                await record(conn, _bad_event())

        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"
        assert status.remedy is not None
        assert status.message is not None

    async def test_a_later_success_clears_the_degraded_state(self, tmp_db: DbPool) -> None:
        """"Consecutive" failures -- a subsequent success resets the streak, so
        a transient blip does not latch the journal degraded forever."""
        with pytest.raises(JournalEventTypeUnregisteredError):
            async with tmp_db.transaction() as conn:
                await record(conn, _bad_event())
        assert (await JournalHealthContributor().health_check()).status == "degraded"

        good_event = JournalEvent(
            type="task.enqueued",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="health-2",
            outcome=Outcome.PENDING,
            attrs=TaskEnqueuedAttrs(trigger_kind=None, depends_on_count=0, max_attempts=1),
        )
        async with tmp_db.transaction() as conn:
            await record(conn, good_event)

        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"
