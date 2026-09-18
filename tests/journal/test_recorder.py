"""``journal.record`` is atomic with the change it records (spec 2.1, AD-24).

The whole point of ``journal.record(conn, event)`` is transactional-outbox
semantics: it takes the CALLER's own open ``DbPool.transaction()`` connection
and never opens or commits one of its own, so it commits or rolls back with
whatever else runs in that block. These two tests are the AC's atomicity proof
directly, with no other subsystem (no ``DurableTaskStore``) in the way --
``tests/pipeline/durable/test_journal_wiring.py`` proves the real wiring.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import JournalAttentionSetByEmitterError, JournalWritesPausedError
from stackowl.journal import (
    ActorKind,
    AttentionClass,
    Intensity,
    JournalEvent,
    Outcome,
    RecordKind,
    RecordRef,
    record,
    write_gate,
)
from stackowl.journal.enums import NeedsYouKind
from stackowl.journal.heal_events import HealExhaustedAttrs
from stackowl.journal.models import JournalAttrsBase
from stackowl.journal.registry import EventTypeSpec, get_registry
from stackowl.journal.task_events import TaskDeadLetteredAttrs, TaskEnqueuedAttrs

pytestmark = pytest.mark.asyncio

# --------------------------------------------------------------------------
# Story 3.1 -- test-only registered types exercising `resolves` (AD-28's
# minimal internal resolver). Spec Boundaries: "no real 'job resumed'/'task
# requeued' emitter exists today" -- registered ONCE at import time, mirroring
# every real `*_events.py` module's own bottom-of-file `_register()` call,
# using dotted names that can never collide with a real event type.
# --------------------------------------------------------------------------


class _TestGiveUpAttrs(JournalAttrsBase):
    pass


class _TestResolvedAttrs(JournalAttrsBase):
    pass


def _narrate_test_give_up(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    return f"{name} gave up (test fixture)."


def _narrate_test_resolved(attrs: JournalAttrsBase, name: str) -> str:  # noqa: ARG001
    return f"{name} resolved (test fixture)."


def _register_test_resolves_types() -> None:
    registry = get_registry()
    if "test.story_3_1.give_up" in registry.all_types():
        return  # already registered by an earlier test module import
    registry.register(EventTypeSpec(
        type="test.story_3_1.give_up", schema_version=1, attrs_model=_TestGiveUpAttrs,
        emitting_process="tests.journal.test_recorder", record_kind=RecordKind.TASK,
        attention_class=AttentionClass.NEEDS_YOU, intensity=Intensity.HIGH,
        needs_you_kind=NeedsYouKind.INCIDENT, table=None, narrate=_narrate_test_give_up,
    ))
    registry.register(EventTypeSpec(
        type="test.story_3_1.resolved", schema_version=1, attrs_model=_TestResolvedAttrs,
        emitting_process="tests.journal.test_recorder", record_kind=RecordKind.TASK,
        attention_class=AttentionClass.AMBIENT, intensity=None,
        resolves=("test.story_3_1.give_up",), table=None, narrate=_narrate_test_resolved,
    ))


_register_test_resolves_types()


def _event(**over: object) -> JournalEvent:
    defaults: dict[str, object] = {
        "type": "task.enqueued",
        "schema_version": 1,
        "actor_kind": ActorKind.AUTONOMOUS,
        "actor_id": "principal-default",
        "target_kind": ActorKind.OWNER,
        "target_id": "t1",
        "outcome": Outcome.PENDING,
        "record_ref": RecordRef(
            kind="sqlite", locator={"table": "tasks", "task_id": "t1"},
        ),
        "attrs": TaskEnqueuedAttrs(
            trigger_kind="chat", depends_on_count=0, max_attempts=30,
        ),
    }
    defaults.update(over)
    return JournalEvent(**defaults)  # type: ignore[arg-type]


class TestTheJournalRowIsAtomicWithTheChangeItRecords:
    async def test_a_forced_rollback_leaves_no_journal_row(self, tmp_db: DbPool) -> None:
        """AC: "a forced rollback leaves no event." The body calls ``record()``
        successfully and THEN raises -- the row it just inserted must not
        survive the rollback."""
        with pytest.raises(RuntimeError, match="simulated failure"):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(target_id="rollback-1"))
                raise RuntimeError("simulated failure after record()")

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("rollback-1",)
        )
        assert rows == []

    async def test_a_commit_leaves_exactly_one_row_with_the_right_envelope(
        self, tmp_db: DbPool
    ) -> None:
        """AC: "a commit always leaves one" -- and the row carries the envelope
        fields the caller supplied, plus a UUIDv7 ``event_id``."""
        async with tmp_db.transaction() as conn:
            event_id = await record(conn, _event(target_id="commit-1"))

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("commit-1",)
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == event_id
        # UUIDv7 text: 36 chars, version nibble '7' at the documented offset.
        assert len(event_id) == 36
        assert event_id[14] == "7"
        assert row["type"] == "task.enqueued"
        assert row["schema_version"] == 1
        assert row["actor_kind"] == "autonomous"
        assert row["actor_id"] == "principal-default"
        assert row["target_kind"] == "owner"
        assert row["target_id"] == "commit-1"
        assert row["outcome"] == "pending"
        assert row["record_ref"] is not None
        # Story 2.2: attention/intensity are computed by record() itself from
        # the registry -- task.enqueued is registered AMBIENT/no-intensity.
        assert row["attention"] == "ambient"
        assert row["intensity"] is None

    async def test_two_records_in_the_same_transaction_both_survive_the_commit(
        self, tmp_db: DbPool
    ) -> None:
        """Not a single-insert primitive by accident -- proves the shared
        connection/lock plumbing tolerates more than one call per block, the
        shape ``enqueue()`` needs (its own INSERT + UPDATE + one journal row)."""
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="multi-1"))
            await record(conn, _event(target_id="multi-2"))

        rows = await tmp_db.fetch_all(
            "SELECT target_id FROM journal_events WHERE target_id IN (?, ?)",
            ("multi-1", "multi-2"),
        )
        assert {r["target_id"] for r in rows} == {"multi-1", "multi-2"}


class TestAttentionIsComputedByRecordItselfNeverByTheEmitter:
    """Story 2.2, AD-5: ``record()`` computes ``attention``/``intensity`` from
    the registered ``EventTypeSpec`` -- emitters never classify."""

    async def test_an_ambient_type_is_recorded_ambient_with_no_intensity(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="attn-ambient-1"))

        rows = await tmp_db.fetch_all(
            "SELECT attention, intensity FROM journal_events WHERE target_id = ?",
            ("attn-ambient-1",),
        )
        assert rows[0]["attention"] == "ambient"
        assert rows[0]["intensity"] is None

    async def test_a_give_up_type_is_recorded_needs_you_high(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(
                type="task.dead_lettered",
                target_id="attn-dl-1",
                outcome=Outcome.DEAD_LETTERED,
                attrs=TaskDeadLetteredAttrs(
                    task_kind="chat", attempt_count=3, max_attempts=3,
                    lease_owner="w1", failure_class="auth", permanent=True,
                ),
            ))

        rows = await tmp_db.fetch_all(
            "SELECT attention, intensity FROM journal_events WHERE target_id = ?",
            ("attn-dl-1",),
        )
        assert rows[0]["attention"] == "needs_you"
        assert rows[0]["intensity"] == "high"

    @pytest.mark.tripwire
    async def test_an_emitter_that_pre_sets_attention_is_refused_before_any_sql_runs(
        self, tmp_db: DbPool
    ) -> None:
        """Cross-cutting invariant, not path-selected -- an emitter making its
        own ambient/needs-you judgment is exactly the per-surface disagreement
        this policy exists to prevent."""
        event = _event(target_id="attn-preset-1", attention="ambient")

        with pytest.raises(JournalAttentionSetByEmitterError):
            async with tmp_db.transaction() as conn:
                await record(conn, event)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("attn-preset-1",),
        )
        assert rows == []


class TestNeedsYouWiring:
    """Story 3.1 (AD-28): ``record()``'s generic open/close wiring, driven
    entirely by registry metadata -- the piece this recorder module owns."""

    async def test_a_needs_you_event_opens_an_item_and_records_needs_you_opened(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(
                type="heal.exhausted", target_id="ny-open-1",
                outcome=Outcome.FAILED,
                attrs=HealExhaustedAttrs(attempt_count=1),
            ))

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:ny-open-1",),
        )
        assert len(item_rows) == 1
        assert item_rows[0]["intensity"] == "high"
        assert item_rows[0]["resolved_cursor"] is None

        opened_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened' "
            "AND target_id = ?",
            ("ny-open-1",),
        )
        assert len(opened_rows) == 1
        # The needs_you.opened event landed in the SAME transaction as the
        # triggering event -- both present after one commit.
        triggering_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'heal.exhausted' "
            "AND target_id = ?",
            ("ny-open-1",),
        )
        assert len(triggering_rows) == 1
        # needs_you.record_ref reuses the triggering event's own ALREADY-
        # REDACTED record_ref_json directly (needs_you.open_item's own
        # docstring) -- never re-serialized, so a needs_you row can never
        # carry an unredacted copy of a locator the journal row itself
        # redacted. `_event()`'s default carries a non-null record_ref, so
        # this proves the two columns are the exact same value, not merely
        # that both happen to be non-null.
        assert item_rows[0]["record_ref"] is not None
        assert item_rows[0]["record_ref"] == triggering_rows[0]["record_ref"]

    async def test_an_ambient_event_never_opens_an_item(self, tmp_db: DbPool) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="ny-ambient-1"))  # task.enqueued -- AMBIENT

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key LIKE ?", ("%ny-ambient-1%",),
        )
        assert item_rows == []

    async def test_a_second_give_up_for_the_same_target_opens_no_duplicate(
        self, tmp_db: DbPool
    ) -> None:
        for attempt in (1, 2):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(
                    type="heal.exhausted", target_id="ny-dup-1",
                    outcome=Outcome.FAILED,
                    attrs=HealExhaustedAttrs(attempt_count=attempt),
                ))

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:ny-dup-1",),
        )
        assert len(item_rows) == 1
        opened_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened' "
            "AND target_id = ?",
            ("ny-dup-1",),
        )
        assert len(opened_rows) == 1, "only the FIRST give-up may open an item"

    async def test_a_forced_rollback_after_a_needs_you_open_leaves_no_rows_at_all(
        self, tmp_db: DbPool
    ) -> None:
        """AD-24: the needs_you row, the needs_you.opened row AND the
        triggering row must all roll back together -- not just the
        triggering row (already proven above for the plain case)."""
        with pytest.raises(RuntimeError, match="simulated failure"):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(
                    type="heal.exhausted", target_id="ny-rollback-1",
                    outcome=Outcome.FAILED,
                    attrs=HealExhaustedAttrs(attempt_count=1),
                ))
                raise RuntimeError("simulated failure after the needs_you open")

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:ny-rollback-1",),
        )
        assert item_rows == []
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("ny-rollback-1",),
        )
        assert journal_rows == [], (
            "neither the triggering row nor needs_you.opened may survive"
        )


class TestResolvesWiring:
    """Story 3.1 (AD-28): the ``resolves`` round trip, via the test-only
    types registered at the top of this module (spec Boundaries: no real
    resolving call site exists yet -- ``RetentionHoldRegistry``'s own
    "ships correct, zero real callers" precedent)."""

    async def test_a_resolving_type_closes_the_matching_open_item(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, JournalEvent(
                type="test.story_3_1.give_up", schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="principal-default",
                target_kind=ActorKind.OWNER, target_id="resolves-target-1",
                outcome=Outcome.OK, attrs=_TestGiveUpAttrs(),
            ))
        async with tmp_db.transaction() as conn:
            await record(conn, JournalEvent(
                type="test.story_3_1.resolved", schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="principal-default",
                target_kind=ActorKind.OWNER, target_id="resolves-target-1",
                outcome=Outcome.OK, attrs=_TestResolvedAttrs(),
            ))

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:resolves-target-1",),
        )
        assert len(item_rows) == 1
        assert item_rows[0]["resolved_cursor"] is not None
        assert item_rows[0]["resolved_by"] == "system:test.story_3_1.resolved"

        resolved_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?",
            ("resolves-target-1",),
        )
        assert len(resolved_rows) == 1

    async def test_resolving_with_nothing_open_records_no_needs_you_resolved(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, JournalEvent(
                type="test.story_3_1.resolved", schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="principal-default",
                target_kind=ActorKind.OWNER, target_id="never-opened-1",
                outcome=Outcome.OK, attrs=_TestResolvedAttrs(),
            ))

        resolved_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?",
            ("never-opened-1",),
        )
        assert resolved_rows == []


class TestBypassWriteGate:
    """Story 3.1 review-pass amendment (AD-28): ``bypass_write_gate=True``
    skips ONLY the ``writes_paused()`` refusal -- every other check still
    runs -- and is scoped to exactly the one sanctioned call site
    (``_supervise_core``'s stand-down write). Unit-level proof; the real
    integration proof (a genuinely paused PROCESS-GLOBAL write-gate, driven
    through the actual ``_supervise_core`` stand-down path) lives in
    ``tests/startup/test_hello_mismatch_supervision.py``."""

    @pytest.fixture(autouse=True)
    def _reset_write_gate(self) -> object:
        write_gate.reset_for_tests()
        yield None
        write_gate.reset_for_tests()

    async def test_a_paused_gate_refuses_without_the_bypass(self, tmp_db: DbPool) -> None:
        write_gate.pause_writes("test: simulated Hello mismatch")

        with pytest.raises(JournalWritesPausedError):
            async with tmp_db.transaction() as conn:
                await record(conn, _event(target_id="gate-refused-1"))

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("gate-refused-1",),
        )
        assert rows == []

    async def test_a_paused_gate_still_writes_with_the_bypass(self, tmp_db: DbPool) -> None:
        write_gate.pause_writes("test: simulated Hello mismatch")

        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="gate-bypassed-1"), bypass_write_gate=True)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("gate-bypassed-1",),
        )
        assert len(rows) == 1

    async def test_the_bypass_skips_only_the_gate_not_other_checks(
        self, tmp_db: DbPool
    ) -> None:
        """The bypass is narrowly scoped -- an emitter that pre-sets
        attention is still refused even with ``bypass_write_gate=True``."""
        write_gate.pause_writes("test: simulated Hello mismatch")
        event = _event(target_id="gate-bypass-other-checks-1", attention="ambient")

        with pytest.raises(JournalAttentionSetByEmitterError):
            async with tmp_db.transaction() as conn:
                await record(conn, event, bypass_write_gate=True)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?",
            ("gate-bypass-other-checks-1",),
        )
        assert rows == []

    async def test_an_unpaused_gate_behaves_identically_with_or_without_the_flag(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            await record(conn, _event(target_id="gate-not-paused-1"), bypass_write_gate=True)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("gate-not-paused-1",),
        )
        assert len(rows) == 1

    async def test_the_bypass_propagates_to_the_nested_needs_you_opened_call(
        self, tmp_db: DbPool
    ) -> None:
        """Regression guard for a real bug this story's own review pass
        surfaced (found by ``tests/startup/test_hello_mismatch_supervision.py``'s
        genuinely-paused-gate integration test, not by a unit test in
        isolation): a NEEDS_YOU event recorded with ``bypass_write_gate=True``
        must ALSO bypass the gate for its own recursive ``needs_you.opened``
        nested ``record()`` call -- otherwise that nested call re-checks
        ``writes_paused()`` fresh, raises, and rolls back the WHOLE
        transaction, including the outer row the caller's bypass was meant
        to land."""
        write_gate.pause_writes("test: simulated Hello mismatch")

        async with tmp_db.transaction() as conn:
            await record(conn, _event(
                type="heal.exhausted", target_id="ny-bypass-propagates-1",
                outcome=Outcome.FAILED,
                attrs=HealExhaustedAttrs(attempt_count=1),
            ), bypass_write_gate=True)

        triggering_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'heal.exhausted' "
            "AND target_id = ?",
            ("ny-bypass-propagates-1",),
        )
        assert len(triggering_rows) == 1, "the outer, bypassed write must land"

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:ny-bypass-propagates-1",),
        )
        assert len(item_rows) == 1, "its needs_you item must also open"

        opened_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened' "
            "AND target_id = ?",
            ("ny-bypass-propagates-1",),
        )
        assert len(opened_rows) == 1, (
            "the nested needs_you.opened call must inherit the bypass too"
        )
