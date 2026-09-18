"""AD-3's upcaster tripwire (Story 2.10): "each version's model and upcaster
stay until its last row is pruned; a tripwire checks this against
``MIN(cursor)`` per version."

No REAL event type has grown a second ``schema_version`` yet (DW-16: wiring
an upcaster into a real read path is explicitly the next story to bump one's
job), so the real-data half of this check is trivially satisfied. What makes
that trivial pass HONEST rather than tautological is ``_missing_versions``
itself: proven, by the control case below, to actually report a gap when one
exists. Mirrors ``test_retention_tripwire.py``'s own convention (a real-data
assertion, plus a control that deliberately fails it).
"""

from __future__ import annotations

import pytest
from pydantic import Field

# Importing `stackowl.journal` registers every real `*_events.py` type as a
# side effect, so `get_registry()` below reflects the real, live registry.
from stackowl.journal import (
    ActorKind,
    JournalEvent,
    Outcome,
    RecordRef,
    get_registry,
    record,
)
from stackowl.journal.enums import AttentionClass, RecordKind
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventRegistry, EventTypeSpec, VersionEntry
from stackowl.journal.task_events import TaskEnqueuedAttrs

pytestmark = pytest.mark.tripwire


def _missing_versions(
    registry: EventRegistry, live_versions_by_type: dict[str, set[int]],
) -> list[tuple[str, int]]:
    """THE CHECK: for every type and every version some row is still live
    at (``MIN(cursor)`` grouped by ``(type, schema_version)`` in a real
    query -- simulated here as the caller's own ``live_versions_by_type``),
    is that version's model (current, or a kept older one via
    ``register_upcaster``) still registered? Returns every ``(type,
    version)`` pair that has live rows but no model -- AD-3's "no version
    with live rows lost its model/upcaster", violated.
    """
    missing: list[tuple[str, int]] = []
    for type_name, versions in live_versions_by_type.items():
        available = registry.versions_for(type_name)
        for version in sorted(versions):
            if version not in available:
                missing.append((type_name, version))
    return missing


class _FakeAttrsV1(JournalAttrsBase):
    label: str = Field(max_length=64)


class _FakeAttrsV2(JournalAttrsBase):
    label: str = Field(max_length=64)
    extra_field: str | None = None


def _narrate_fake(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    return f"a fake event about {name}"


class TestRealRegistryEveryTypeIsVOnly:
    def test_no_registered_type_has_a_live_row_below_its_current_version(self) -> None:
        """Every real registered type's only LIVE version, today, is its own
        current ``schema_version`` -- nothing has bumped one yet, so
        assuming "the current version is the only live one" per type is
        exactly true, and the check trivially passes."""
        registry = get_registry()
        live_versions_by_type = {
            type_name: {registry.get(type_name).schema_version}
            for type_name in registry.all_types()
        }

        missing = _missing_versions(registry, live_versions_by_type)

        assert missing == []


class TestRealDataQueriedFromJournalEvents:
    """The other real-data half AC2 literally names: "the version ... checked
    against ``MIN(cursor)``" must come from an actual ``SELECT type,
    schema_version, MIN(cursor) FROM journal_events GROUP BY type,
    schema_version`` query against real rows -- not a dict built from the
    registry's own current ``schema_version`` (that construction can only
    ever agree with itself; see ``TestRealRegistryEveryTypeIsVOnly`` above,
    which is the by-construction half AC2 is not about). Additive only: the
    synthetic/control tests elsewhere in this module are unchanged.
    """

    @pytest.mark.asyncio
    async def test_versions_live_in_journal_events_are_all_registered(
        self, tmp_db,  # noqa: ANN001
    ) -> None:
        async with tmp_db.transaction() as conn:
            for target_id in ("real-data-1", "real-data-2"):
                await record(conn, JournalEvent(
                    type="task.enqueued",
                    schema_version=1,
                    actor_kind=ActorKind.AUTONOMOUS,
                    actor_id="principal-default",
                    target_kind=ActorKind.OWNER,
                    target_id=target_id,
                    outcome=Outcome.PENDING,
                    record_ref=RecordRef(
                        kind="sqlite", locator={"table": "tasks", "task_id": target_id},
                    ),
                    attrs=TaskEnqueuedAttrs(
                        trigger_kind="chat", depends_on_count=0, max_attempts=30,
                    ),
                ))

        rows = await tmp_db.fetch_all(
            "SELECT type, schema_version, MIN(cursor) AS min_cursor "
            "FROM journal_events GROUP BY type, schema_version"
        )
        live_versions_by_type: dict[str, set[int]] = {}
        for row in rows:
            live_versions_by_type.setdefault(row["type"], set()).add(row["schema_version"])
        # The real rows just recorded are actually in the real query's result.
        assert live_versions_by_type["task.enqueued"] == {1}

        missing = _missing_versions(get_registry(), live_versions_by_type)

        assert missing == []


class TestTheControlCaseProvesTheCheckCanFail:
    def test_a_v2_only_type_with_a_simulated_v1_row_is_reported(self) -> None:
        """THE CONTROL. A throwaway registry declares ``fake.type`` only at
        v2 -- no ``register_upcaster`` call ever runs for v1. Simulating a
        live v1 row (as a real ``MIN(cursor)`` query would surface one) must
        make ``_missing_versions`` report exactly ``("fake.type", 1)`` --
        proving the assertion is a real comparison, not a tautology that
        would pass no matter what the registry held."""
        registry = EventRegistry()
        registry.register(EventTypeSpec(
            type="fake.type", schema_version=2, attrs_model=_FakeAttrsV2,
            emitting_process="test.fixture", record_kind=RecordKind.TASK,
            attention_class=AttentionClass.AMBIENT, intensity=None,
            table=None, narrate=_narrate_fake,
        ))

        missing = _missing_versions(registry, {"fake.type": {1, 2}})

        assert missing == [("fake.type", 1)]

    def test_registering_the_v1_upcaster_clears_the_report(self) -> None:
        """The other half of the control: once v1 IS kept via
        ``register_upcaster``, the same simulated live v1 row is no longer
        reported missing -- the check reacts to the registration, not to a
        hardcoded type name."""
        registry = EventRegistry()
        registry.register(EventTypeSpec(
            type="fake.type", schema_version=2, attrs_model=_FakeAttrsV2,
            emitting_process="test.fixture", record_kind=RecordKind.TASK,
            attention_class=AttentionClass.AMBIENT, intensity=None,
            table=None, narrate=_narrate_fake,
        ))
        registry.register_upcaster(
            "fake.type", 1, _FakeAttrsV1, lambda raw: {**raw, "extra_field": None},
        )

        missing = _missing_versions(registry, {"fake.type": {1, 2}})

        assert missing == []


class TestRegisterUpcasterValidation:
    def _registry_with_fake_v2(self) -> EventRegistry:
        registry = EventRegistry()
        registry.register(EventTypeSpec(
            type="fake.type", schema_version=2, attrs_model=_FakeAttrsV2,
            emitting_process="test.fixture", record_kind=RecordKind.TASK,
            attention_class=AttentionClass.AMBIENT, intensity=None,
            table=None, narrate=_narrate_fake,
        ))
        return registry

    def test_an_unregistered_type_is_refused(self) -> None:
        registry = EventRegistry()
        with pytest.raises(ValueError, match="not registered"):
            registry.register_upcaster("no.such.type", 1, _FakeAttrsV1, None)

    def test_a_model_that_is_not_a_journal_attrs_base_subclass_is_refused(self) -> None:
        registry = self._registry_with_fake_v2()

        class _NotAJournalAttrsModel:
            pass

        with pytest.raises(ValueError, match="JournalAttrsBase subclass"):
            registry.register_upcaster("fake.type", 1, _NotAJournalAttrsModel, None)  # type: ignore[arg-type]

    def test_a_version_below_one_is_refused(self) -> None:
        registry = self._registry_with_fake_v2()
        with pytest.raises(ValueError, match="not a valid schema_version"):
            registry.register_upcaster("fake.type", 0, _FakeAttrsV1, None)

    def test_a_version_not_older_than_current_is_refused(self) -> None:
        registry = self._registry_with_fake_v2()
        with pytest.raises(ValueError, match="not older"):
            registry.register_upcaster("fake.type", 2, _FakeAttrsV2, None)

    def test_a_version_newer_than_current_is_refused(self) -> None:
        registry = self._registry_with_fake_v2()
        with pytest.raises(ValueError, match="not older"):
            registry.register_upcaster("fake.type", 3, _FakeAttrsV2, None)

    def test_a_duplicate_version_is_refused(self) -> None:
        registry = self._registry_with_fake_v2()
        registry.register_upcaster("fake.type", 1, _FakeAttrsV1, None)
        with pytest.raises(ValueError, match="already registered"):
            registry.register_upcaster("fake.type", 1, _FakeAttrsV1, None)

    def test_versions_for_includes_the_current_version_with_no_upcaster(self) -> None:
        registry = self._registry_with_fake_v2()

        versions = registry.versions_for("fake.type")

        assert versions == {2: VersionEntry(model=_FakeAttrsV2, upcaster=None)}

    def test_versions_for_includes_a_kept_older_version(self) -> None:
        registry = self._registry_with_fake_v2()

        def _upcast(raw: dict) -> dict:
            return {**raw, "extra_field": None}

        registry.register_upcaster("fake.type", 1, _FakeAttrsV1, _upcast)

        versions = registry.versions_for("fake.type")

        assert set(versions) == {1, 2}
        assert versions[1] == VersionEntry(model=_FakeAttrsV1, upcaster=_upcast)
        assert versions[2] == VersionEntry(model=_FakeAttrsV2, upcaster=None)
