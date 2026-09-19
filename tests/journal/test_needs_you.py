"""Story 3.1/3.2 -- ``journal/needs_you.py``'s open/resolve/query primitives
and the ``needs_you`` table itself (AD-28).

Proves: the table's real shape (migration 0150); the partial unique index is
the ONLY thing enforcing "one open item per target", under REAL concurrent
writers (``asyncio.gather``, mirroring
``tests/scheduler/test_poller_cas_claim.py``'s own idiom -- spec Boundaries);
``open_items()``'s ordering (high before normal, then ``opened_cursor``
ascending); the retention-hold checker's own query correctness; and (Story
3.2) the public ``resolve()`` resolver's full I/O & Edge-Case Matrix -- clean
win, concurrent answers, already-resolved/expired no-ops, version/digest
refusal, expiry-through-the-same-resolver, and the structural proof that no
answer is ever enqueued as a task.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import NeedsYouItemNotFoundError
from stackowl.journal import ActorKind, JournalEvent, Outcome, needs_you, record
from stackowl.journal.budget_events import BudgetWarningAttrs
from stackowl.journal.consent_events import ConsentRequestedAttrs
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


async def _open_incident_item(db: DbPool, target_id: str) -> str:
    """Open a real incident item (via ``heal.exhausted``) and return its id --
    the shared setup every Story 3.2 resolver test below starts from."""
    await _open_heal_exhausted(db, target_id)
    rows = await db.fetch_all(
        "SELECT id FROM needs_you WHERE dedupe_key = ?",
        (f"incident:owner:{target_id}",),
    )
    return str(rows[0]["id"])


async def _resolve_in_own_transaction(
    db: DbPool, *, item_id: str, answer: str, resolved_by: str,
    expected_version: int | None = None, expected_digest: str | None = None,
) -> needs_you.NeedsYouResolution:
    """``resolve()`` runs on the CALLER's own open transaction (its own
    docstring) -- this is that caller, one ``db.transaction()`` block per
    call, exactly the shape a real surface (Telegram, the strip) would use."""
    async with db.transaction() as conn:
        return await needs_you.resolve(
            conn, item_id=item_id, answer=answer, resolved_by=resolved_by,
            expected_version=expected_version, expected_digest=expected_digest,
        )


class TestPublicResolverWinsAndLoses:
    """AC1, AC3: a clean win writes through the one conditional UPDATE and
    records ``needs_you.resolved``; an already-settled item returns the
    winning outcome unchanged, with no further write."""

    async def test_a_clean_win_resolves_and_records_needs_you_resolved(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "clean-win")

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="acknowledged", resolved_by="owner",
        )

        assert result.outcome == "resolved"
        assert result.answer == "acknowledged"
        assert result.item_id == item_id

        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        assert row["answer"] == "acknowledged"
        assert row["resolved_by"] == "owner"
        assert row["resolved_cursor"] is not None
        assert int(row["resolved_cursor"]) != 0, "the claim sentinel must be fixed up"

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1
        assert resolved_events[0]["outcome"] == "ok"
        # The needs_you.resolved row's own cursor must match the fixed-up
        # resolved_cursor on the needs_you row -- same reasoning as Story
        # 3.1's own "same transaction" proof.
        assert int(resolved_events[0]["cursor"]) == int(row["resolved_cursor"])

    async def test_answering_an_already_resolved_item_returns_the_winning_answer_unchanged(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "already-resolved")
        first = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="first answer", resolved_by="owner",
        )
        assert first.outcome == "resolved"

        second = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="second answer", resolved_by="someone-else",
        )

        assert second.outcome == "resolved"
        assert second.answer == "first answer", "the FIRST answer must win, unchanged"

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1, "the second call must write nothing"

    async def test_answering_an_already_expired_item_returns_expired_unchanged(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "already-expired")
        async with tmp_db.transaction() as conn:
            first = await needs_you._expire_item(conn, item_id=item_id)
        assert first.outcome == "expired"

        second = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="too late", resolved_by="owner",
        )

        assert second.outcome == "expired"
        assert second.answer is None

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1, "the second call must write nothing"

    async def test_an_unknown_item_id_raises_the_typed_not_found_error(
        self, tmp_db: DbPool,
    ) -> None:
        async with tmp_db.transaction() as conn:
            with pytest.raises(NeedsYouItemNotFoundError):
                await needs_you.resolve(
                    conn, item_id="does-not-exist", answer="x", resolved_by="owner",
                )

    async def test_waiter_kind_and_waiter_id_round_trip_through_resolve(
        self, tmp_db: DbPool,
    ) -> None:
        """Every other test's item has ``waiter_kind``/``waiter_id`` NULL
        (via ``_open_incident_item``), so a swap or drop of either at any of
        ``resolve()``'s several ``NeedsYouResolution`` construction sites
        would be invisible (``None == None``). Set both to real, DISTINCT
        values via a direct SQL UPDATE (same idiom the ``expires_at`` tests
        below already use) so a swap between the two fields is caught too."""
        item_id = await _open_incident_item(tmp_db, "waiter-round-trip")
        await tmp_db.execute(
            "UPDATE needs_you SET waiter_kind = ?, waiter_id = ? WHERE id = ?",
            ("turn", "turn-abc123", item_id),
        )

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="ack", resolved_by="owner",
        )

        assert result.outcome == "resolved"
        assert result.waiter_kind == "turn"
        assert result.waiter_id == "turn-abc123"

    async def test_a_secret_shaped_answer_is_redacted_before_storage(
        self, tmp_db: DbPool,
    ) -> None:
        """``resolve()`` scans ``answer`` itself via ``redact_secret_shapes``
        (it bypasses ``record()``'s own ``attrs`` scanning entirely -- spec
        Boundaries) -- exercise that call with a real secret shape, not just
        plain text every other fixture uses. Built via concatenation, never
        one contiguous literal, so this file's own secret-shaped string is
        not itself flagged by the very scanner it is testing."""
        item_id = await _open_incident_item(tmp_db, "secret-shaped-answer")
        secret_answer = "AKIA" + "ABCDEFGHIJKLMNOP"

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer=secret_answer, resolved_by="owner",
        )

        assert result.outcome == "resolved"
        assert secret_answer not in (result.answer or "")
        assert "***" in (result.answer or "")

        rows = await tmp_db.fetch_all("SELECT answer FROM needs_you WHERE id = ?", (item_id,))
        assert secret_answer not in (rows[0]["answer"] or "")
        assert "***" in (rows[0]["answer"] or "")


class TestConcurrentAnswers:
    """AC2: two answers to the same item, via real ``asyncio.gather`` -- one
    wins, the other returns the winning outcome unchanged."""

    async def test_two_concurrent_answers_only_one_wins(self, tmp_db: DbPool) -> None:
        item_id = await _open_incident_item(tmp_db, "concurrent-answer")

        result_a, result_b = await asyncio.gather(
            _resolve_in_own_transaction(
                tmp_db, item_id=item_id, answer="yes", resolved_by="surface-a",
            ),
            _resolve_in_own_transaction(
                tmp_db, item_id=item_id, answer="no", resolved_by="surface-b",
            ),
        )

        assert result_a.outcome == "resolved"
        assert result_b.outcome == "resolved"
        # Exactly one answer actually won -- the loser's own return value
        # must carry that SAME winning answer, unchanged, never its own.
        assert result_a.answer == result_b.answer
        assert result_a.answer in ("yes", "no")

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1, "only the WINNING resolve may record needs_you.resolved"

    async def test_resolve_racing_the_expiry_sweep_both_settle_as_expired(
        self, tmp_db: DbPool,
    ) -> None:
        """A real answer arriving at the SAME moment the expiry sweep ticks
        for the same item -- ``resolve()`` (which routes through
        ``_expire_item`` itself once it sees a past ``expires_at``) racing a
        DIRECT ``_expire_item`` call (the sweep's own body), via real
        ``asyncio.gather``. Whichever wins, BOTH must settle as expired --
        the answer must never win against an already-past ``expires_at``."""
        item_id = await _open_incident_item(tmp_db, "resolve-vs-expire-race")
        await tmp_db.execute(
            "UPDATE needs_you SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", item_id),
        )

        async def _expire_in_own_transaction() -> needs_you.NeedsYouResolution:
            async with tmp_db.transaction() as conn:
                return await needs_you._expire_item(conn, item_id=item_id)

        result_a, result_b = await asyncio.gather(
            _resolve_in_own_transaction(
                tmp_db, item_id=item_id, answer="too late", resolved_by="owner",
            ),
            _expire_in_own_transaction(),
        )

        assert result_a.outcome == "expired"
        assert result_b.outcome == "expired"

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1, "only the WINNING claim may record needs_you.resolved"
        assert resolved_events[0]["outcome"] == "expired"


class TestVersionAndDigestRefusal:
    """AC4: a stale version or digest is refused, and the item is re-shown at
    its CURRENT version -- never a write."""

    async def test_version_mismatch_is_refused_with_the_current_version(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "version-mismatch")

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="too old", resolved_by="owner",
            expected_version=999,
        )

        assert result.outcome == "refused"
        assert result.version == 1, "the row's REAL current version, not the stale one submitted"

        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        assert rows[0]["resolved_cursor"] is None, "a refusal must write nothing"

    async def test_digest_mismatch_is_refused(self, tmp_db: DbPool) -> None:
        item_id = await _open_incident_item(tmp_db, "digest-mismatch")

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="wrong digest", resolved_by="owner",
            expected_digest="0000000000000000",
        )

        assert result.outcome == "refused"
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        assert rows[0]["resolved_cursor"] is None

    async def test_the_correct_digest_is_accepted(self, tmp_db: DbPool) -> None:
        item_id = await _open_incident_item(tmp_db, "digest-match")
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        correct_digest = needs_you.compute_item_digest(
            needs_you.NeedsYouKind(row["kind"]), row["record_ref"], int(row["version"]),
        )

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="right digest", resolved_by="owner",
            expected_digest=correct_digest,
        )

        assert result.outcome == "resolved"
        assert result.answer == "right digest"


class TestExpiryThroughTheSameResolver:
    """AC5: an answer arriving after ``expires_at`` resolves as ``expired``
    through the SAME conditional-UPDATE resolver, ignoring the submitted
    answer/version/digest entirely; and ``open_items()``'s own opportunistic
    sweep excludes a now-expired item from the open set."""

    async def test_answer_after_expires_at_resolves_as_expired(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "past-expiry")
        await tmp_db.execute(
            "UPDATE needs_you SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", item_id),
        )

        result = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="ignored", resolved_by="owner",
            expected_version=1,
        )

        assert result.outcome == "expired"
        assert result.answer is None

        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        assert row["answer"] is None, "the submitted answer must never land on the row"
        assert row["resolved_cursor"] is not None

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1
        assert resolved_events[0]["outcome"] == "expired"

    async def test_sweep_expired_items_expires_every_stale_open_item(
        self, tmp_db: DbPool,
    ) -> None:
        stale_id = await _open_incident_item(tmp_db, "sweep-stale")
        fresh_id = await _open_incident_item(tmp_db, "sweep-fresh")
        await tmp_db.execute(
            "UPDATE needs_you SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", stale_id),
        )

        expired_ids = await needs_you.sweep_expired_items(tmp_db)

        assert expired_ids == [stale_id]
        rows = await tmp_db.fetch_all("SELECT id, resolved_cursor FROM needs_you WHERE id = ?", (fresh_id,))
        assert rows[0]["resolved_cursor"] is None, "an item with no expires_at must survive the sweep"

    async def test_open_items_opportunistic_sweep_excludes_a_now_expired_item(
        self, tmp_db: DbPool,
    ) -> None:
        stale_id = await _open_incident_item(tmp_db, "opportunistic-sweep")
        await tmp_db.execute(
            "UPDATE needs_you SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", stale_id),
        )

        items = await needs_you.open_items(tmp_db)

        assert stale_id not in {i.id for i in items}
        rows = await tmp_db.fetch_all(
            "SELECT resolved_cursor FROM needs_you WHERE id = ?", (stale_id,),
        )
        assert rows[0]["resolved_cursor"] is not None, "the opportunistic sweep must have run"


class TestNoCommandTaskEnqueue:
    """AC6: resolve()/the expiry sweep import nothing from ``pipeline/durable/``
    or any other task-enqueue path -- proven structurally (spec Boundaries:
    "confirmed: no 'COMMAND task' concept exists anywhere in this codebase
    yet ... prove this structurally with a test"), not just by absence of a
    call in today's tests."""

    @staticmethod
    def _imported_module_names(path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                # `from stackowl.pipeline import durable` splits the module
                # (`stackowl.pipeline`) from the imported name (`durable`) --
                # the bare module alone would never contain "pipeline.durable"
                # for the offender check below, so also collect each
                # `alias.name` (mirroring the `ast.Import` branch above),
                # qualified by the module it came from.
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
        return names

    def test_needs_you_module_imports_nothing_from_pipeline_durable(self) -> None:
        path = pathlib.Path(__file__).resolve().parents[2] / "src/stackowl/journal/needs_you.py"
        names = self._imported_module_names(path)
        offenders = {n for n in names if "pipeline.durable" in n or "pipeline/durable" in n}
        assert offenders == set(), f"needs_you.py must never import a task-enqueue path: {offenders}"

    def test_needs_you_expiry_sweep_handler_imports_nothing_from_pipeline_durable(self) -> None:
        path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src/stackowl/scheduler/handlers/needs_you_expiry_sweep.py"
        )
        names = self._imported_module_names(path)
        offenders = {n for n in names if "pipeline.durable" in n or "pipeline/durable" in n}
        assert offenders == set(), (
            f"needs_you_expiry_sweep.py must never import a task-enqueue path: {offenders}"
        )


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


async def _open_approval_item(db: DbPool, target_id: str) -> None:
    """Open a real `approval` item via the REGISTERED `consent.requested`
    type (Story 3.3) -- the same generic NEEDS_YOU wiring inside record()
    every other opening event in this file already uses."""
    async with db.transaction() as conn:
        await record(conn, JournalEvent(
            type="consent.requested", schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS, actor_id="tools.consent",
            target_kind=ActorKind.OWNER, target_id=target_id,
            outcome=Outcome.OK,
            attrs=ConsentRequestedAttrs(
                tool_name="shell", channel="cli", session_key="s1", category=None,
            ),
        ))


class TestBindWaiter:
    """Story 3.3 (AD-28): bind_waiter() binds waiter_kind/waiter_id/expires_at
    onto the item record()'s own generic wiring just opened, in the SAME
    transaction -- a second conditional UPDATE on the identical dedupe_key,
    mirroring _resolve_open_item's own shape."""

    async def test_binds_onto_the_item_just_opened_in_the_same_transaction(
        self, tmp_db: DbPool,
    ) -> None:
        target_id = "bind-waiter-target"
        async with tmp_db.transaction() as conn:
            await record(conn, JournalEvent(
                type="consent.requested", schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS, actor_id="tools.consent",
                target_kind=ActorKind.OWNER, target_id=target_id,
                outcome=Outcome.OK,
                attrs=ConsentRequestedAttrs(
                    tool_name="shell", channel="cli", session_key="s1", category=None,
                ),
            ))
            item_id = await needs_you.bind_waiter(
                conn, kind=needs_you.NeedsYouKind.APPROVAL,
                target_kind="owner", target_id=target_id,
                waiter_kind=needs_you.WAITER_KIND_TURN, waiter_id="waiter-1",
                expires_at="2099-01-01T00:00:00+00:00",
            )

        assert item_id is not None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        assert row["waiter_kind"] == "turn"
        assert row["waiter_id"] == "waiter-1"
        assert row["expires_at"] == "2099-01-01T00:00:00+00:00"

    async def test_returns_none_when_nothing_is_open_for_the_dedupe_key(
        self, tmp_db: DbPool,
    ) -> None:
        async with tmp_db.transaction() as conn:
            item_id = await needs_you.bind_waiter(
                conn, kind=needs_you.NeedsYouKind.APPROVAL,
                target_kind="owner", target_id="never-opened",
                waiter_kind=needs_you.WAITER_KIND_TURN, waiter_id="waiter-x",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        assert item_id is None

    async def test_never_binds_onto_an_already_resolved_item(
        self, tmp_db: DbPool,
    ) -> None:
        target_id = "bind-waiter-resolved"
        await _open_approval_item(tmp_db, target_id)
        dedupe_key = f"approval:owner:{target_id}"
        async with tmp_db.transaction() as conn:
            await needs_you._resolve_open_item(
                conn, dedupe_key=dedupe_key, resolved_cursor=999_999,
                resolved_by="system:test",
            )

        async with tmp_db.transaction() as conn:
            item_id = await needs_you.bind_waiter(
                conn, kind=needs_you.NeedsYouKind.APPROVAL,
                target_kind="owner", target_id=target_id,
                waiter_kind=needs_you.WAITER_KIND_TURN, waiter_id="waiter-late",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        assert item_id is None


class TestExpireStrandedTurnWaiters:
    """Story 3.3 (AD-28), AC4: the boot sweep resolves every still-open
    ``waiter_kind="turn"`` item as ``expired`` UNCONDITIONALLY -- ignoring
    ``expires_at`` entirely (the in-memory waiter that opened it is provably
    gone at boot)."""

    async def test_expires_every_open_turn_waiter_ignoring_expires_at(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "stranded-turn")
        await tmp_db.execute(
            "UPDATE needs_you SET waiter_kind = ?, waiter_id = ?, "
            "expires_at = ? WHERE id = ?",
            ("turn", "turn-1", "2099-01-01T00:00:00+00:00", item_id),
        )

        expired_ids = await needs_you.expire_stranded_turn_waiters(tmp_db)

        assert expired_ids == [item_id]
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        assert row["resolved_cursor"] is not None
        assert row["resolved_by"] == "system:expire_stranded_turn_waiters"
        assert row["answer"] is None

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved' "
            "AND target_id = ?", (item_id,),
        )
        assert len(resolved_events) == 1
        assert resolved_events[0]["outcome"] == "expired"

    async def test_leaves_items_with_no_turn_waiter_untouched(
        self, tmp_db: DbPool,
    ) -> None:
        no_waiter_id = await _open_incident_item(tmp_db, "no-waiter")
        durable_id = await _open_incident_item(tmp_db, "durable-waiter")
        await tmp_db.execute(
            "UPDATE needs_you SET waiter_kind = ?, waiter_id = ? WHERE id = ?",
            ("durable", "cmd-1", durable_id),
        )

        expired_ids = await needs_you.expire_stranded_turn_waiters(tmp_db)

        assert expired_ids == []
        for item_id in (no_waiter_id, durable_id):
            rows = await tmp_db.fetch_all(
                "SELECT resolved_cursor FROM needs_you WHERE id = ?", (item_id,),
            )
            assert rows[0]["resolved_cursor"] is None

    async def test_one_bad_row_does_not_sink_the_rest_of_the_pass(
        self, tmp_db: DbPool,
    ) -> None:
        """Verification-gap: expire_stranded_turn_waiters's per-item failure
        handling (one bad row among several candidates) must never sink the
        rest of boot. A row with a corrupted `kind` value fails INSIDE
        _expire_item's own attrs construction (NeedsYouKind(claimed["kind"])
        raises ValueError) -- a real per-item failure, not a mock."""
        good_id = await _open_incident_item(tmp_db, "boot-sweep-good")
        bad_id = await _open_incident_item(tmp_db, "boot-sweep-bad")
        await tmp_db.execute(
            "UPDATE needs_you SET waiter_kind = 'turn' WHERE id IN (?, ?)",
            (good_id, bad_id),
        )
        await tmp_db.execute(
            "UPDATE needs_you SET kind = 'not-a-real-kind' WHERE id = ?",
            (bad_id,),
        )

        expired_ids = await needs_you.expire_stranded_turn_waiters(tmp_db)

        assert expired_ids == [good_id]
        rows = await tmp_db.fetch_all(
            "SELECT resolved_cursor FROM needs_you WHERE id = ?", (bad_id,),
        )
        assert rows[0]["resolved_cursor"] is None, (
            "the bad row's own transaction must have rolled back -- never "
            "left half-claimed"
        )


class TestSettleOrAbandon:
    """Story 3.3, review pass 1 Groups A+B: the shared settle-on-abandon
    primitive both real call sites' non-happy-path exits use to mark a bound
    item settled when there is no real answer."""

    async def test_settles_a_bound_item_with_no_answer(self, tmp_db: DbPool) -> None:
        item_id = await _open_incident_item(tmp_db, "settle-abandon")

        result = await needs_you.settle_or_abandon(
            tmp_db, item_id=item_id, resolved_by="system:consent_prompt_error",
        )

        assert result is not None
        assert result.outcome == "expired"
        assert result.answer is None
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE id = ?", (item_id,))
        row = rows[0]
        assert row["answer"] is None
        assert row["resolved_by"] == "system:consent_prompt_error"
        assert row["resolved_cursor"] is not None

    async def test_is_a_no_op_with_no_db_pool(self) -> None:
        result = await needs_you.settle_or_abandon(
            None, item_id="whatever", resolved_by="system:test",
        )
        assert result is None

    async def test_is_a_no_op_with_no_item_id(self, tmp_db: DbPool) -> None:
        result = await needs_you.settle_or_abandon(
            tmp_db, item_id=None, resolved_by="system:test",
        )
        assert result is None

    async def test_never_raises_on_an_unknown_item_id(self, tmp_db: DbPool) -> None:
        """Unlike resolve(), which raises NeedsYouItemNotFoundError for a
        genuine caller bug, settle_or_abandon() must never raise -- called
        from exception-handling paths at both real call sites, it must never
        become a second failure itself."""
        result = await needs_you.settle_or_abandon(
            tmp_db, item_id="does-not-exist-at-all", resolved_by="system:test",
        )
        assert result is None

    async def test_settling_an_already_resolved_item_returns_its_real_outcome(
        self, tmp_db: DbPool,
    ) -> None:
        item_id = await _open_incident_item(tmp_db, "settle-already-resolved")
        first = await _resolve_in_own_transaction(
            tmp_db, item_id=item_id, answer="already answered", resolved_by="owner",
        )
        assert first.outcome == "resolved"

        result = await needs_you.settle_or_abandon(
            tmp_db, item_id=item_id, resolved_by="system:consent_prompt_error",
        )

        assert result is not None
        assert result.outcome == "resolved"
        assert result.answer == "already answered", "must never overwrite a real answer"
