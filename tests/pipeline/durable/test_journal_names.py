"""The task ``NameResolver`` (AD-30): ``resolve_task_name`` in isolation, and
``register_task_name_resolver`` wired end-to-end through ``journal.narrate()``
against a real ``tmp_db``/``DurableTaskStore``.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef, narrate
from stackowl.journal.narrator import reset_name_resolvers_for_tests
from stackowl.journal.task_events import TaskEnqueuedAttrs
from stackowl.pipeline.durable.journal_names import (
    register_task_name_resolver,
    resolve_task_name,
)
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_name_resolvers() -> Generator[None]:
    """This module registers a real global resolver -- keep it from leaking
    into other tests in ``tests/pipeline/durable/`` (same convention as
    ``tests/journal/conftest.py``'s per-test reset)."""
    reset_name_resolvers_for_tests()
    yield
    reset_name_resolvers_for_tests()


class TestResolveTaskName:
    async def test_returns_the_goal_for_an_existing_task(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="name-1", goal="Buy milk", status="pending"))

        name = await resolve_task_name(store, "name-1")
        assert name == "Buy milk"

    async def test_truncates_a_goal_longer_than_64_chars(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        long_goal = "x" * 100
        await store.enqueue(DurableTask(task_id="name-2", goal=long_goal, status="pending"))

        name = await resolve_task_name(store, "name-2")
        assert name == long_goal[:64]
        assert len(name) == 64

    async def test_returns_none_for_a_missing_task(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        name = await resolve_task_name(store, "does-not-exist")
        assert name is None


class TestRegisterTaskNameResolverWiredThroughNarrate:
    async def test_narrate_names_a_real_task_by_its_goal(self, tmp_db: DbPool) -> None:
        store = DurableTaskStore(tmp_db)
        await store.enqueue(DurableTask(task_id="wired-1", goal="Ship the report", status="pending"))

        register_task_name_resolver(tmp_db)

        event = JournalEvent(
            type="task.enqueued",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="wired-1",
            outcome=Outcome.PENDING,
            record_ref=RecordRef(kind="sqlite", locator={"table": "tasks", "task_id": "wired-1"}),
            attrs=TaskEnqueuedAttrs(trigger_kind="chat", depends_on_count=0, max_attempts=30),
        )
        result = await narrate(event)
        assert result.full == "Task Ship the report was queued."

    async def test_narrate_tombstones_a_task_that_no_longer_exists(self, tmp_db: DbPool) -> None:
        register_task_name_resolver(tmp_db)

        event = JournalEvent(
            type="task.enqueued",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="principal-default",
            target_kind=ActorKind.OWNER,
            target_id="does-not-exist",
            outcome=Outcome.PENDING,
            attrs=TaskEnqueuedAttrs(trigger_kind="chat", depends_on_count=0, max_attempts=30),
        )
        result = await narrate(event)
        assert result.full == "Task a retired task was queued."

    async def test_registering_twice_for_the_same_record_kind_raises(
        self, tmp_db: DbPool
    ) -> None:
        register_task_name_resolver(tmp_db)
        with pytest.raises(ValueError, match="already registered"):
            register_task_name_resolver(tmp_db)
