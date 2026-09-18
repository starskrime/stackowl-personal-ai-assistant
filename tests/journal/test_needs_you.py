"""Story 3.1 -- ``journal/needs_you.py``'s open/resolve/query primitives and
the ``needs_you`` table itself (AD-28).

Proves: the table's real shape (migration 0150); the partial unique index is
the ONLY thing enforcing "one open item per target", under REAL concurrent
writers (``asyncio.gather``, mirroring
``tests/scheduler/test_poller_cas_claim.py``'s own idiom -- spec Boundaries);
``open_items()``'s ordering (high before normal, then ``opened_cursor``
ascending); and the retention-hold checker's own query correctness.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, needs_you, record
from stackowl.journal.budget_events import BudgetWarningAttrs
from stackowl.journal.heal_events import HealExhaustedAttrs
from stackowl.journal.retention_holds import get_retention_hold_registry

pytestmark = pytest.mark.asyncio


async def _open_heal_exhausted(db: DbPool, target_id: str, *, attempt: int = 1) -> None:
    async with db.transaction() as conn:
        await record(conn, JournalEvent(
            type="heal.exhausted", schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS, actor_id="health_sweep",
            target_kind=ActorKind.OWNER, target_id=target_id,
            outcome=Outcome.FAILED,
            attrs=HealExhaustedAttrs(attempt_count=attempt),
        ))


class TestTheNeedsYouTableShape:
    async def test_the_table_has_ad28s_full_column_list(self, tmp_db: DbPool) -> None:
        rows = await tmp_db.fetch_all("PRAGMA table_info(needs_you)", ())
        columns = {r["name"] for r in rows}
        assert columns == {
            "id", "kind", "intensity", "record_ref", "dedupe_key",
            "waiter_kind", "waiter_id", "expires_at", "version",
            "opened_cursor", "resolved_cursor", "answer", "resolved_by",
        }

    async def test_both_indexes_exist(self, tmp_db: DbPool) -> None:
        rows = await tmp_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'needs_you'",
            (),
        )
        names = {r["name"] for r in rows}
        assert "idx_needs_you_dedupe_key_open" in names
        assert "idx_needs_you_open_set" in names

    async def test_the_migration_is_idempotent(self, tmp_db: DbPool) -> None:
        """Re-running the same CREATE statements must not raise -- proves
        ``IF NOT EXISTS``/``CREATE UNIQUE INDEX IF NOT EXISTS`` guard every
        statement, the same idempotency every other migration in this repo
        carries. Reuses the runner's own tokenizer (not a naive ``";"``
        split) -- the SQL comments above carry ``--`` and free prose that a
        naive split can mis-tokenize."""
        from pathlib import Path

        from stackowl.db.migrations.runner import _split_sql

        sql = Path(
            "src/stackowl/db/migrations/0150_needs_you.sql"
        ).read_text(encoding="utf-8")
        for statement in _split_sql(sql, keep_comments=False):
            stripped = statement.strip(" ;\n")
            if stripped:
                await tmp_db.execute(stripped, ())


class TestDedupeRaceUnderRealConcurrency:
    """AD-28 Boundaries: "prove this with a real concurrent-writer test
    (mirror ``tests/scheduler/test_poller_cas_claim.py``'s ``asyncio.gather``
    idiom against a real migrated DB)". No application-level check-then-
    insert guards this -- only the partial unique index."""

    async def test_two_concurrent_give_ups_for_the_same_target_open_exactly_one_item(
        self, tmp_db: DbPool
    ) -> None:
        await asyncio.gather(
            _open_heal_exhausted(tmp_db, "race-subsystem", attempt=1),
            _open_heal_exhausted(tmp_db, "race-subsystem", attempt=2),
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:race-subsystem",),
        )
        assert len(rows) == 1, "the partial unique index must allow only ONE open item"

        opened_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened' AND target_id = ?",
            ("race-subsystem",),
        )
        assert len(opened_events) == 1, "only the WINNING open may record needs_you.opened"

    async def test_a_duplicate_give_up_while_open_opens_no_second_item(
        self, tmp_db: DbPool
    ) -> None:
        """Sequential companion to the race above -- the plain AC, easier to
        read on its own."""
        await _open_heal_exhausted(tmp_db, "seq-subsystem", attempt=1)
        await _open_heal_exhausted(tmp_db, "seq-subsystem", attempt=2)

        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:seq-subsystem",),
        )
        assert len(rows) == 1
        opened_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.opened' AND target_id = ?",
            ("seq-subsystem",),
        )
        assert len(opened_events) == 1


class TestOpenItemsOrdering:
    async def test_high_sorts_before_normal_then_by_opened_cursor_ascending(
        self, tmp_db: DbPool
    ) -> None:
        # Opened first, but NORMAL intensity -- must sort LAST.
        async with tmp_db.transaction() as conn:
            await record(conn, JournalEvent(
                type="budget.warning", schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="cost_tracker",
                target_kind=ActorKind.OWNER, target_id="2026-09-18",
                outcome=Outcome.OK,
                attrs=BudgetWarningAttrs(
                    date="2026-09-18", current_usd=0.081, limit_usd=0.10, ratio_pct=81,
                ),
            ))
        # Opened second and third, HIGH intensity -- both sort before the
        # alert above, in opened_cursor order (sub-a before sub-b).
        await _open_heal_exhausted(tmp_db, "sub-a")
        await _open_heal_exhausted(tmp_db, "sub-b")

        items = await needs_you.open_items(tmp_db)
        assert len(items) == 3
        assert [i.kind.value for i in items] == ["incident", "incident", "alert"]
        assert items[0].dedupe_key == "incident:owner:sub-a"
        assert items[1].dedupe_key == "incident:owner:sub-b"
        assert items[2].dedupe_key == "alert:owner:2026-09-18"
        # opened_cursor is genuinely ascending within the tie.
        assert items[0].opened_cursor < items[1].opened_cursor

    async def test_a_resolved_item_never_appears_in_the_open_set(
        self, tmp_db: DbPool
    ) -> None:
        await _open_heal_exhausted(tmp_db, "resolve-me")
        rows = await tmp_db.fetch_all(
            "SELECT id FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:resolve-me",),
        )
        item_id = rows[0]["id"]

        async with tmp_db.transaction() as conn:
            resolved_id = await needs_you._resolve_open_item(
                conn, dedupe_key="incident:owner:resolve-me",
                resolved_cursor=999_999, resolved_by="system:test",
            )
        assert resolved_id == item_id

        items = await needs_you.open_items(tmp_db)
        assert items == []


class TestItemViewTyping:
    """Review-pass hardening: NeedsYouItemView.kind/.intensity are the real
    enums, not plain str."""

    async def test_kind_and_intensity_are_real_enum_instances(
        self, tmp_db: DbPool
    ) -> None:
        await _open_heal_exhausted(tmp_db, "typed-view")
        items = await needs_you.open_items(tmp_db)
        assert len(items) == 1
        from stackowl.journal.enums import Intensity, NeedsYouKind

        assert isinstance(items[0].kind, NeedsYouKind)
        assert items[0].kind is NeedsYouKind.INCIDENT
        assert isinstance(items[0].intensity, Intensity)
        assert items[0].intensity is Intensity.HIGH


class TestTheResolverIsMinimalAndInternal:
    async def test_resolving_a_dedupe_key_with_nothing_open_is_a_no_op(
        self, tmp_db: DbPool
    ) -> None:
        async with tmp_db.transaction() as conn:
            resolved_id = await needs_you._resolve_open_item(
                conn, dedupe_key="incident:owner:never-opened",
                resolved_cursor=1, resolved_by="system:test",
            )
        assert resolved_id is None

    async def test_resolving_the_same_key_twice_only_the_first_wins(
        self, tmp_db: DbPool
    ) -> None:
        await _open_heal_exhausted(tmp_db, "double-resolve")

        async with tmp_db.transaction() as conn:
            first = await needs_you._resolve_open_item(
                conn, dedupe_key="incident:owner:double-resolve",
                resolved_cursor=1, resolved_by="system:first",
            )
        async with tmp_db.transaction() as conn:
            second = await needs_you._resolve_open_item(
                conn, dedupe_key="incident:owner:double-resolve",
                resolved_cursor=2, resolved_by="system:second",
            )

        assert first is not None
        assert second is None

    def test_resolved_by_beyond_the_bound_is_refused_at_the_model_level(self) -> None:
        """Review-pass hardening: NeedsYouResolvedAttrs.resolved_by now has
        AD-4's 64-char bound, matching every sibling attrs string field."""
        import pytest as _pytest
        from pydantic import ValidationError

        with _pytest.raises(ValidationError):
            needs_you.NeedsYouResolvedAttrs(
                item_id="x", kind=needs_you.NeedsYouKind.INCIDENT,
                resolved_by="x" * 65,
            )


class TestRetentionHold:
    async def test_held_cursors_is_empty_with_no_pool_wired(self) -> None:
        original = needs_you._DB_POOL
        needs_you._DB_POOL = None
        try:
            assert await needs_you._held_cursors() == frozenset()
        finally:
            needs_you._DB_POOL = original

    async def test_held_cursors_reports_every_unresolved_opened_cursor(
        self, tmp_db: DbPool
    ) -> None:
        original = needs_you._DB_POOL
        needs_you.set_db_pool(tmp_db)
        try:
            await _open_heal_exhausted(tmp_db, "held-subsystem")
            triggering = await tmp_db.fetch_all(
                "SELECT cursor FROM journal_events WHERE type = 'heal.exhausted' "
                "AND target_id = ?",
                ("held-subsystem",),
            )
            triggering_cursor = int(triggering[0]["cursor"])

            held = await needs_you._held_cursors()
            assert triggering_cursor in held
        finally:
            needs_you._DB_POOL = original

    async def test_held_cursors_excludes_resolved_items(self, tmp_db: DbPool) -> None:
        original = needs_you._DB_POOL
        needs_you.set_db_pool(tmp_db)
        try:
            await _open_heal_exhausted(tmp_db, "resolved-subsystem")
            triggering = await tmp_db.fetch_all(
                "SELECT cursor FROM journal_events WHERE type = 'heal.exhausted' "
                "AND target_id = ?",
                ("resolved-subsystem",),
            )
            triggering_cursor = int(triggering[0]["cursor"])
            async with tmp_db.transaction() as conn:
                await needs_you._resolve_open_item(
                    conn, dedupe_key="incident:owner:resolved-subsystem",
                    resolved_cursor=999_999, resolved_by="system:test",
                )

            held = await needs_you._held_cursors()
            assert triggering_cursor not in held
        finally:
            needs_you._DB_POOL = original

    def test_needs_you_is_already_registered_as_a_real_hold_source(self) -> None:
        """A boot-time import chain (``journal/__init__.py`` -> ``needs_you``)
        already registered ``"needs_you"`` into the process singleton -- a
        second registration under the same name is refused loudly, the same
        as any other duplicate (mirrors
        ``tests/scheduler/handlers/test_journal_prune.py``'s own
        ``TestDuplicateHoldSourceRegistration``, proving the SAME singleton)."""
        with pytest.raises(ValueError, match="needs_you"):
            get_retention_hold_registry().register_hold_source(
                "needs_you", lambda: frozenset()
            )


class TestDedupeKey:
    def test_dedupe_key_shape(self) -> None:
        assert needs_you._dedupe_key("incident", "owner", "db") == "incident:owner:db"
