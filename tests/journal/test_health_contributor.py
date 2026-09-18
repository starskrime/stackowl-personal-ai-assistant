"""A ``journal.record()`` failure is never swallowed -- it degrades
:class:`JournalHealthContributor` with a remedy (NFR17).
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalEventTypeUnregisteredError
from stackowl.journal import ActorKind, JournalEvent, Outcome, record
from stackowl.journal.health import JournalHealthContributor, note_wal_size
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


class TestTheWalBudgetStreakDegradesTheHealthContributor:
    """AD-6, Story 2.11: the WAL file staying over its size budget across
    CONSECUTIVE ``journal_prune`` passes degrades health with a remedy. A
    single over-budget pass must not fire it -- only three in a row."""

    async def test_one_over_budget_pass_does_not_degrade(self) -> None:
        note_wal_size(200, 100)
        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"

    async def test_two_over_budget_passes_do_not_degrade(self) -> None:
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"

    async def test_three_consecutive_over_budget_passes_degrade_with_a_remedy(
        self,
    ) -> None:
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"
        assert status.remedy is not None
        assert status.message is not None

    async def test_a_pass_back_under_budget_resets_the_streak(self) -> None:
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        assert (await JournalHealthContributor().health_check()).status == "degraded"

        # Back under budget -- the streak resets and health clears, unlike
        # the LIFETIME budget-exceeded count, which never clears.
        note_wal_size(50, 100)
        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"

    async def test_the_streak_is_not_confused_with_a_record_failure(
        self, tmp_db: DbPool
    ) -> None:
        """The two signals are independent -- a WAL-budget degrade must not
        be masked by, or mask, a `record()` failure streak."""
        with pytest.raises(JournalEventTypeUnregisteredError):
            async with tmp_db.transaction() as conn:
                await record(conn, _bad_event())
        note_wal_size(200, 100)
        note_wal_size(200, 100)
        note_wal_size(200, 100)

        status = await JournalHealthContributor().health_check()
        # The consecutive-failure streak takes priority when both are set.
        assert status.status == "degraded"
        assert "record" in (status.message or "").lower()
