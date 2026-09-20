"""The objective ``NameResolver`` (AD-30, Story 4.7): ``resolve_objective_name``
in isolation, and ``register_objective_name_resolver`` wired end-to-end
through ``journal.narrate()`` against a real ``tmp_db``/``ObjectiveStore``.
Mirrors ``tests/pipeline/durable/test_journal_names.py``'s own shape exactly.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef, narrate
from stackowl.journal.narrator import reset_name_resolvers_for_tests
from stackowl.journal.objective_events import ObjectiveSetAttrs
from stackowl.objectives.journal_names import (
    register_objective_name_resolver,
    resolve_objective_name,
)
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_name_resolvers() -> Generator[None]:
    """This module registers a real global resolver -- keep it from leaking
    into other tests (same convention as `tests/pipeline/durable/
    test_journal_names.py`'s own per-test reset)."""
    reset_name_resolvers_for_tests()
    yield
    reset_name_resolvers_for_tests()


def _event(objective_id: str) -> JournalEvent:
    return JournalEvent(
        type="objective.set",
        schema_version=1,
        actor_kind=ActorKind.OWNER,
        actor_id="owner",
        target_kind=ActorKind.OWNER,
        target_id=objective_id,
        outcome=Outcome.OK,
        record_ref=RecordRef(
            kind="sqlite", locator={"table": "objectives", "objective_id": objective_id},
        ),
        attrs=ObjectiveSetAttrs(command_id="cmd-1"),
    )


class TestResolveObjectiveName:
    async def test_returns_the_intent_for_an_existing_objective(self, tmp_db: DbPool) -> None:
        store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
        await store.create(
            Objective(objective_id="obj-1", owner_id=DEFAULT_PRINCIPAL_ID, intent="Watch stock")
        )

        name = await resolve_objective_name(tmp_db, "obj-1")
        assert name == "Watch stock"

    async def test_truncates_an_intent_longer_than_64_chars(self, tmp_db: DbPool) -> None:
        store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
        long_intent = "x" * 100
        await store.create(
            Objective(objective_id="obj-2", owner_id=DEFAULT_PRINCIPAL_ID, intent=long_intent)
        )

        name = await resolve_objective_name(tmp_db, "obj-2")
        assert name == long_intent[:64]
        assert len(name) == 64

    async def test_returns_none_for_a_missing_objective(self, tmp_db: DbPool) -> None:
        name = await resolve_objective_name(tmp_db, "does-not-exist")
        assert name is None

    async def test_scoped_by_owner_never_a_bare_unscoped_select(self, tmp_db: DbPool) -> None:
        """The whole point of the review fix (Story 4.7): this must go
        through ObjectiveStore's own owner-scoped read, never a raw
        `SELECT ... FROM objectives` (the tenancy tripwire this closes:
        `tests/tenancy/test_no_owner_scope_bypass.py`). Cross-owner rows are
        invisible, exactly like `ObjectiveStore.get` itself."""
        other_owner_store = ObjectiveStore(tmp_db, "some-other-owner")
        await other_owner_store.create(
            Objective(objective_id="obj-3", owner_id="some-other-owner", intent="Not mine")
        )

        name = await resolve_objective_name(tmp_db, "obj-3")
        assert name is None


class TestRegisterObjectiveNameResolverWiredThroughNarrate:
    async def test_narrate_names_a_real_objective_by_its_intent(self, tmp_db: DbPool) -> None:
        store = ObjectiveStore(tmp_db, DEFAULT_PRINCIPAL_ID)
        await store.create(
            Objective(objective_id="wired-1", owner_id=DEFAULT_PRINCIPAL_ID, intent="Ship the report")
        )
        register_objective_name_resolver(tmp_db)

        result = await narrate(_event("wired-1"))
        assert result.full == "Objective Ship the report created."

    async def test_narrate_tombstones_an_objective_that_no_longer_exists(
        self, tmp_db: DbPool
    ) -> None:
        register_objective_name_resolver(tmp_db)

        result = await narrate(_event("does-not-exist"))
        assert result.full == "Objective a retired objective created."

    async def test_registering_twice_for_the_same_record_kind_raises(
        self, tmp_db: DbPool
    ) -> None:
        register_objective_name_resolver(tmp_db)
        with pytest.raises(ValueError, match="already registered"):
            register_objective_name_resolver(tmp_db)
