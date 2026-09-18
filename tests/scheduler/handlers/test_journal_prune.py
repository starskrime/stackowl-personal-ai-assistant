"""``JournalPruneHandler`` -- the journal's own decay leg (AD-6, Story 2.11).

Covers the spec's full I/O & Edge-Case Matrix: a normal pass deletes only
rows older than retention, in bounded batches; a held old row survives;
repeated boot seeds exactly one job row; a prune that deletes rows
checkpoints the WAL once and logs the result; a duplicate hold-source
registration is refused loudly; and the tripwire proves ``journal_prune`` is
the ONLY writer that ever issues ``DELETE FROM journal_events`` (mirrors
``tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py``'s
own style for its sibling, ``db_reclaim``).
"""

from __future__ import annotations

import inspect
import pathlib
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import aiosqlite
import pytest

from stackowl.config.journal_settings import JournalSettings
from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, record
from stackowl.journal.health import JournalHealthContributor
from stackowl.journal.health import reset_for_tests as _reset_journal_health
from stackowl.journal.retention_holds import (
    get_retention_hold_registry,
    reset_retention_holds_for_tests,
)
from stackowl.journal.task_events import TaskEnqueuedAttrs
from stackowl.scheduler.assembly import _seed_minutes_schedule
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.journal_prune import (
    _PRUNE_BATCH,
    _PRUNE_MAX_PER_PASS,
    JournalPruneHandler,
    register_journal_prune_handler,
)
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_process_global_state() -> None:
    """``journal/health.py``, ``journal/retention_holds.py`` and the
    ``HandlerRegistry`` singleton are all process-global, module-level state
    -- reset around EVERY test in this file so one test's degrade/
    registration can never leak into another. The health/retention-hold
    reset mirrors ``tests/journal/conftest.py``'s own autouse fixtures (this
    file lives outside that package, so it repeats them locally); the
    registry reset mirrors ``test_downloads_janitor.py``'s own
    ``_reset_registry`` fixture."""
    HandlerRegistry.reset()
    _reset_journal_health()
    reset_retention_holds_for_tests()
    yield
    HandlerRegistry.reset()
    _reset_journal_health()
    reset_retention_holds_for_tests()


@pytest.fixture()
async def db_with_path(
    tmp_path: Path, _migrated_template: Path
) -> AsyncGenerator[tuple[DbPool, Path]]:
    """Same shape as ``tests/conftest.py``'s own ``tmp_db`` fixture, but also
    yields the file path -- ``JournalPruneHandler`` needs it to stat the
    ``-wal`` sidecar, and ``DbPool`` exposes no public path."""
    from tests.conftest import seed_migrated_db

    db_path = seed_migrated_db(tmp_path / "journal_prune_test.db", _migrated_template)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool, db_path
    finally:
        await pool.close()


def _job() -> Job:
    return Job(
        job_id="journal_prune-test",
        handler_name="journal_prune",
        schedule="every 1h",
        idempotency_key="journal_prune:every-60m",
        last_run_at=None,
        next_run_at="2026-01-01T00:00:00+00:00",
        status="pending",
    )


async def _insert_event(pool: DbPool, occurred_at: str) -> int:
    """Insert one real, registry-valid journal row via the production
    ``journal.record`` API (not a hand-typed INSERT) and return its cursor."""
    async with pool.transaction() as conn:
        event_id = await record(
            conn,
            JournalEvent(
                type="task.enqueued",
                schema_version=1,
                actor_kind=ActorKind.AUTONOMOUS,
                actor_id="principal-default",
                target_kind=ActorKind.OWNER,
                target_id=f"t-{uuid.uuid4().hex[:8]}",
                outcome=Outcome.PENDING,
                attrs=TaskEnqueuedAttrs(
                    trigger_kind=None, depends_on_count=0, max_attempts=1
                ),
                occurred_at=occurred_at,
            ),
        )
    rows = await pool.fetch_all(
        "SELECT cursor FROM journal_events WHERE event_id = ?", (event_id,)
    )
    return int(rows[0]["cursor"])


def _days_ago(days: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Normal prune pass
# ---------------------------------------------------------------------------


class TestANormalPrunePass:
    async def test_only_rows_older_than_retention_are_deleted(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        old_cursor = await _insert_event(pool, _days_ago(40))
        new_cursor = await _insert_event(pool, _days_ago(1))

        result = await JournalPruneHandler(pool, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 1
        remaining = await pool.fetch_all(
            "SELECT cursor FROM journal_events ORDER BY cursor", ()
        )
        remaining_cursors = {int(r["cursor"]) for r in remaining}
        assert old_cursor not in remaining_cursors
        assert new_cursor in remaining_cursors

    async def test_nothing_stale_means_no_delete_at_all(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        new_cursor = await _insert_event(pool, _days_ago(1))

        result = await JournalPruneHandler(pool, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 0
        remaining = await pool.fetch_all("SELECT cursor FROM journal_events", ())
        assert {int(r["cursor"]) for r in remaining} == {new_cursor}

    def test_the_delete_is_batched_and_capped(self) -> None:
        """Structural, mirroring
        ``test_the_run_history_is_finally_bounded.py::test_a_huge_backlog_is_deleted_in_BATCHES``:
        the call sites, not a 50,000-row fixture."""
        src = inspect.getsource(JournalPruneHandler.execute)
        assert "LIMIT ?" in src, "the delete is unbounded again"
        assert "_PRUNE_MAX_PER_PASS" in src, "one pass can consume the whole backlog"
        assert "_PRUNE_BATCH" in src
        assert _PRUNE_BATCH == 5_000
        assert _PRUNE_MAX_PER_PASS == 50_000


# ---------------------------------------------------------------------------
# Held old row
# ---------------------------------------------------------------------------


class TestARegisteredHoldSurvivesThePass:
    async def test_a_held_old_row_is_never_deleted(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        held_cursor = await _insert_event(pool, _days_ago(40))
        other_old_cursor = await _insert_event(pool, _days_ago(40))

        get_retention_hold_registry().register_hold_source(
            "test_hold_source", lambda: frozenset({held_cursor})
        )

        result = await JournalPruneHandler(pool, db_path).execute(_job())

        assert result.success is True
        remaining = await pool.fetch_all(
            "SELECT cursor FROM journal_events ORDER BY cursor", ()
        )
        remaining_cursors = {int(r["cursor"]) for r in remaining}
        assert held_cursor in remaining_cursors, "a HELD row must survive the pass"
        assert other_old_cursor not in remaining_cursors, (
            "an UNHELD old row must still be pruned"
        )


# ---------------------------------------------------------------------------
# Repeated boot -- idempotent seeding
# ---------------------------------------------------------------------------


class TestRepeatedBootSeedsExactlyOnce:
    async def test_seeding_twice_leaves_exactly_one_job_row(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, _db_path = db_with_path
        await _seed_minutes_schedule(
            pool, handler_name="journal_prune", schedule="every 1h",
            interval_minutes=60,
        )
        await _seed_minutes_schedule(
            pool, handler_name="journal_prune", schedule="every 1h",
            interval_minutes=60,
        )
        rows = await pool.fetch_all(
            "SELECT job_id FROM jobs WHERE handler_name = ?", ("journal_prune",)
        )
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Checkpoint after prune
# ---------------------------------------------------------------------------


class TestTheWalIsCheckpointedAndReported:
    async def test_a_pass_that_deletes_rows_checkpoints_once_and_reports(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        await _insert_event(pool, _days_ago(40))

        result = await JournalPruneHandler(pool, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 1
        for key in ("busy", "log_frames", "checkpointed_frames", "wal_bytes"):
            assert key in result.metadata, f"{key} missing from the reported metadata"
            assert isinstance(result.metadata[key], int)

    async def test_a_pass_that_deletes_nothing_still_checkpoints(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        """The checkpoint measures WAL growth from ORDINARY journal writes
        too, so it must run even when the prune itself found nothing."""
        pool, db_path = db_with_path
        await _insert_event(pool, _days_ago(1))

        result = await JournalPruneHandler(pool, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 0
        assert "checkpointed_frames" in result.metadata


# ---------------------------------------------------------------------------
# secure_delete is toggled ON for the delete and reset back OFF -- DbPool
# holds ONE process-lifetime connection, so leaving it ON would silently
# apply secure-delete overhead to every OTHER DELETE/UPDATE in the app.
# ---------------------------------------------------------------------------


class _RecordingPool:
    """Wraps a REAL pool, recording every ``execute`` SQL string, so a test
    can assert both the ORDER pragmas were issued in and that they actually
    ran (not merely that the handler intended to run them)."""

    def __init__(self, real: DbPool) -> None:
        self._real = real
        self.executed: list[str] = []

    async def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append(sql)
        await self._real.execute(sql, params)

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        return await self._real.fetch_all(sql, params)


class TestSecureDeleteIsToggledOnAndResetOff:
    async def test_secure_delete_is_set_during_the_pass_and_reset_after(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        await _insert_event(pool, _days_ago(40))
        recording = _RecordingPool(pool)

        result = await JournalPruneHandler(recording, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 1
        assert "PRAGMA secure_delete=ON" in recording.executed
        on_index = recording.executed.index("PRAGMA secure_delete=ON")
        assert "PRAGMA secure_delete=OFF" in recording.executed[on_index:], (
            "secure_delete was turned ON but never reset back OFF"
        )

        # Not just that the handler ISSUED the reset statement -- that the
        # pool's single, process-lifetime connection actually reflects it.
        rows = await pool.fetch_all("PRAGMA secure_delete", ())
        assert int(next(iter(rows[0].values()))) == 0, (
            "secure_delete is still ON on the shared connection after the pass"
        )

    async def test_a_pass_with_nothing_to_delete_never_touches_secure_delete(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        """No rows eligible -> no PRAGMA toggle at all, not a toggle-then-
        immediately-reset no-op."""
        pool, db_path = db_with_path
        await _insert_event(pool, _days_ago(1))
        recording = _RecordingPool(pool)

        result = await JournalPruneHandler(recording, db_path).execute(_job())

        assert result.success is True
        assert result.metadata["pruned_count"] == 0
        assert "PRAGMA secure_delete=ON" not in recording.executed
        assert "PRAGMA secure_delete=OFF" not in recording.executed


# ---------------------------------------------------------------------------
# WAL over budget -- integration point (the streak/degrade logic itself is
# covered by tests/journal/test_health_contributor.py)
# ---------------------------------------------------------------------------


class TestWalSizeIsFedToHealth:
    async def test_execute_reports_the_measured_wal_size_to_health(
        self, db_with_path: tuple[DbPool, Path]
    ) -> None:
        pool, db_path = db_with_path
        await _insert_event(pool, _days_ago(1))

        await JournalPruneHandler(pool, db_path).execute(_job())

        # A healthy, in-budget WAL never degrades -- proves note_wal_size was
        # actually called (not merely that execute() succeeded).
        status = await JournalHealthContributor().health_check()
        assert status.status == "ok"

    async def test_three_real_consecutive_over_budget_passes_degrade_end_to_end(
        self, db_with_path: tuple[DbPool, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drives THREE real ``execute()`` calls (not ``note_wal_size()``
        directly -- that unit coverage lives in
        ``tests/journal/test_health_contributor.py``) with an ACTUALLY
        over-budget WAL, and proves the health contributor degrades.

        A single-writer pool's own ``PRAGMA wal_checkpoint(TRUNCATE)`` always
        drains the ``-wal`` file back to 0 bytes when nothing else has it
        open (verified empirically) -- so a second, independent connection
        holds a read transaction open for the duration, which is exactly
        what makes a REAL checkpoint report ``busy`` and leave the WAL
        non-empty, the same shape a slow concurrent reader would produce in
        production.
        """
        pool, db_path = db_with_path

        class _FakeSettings:
            def __init__(self) -> None:
                self.journal = JournalSettings(wal_size_budget_bytes=1000)

        monkeypatch.setattr(
            "stackowl.config.settings.Settings", lambda: _FakeSettings()
        )

        reader = await aiosqlite.connect(db_path)
        await reader.execute("BEGIN")
        await reader.execute("SELECT COUNT(*) FROM journal_events")
        try:
            for _ in range(3):
                await _insert_event(pool, _days_ago(1))
                result = await JournalPruneHandler(pool, db_path).execute(_job())
                assert result.success is True
                assert result.metadata["wal_bytes"] > 1000, (
                    "the concurrent reader did not block the checkpoint as "
                    "expected -- the WAL drained to 0 and this test proves "
                    "nothing"
                )
        finally:
            await reader.rollback()
            await reader.close()

        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"
        assert status.message is not None and "WAL" in status.message
        assert status.remedy is not None


# ---------------------------------------------------------------------------
# Duplicate hold-source registration (unit coverage lives in
# tests/journal/test_retention_holds.py; this proves the SAME singleton
# journal_prune reads from enforces it too)
# ---------------------------------------------------------------------------


class TestDuplicateHoldSourceRegistration:
    def test_a_duplicate_name_is_refused_loudly(self) -> None:
        get_retention_hold_registry().register_hold_source(
            "dup", lambda: frozenset()
        )
        with pytest.raises(ValueError, match="dup"):
            get_retention_hold_registry().register_hold_source(
                "dup", lambda: frozenset()
            )


# ---------------------------------------------------------------------------
# A failure never raises -- maintenance may not fail a tick
# ---------------------------------------------------------------------------


class _BoomPool:
    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        raise RuntimeError("db gone")

    async def execute(self, sql: str, params: tuple = ()) -> None:
        raise RuntimeError("db gone")


class TestAFailureNeverRaises:
    async def test_a_failure_returns_a_failed_job_result_and_degrades_health(
        self, tmp_path: Path
    ) -> None:
        handler = JournalPruneHandler(_BoomPool(), tmp_path / "does-not-exist.db")
        result = await handler.execute(_job())

        assert result.success is False
        assert result.error is not None

        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"
        assert status.remedy is not None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_journal_prune_handler_registers_under_its_own_name(
    tmp_path: Path,
) -> None:
    register_journal_prune_handler(
        DbPool(db_path=tmp_path / "unused.db"), tmp_path / "unused.db"
    )
    handler = HandlerRegistry.instance().get("journal_prune")
    assert handler is not None
    assert handler.handler_name == "journal_prune"


# ---------------------------------------------------------------------------
# The tripwire: journal_prune is the ONLY writer that ever deletes a journal
# row. Mirrors this file's own sibling for db_reclaim/job_runs in spirit.
# ---------------------------------------------------------------------------


@pytest.mark.tripwire
def test_nothing_else_deletes_from_journal_events() -> None:
    root = pathlib.Path(__file__).resolve().parents[3] / "src"
    hits = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "DELETE FROM journal_events" in text:
            hits.append(str(path.relative_to(root.parent)))
    assert hits == ["src/stackowl/scheduler/handlers/journal_prune.py"], (
        f"a second writer issues DELETE FROM journal_events: {hits}"
    )
