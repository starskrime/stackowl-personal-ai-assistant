"""Story 6.6 (part A) — what outlived the contradiction detector.

The ContradictionDetector tests that gave this file its name went with the
detector in D08.2 seam 3: it scanned committed_facts, empty since migration
0112, and nothing consumed its reports once the DreamWorker phases were removed.

The two migration assertions below are unrelated housekeeping and survive on
their own merits — they are why this file was trimmed rather than deleted.

The helper module it re-exported went too (2026-09-01): every fixture and double
in it (FakeBridge, FakePromoter, FakeKuzu, staged, record) served the DreamWorker
and KuzuSync handlers, which are now deleted. Neither surviving test used any of
them — the import was a re-export nothing consumed.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Migration 0017 + DreamWorkerCheckpoint shape
# ---------------------------------------------------------------------------


def test_migration_0017_file_exists() -> None:
    """T13 — migration file exists at the expected path."""
    path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
        / "0017_dreamworker.sql"
    )
    assert path.exists(), f"missing migration: {path}"


def test_migration_count_is_17(tmp_path: Path) -> None:
    """T14 — MigrationRunner discovers and runs EVERY migration .sql file.

    Name kept historical for log searchability. The expected count is now
    derived dynamically from the actual ``.sql`` files on disk (no more manual
    bumps on every new migration); the invariant under test is that the runner
    discovers all migration files with none silently skipped.
    """
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    runner = MigrationRunner(db_path=tmp_path / "count.db")
    results = runner.run()
    assert len(results) == expected
