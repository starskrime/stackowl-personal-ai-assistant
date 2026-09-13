"""Migration 0143 deletes the finished one-shots the retired rule left behind.

MEASURED 2026-09-12 on the owner's box: 136 of 169 ``enabled=1`` jobs were
``rollover_summary`` one-shots parked ``status='completed'`` with a ``next_run_at``
frozen in the past, plus 32 ``job_runs`` rows of theirs. The scheduler now deletes a
finished one-shot where it records the outcome (see
``test_a_one_shot_job_does_not_re_arm.py``); this migration clears the backlog on
every install.

THE REAL RUNNER, NOT ``executescript``. The runner's connection does not enforce
foreign keys, which is exactly why the migration deletes ``job_runs`` explicitly —
a test that ran the SQL on a foreign-keys-ON connection would pass on the cascade
and prove nothing about the ordering. So the database is migrated through 0142 by
the runner, seeded with the dirty shape, and upgraded by the runner.

WHAT MUST SURVIVE is most of this file, because deleting the wrong row is the
expensive direction: a pending, running or retrying one-shot is live work; a PAUSED
one-shot (``pause()`` writes ``status='failed', enabled=0``) is the user's decision;
a FAILED one-shot whose failure was never recorded in the audit log is its own only
record (the 0075 pattern — the scheduler records and retires it at runtime); a job
without ``run_once`` is never touched whatever its status; a row whose ``params`` is
not JSON must not abort a customer's migration.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationResult, MigrationRunner

_MIGRATIONS = Path(__file__).resolve().parents[2] / "src/stackowl/db/migrations"
_NAME = "0143_finished_one_shot_jobs_are_deleted.sql"
_PAST = "2026-09-01T09:00:00+00:00"

#: job_id -> (status, enabled, params as stored). Deleted rows first.
_DELETED: dict[str, tuple[str, int, str]] = {
    "rollover-done-1": ("completed", 1, json.dumps({"run_once": True})),
    "rollover-done-2": ("completed", 1, json.dumps({"run_once": True})),
    "reminder-failed-and-recorded": ("failed", 1, json.dumps({"run_once": True})),
}
_KEPT: dict[str, tuple[str, int, str]] = {
    "one-shot-pending": ("pending", 1, json.dumps({"run_once": True})),
    "one-shot-running": ("running", 1, json.dumps({"run_once": True})),
    "one-shot-retrying": ("pending", 1, json.dumps({"run_once": True})),
    "one-shot-paused": ("failed", 0, json.dumps({"run_once": True})),
    "one-shot-failed-never-recorded": ("failed", 1, json.dumps({"run_once": True})),
    "recurring-live": ("pending", 1, json.dumps({})),
    "recurring-not-run-once": ("completed", 1, json.dumps({"goal": "g"})),
    "run-once-false": ("completed", 1, json.dumps({"run_once": False})),
    "params-not-json": ("completed", 1, "{run_once: 1"),
}
#: (event_type, target). Only a job_failed_terminal row counts as the record of a
#: terminal failure — any other event about the same job does not.
_AUDIT: list[tuple[str, str]] = [
    ("job_failed_terminal", "reminder-failed-and-recorded"),
    ("job_rearmed_one_shot", "one-shot-failed-never-recorded"),
    ("job_failed_terminal", "one-shot-paused"),
]
#: The runs of the deleted jobs: 2 + 1 + 1, and one of a live job that stays.
_RUNS = ["rollover-done-1", "rollover-done-1", "rollover-done-2",
         "reminder-failed-and-recorded", "recurring-live"]


@pytest.fixture(scope="module")
def _migrated_through_0142(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The upgrade's starting point, built by the real runner from 0001..0142."""
    root = tmp_path_factory.mktemp("before_0143")
    older = root / "migrations"
    older.mkdir()
    for path in _MIGRATIONS.glob("*.sql"):
        if path.name.split("_", 1)[0] < "0143":
            shutil.copy(path, older / path.name)
    db = root / "template.db"
    MigrationRunner(db_path=db, migrations_dir=older, backup_root=root / "backups").run()
    return db


def _seed_dirty_install(template: Path, dest: Path) -> Path:
    shutil.copyfile(template, dest)
    conn = sqlite3.connect(dest)
    try:
        for job_id, (status, enabled, params) in {**_DELETED, **_KEPT}.items():
            conn.execute(
                "INSERT INTO jobs (job_id, handler_name, schedule, idempotency_key, "
                "next_run_at, status, retry_count, retry_at, created_at, enabled, params) "
                "VALUES (?, 'rollover_summary', 'manual', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id, f"idem-{job_id}", _PAST, status,
                    1 if job_id == "one-shot-retrying" else 0,
                    "2026-09-12T10:05:00+00:00" if job_id == "one-shot-retrying" else None,
                    _PAST, enabled, params,
                ),
            )
        for n, job_id in enumerate(_RUNS):
            conn.execute(
                "INSERT INTO job_runs (run_id, job_id, idempotency_key, status, "
                "duration_ms, ran_at) VALUES (?, ?, ?, 'completed', 1.0, ?)",
                (f"run-{n}", job_id, f"idem-{job_id}@{_PAST}", _PAST),
            )
        for event_type, target in _AUDIT:
            conn.execute(
                "INSERT INTO audit_log (event_type, actor, target, timestamp, details) "
                "VALUES (?, 'scheduler', ?, 0, '{}')",
                (event_type, target),
            )
        conn.commit()
    finally:
        conn.close()
    return dest


def _upgrade(db: Path, tmp_path: Path) -> dict[str, MigrationResult]:
    results = MigrationRunner(
        db_path=db, migrations_dir=_MIGRATIONS, backup_root=tmp_path / "backups"
    ).run()
    return {r.name: r for r in results}


def _job_ids(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT job_id FROM jobs")}
    finally:
        conn.close()


def _run_job_ids(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in conn.execute("SELECT job_id FROM job_runs"))
    finally:
        conn.close()


def test_the_finished_one_shots_and_their_runs_are_deleted(
    _migrated_through_0142: Path, tmp_path: Path
) -> None:
    db = _seed_dirty_install(_migrated_through_0142, tmp_path / "dirty.db")

    result = _upgrade(db, tmp_path)[_NAME]

    assert result.action == "applied"
    assert _job_ids(db).isdisjoint(_DELETED), "a finished one-shot survived the upgrade"
    assert _run_job_ids(db) == ["recurring-live"], (
        "the runner does not enforce foreign keys, so a finished one-shot's job_runs "
        "must be deleted explicitly or they are orphaned"
    )
    assert result.rows_changed == (4, 3), (
        "the boot log must carry what the migration deleted: job_runs first, then jobs"
    )


def test_live_paused_unrecorded_recurring_and_malformed_rows_are_untouched(
    _migrated_through_0142: Path, tmp_path: Path
) -> None:
    """The expensive direction. Every row here is live work, the user's decision, the
    only record of its own failure, not a one-shot, or unreadable — none of them is
    finished work this migration may delete."""
    db = _seed_dirty_install(_migrated_through_0142, tmp_path / "dirty.db")

    _upgrade(db, tmp_path)

    assert _job_ids(db) == set(_KEPT)


def test_a_second_run_deletes_nothing(
    _migrated_through_0142: Path, tmp_path: Path
) -> None:
    """Idempotent twice over: the runner skips an applied migration, and the SQL
    itself, run again on the upgraded database, changes no row."""
    db = _seed_dirty_install(_migrated_through_0142, tmp_path / "dirty.db")
    _upgrade(db, tmp_path)

    again = _upgrade(db, tmp_path)[_NAME]
    assert again.action == "skipped"
    assert again.rows_changed == ()

    conn = sqlite3.connect(db)
    try:
        before = conn.total_changes
        conn.executescript((_MIGRATIONS / _NAME).read_text(encoding="utf-8"))
        assert conn.total_changes == before, "a re-run of 0143 deleted something"
    finally:
        conn.close()
    assert _job_ids(db) == set(_KEPT)
