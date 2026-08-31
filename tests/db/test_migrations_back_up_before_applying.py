"""A migration may not touch the database until a verified backup exists.

EARNED 2026-08-30, twice over.

Migration 0112 deleted 242,477 facts and its own message says "a routine
pre-change database backup was taken, as before every migration." There is no
such routine: ``src/stackowl/db/migrations/runner.py`` contained no backup, no
snapshot and no copy. The backup was taken by hand, and the sentence describes
a platform behaviour that has never existed.

The second half is why it matters now. The 2026-08-30 shell guard forces every
data change through a migration, so migrations became the ONLY write path for
destructive changes — and that path had no backup. Closing one hole opened the
other wider.

``_exclusive_tx`` already gives atomicity, so a migration cannot apply HALFWAY.
It gives nothing against a migration that executes perfectly and deletes the
wrong thing, which is exactly what 0112 was.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner


def _seed_db(db_path: Path) -> None:
    """A database shaped like a real one.

    ``stackowl_meta`` is not decoration: ``_set_schema_version`` writes to it
    inside every migration's exclusive transaction, so a fixture without it
    cannot take the path production takes. Copied from 0001_initial.sql rather
    than invented, so it cannot drift from the real schema silently.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stackowl_meta (
               key        TEXT NOT NULL PRIMARY KEY,
               value      TEXT NOT NULL,
               updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
           )"""
    )
    conn.execute("INSERT OR IGNORE INTO stackowl_meta (key, value) VALUES ('schema_version', '0000')")
    conn.execute("CREATE TABLE precious (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO precious (note) VALUES ('the row 0112 would have deleted')")
    conn.commit()
    conn.close()


def _write_migration(migrations: Path, version: str, sql: str) -> None:
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / f"{version}_test_migration.sql").write_text(sql, encoding="utf-8")


def _backup_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("pre-migration-*") if p.is_dir())


# =========================================================================== #
# 1. The backup happens, and it happens BEFORE the damage
# =========================================================================== #


def test_a_pending_migration_takes_a_backup_first(tmp_path: Path) -> None:
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"
    _write_migration(migrations, "9001", "DELETE FROM precious;")

    MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    dirs = _backup_dirs(backups)
    assert len(dirs) == 1, f"expected exactly one backup, got {dirs}"
    assert (dirs[0] / "stackowl.db").is_file()


def test_the_backup_holds_the_state_BEFORE_the_migration_ran(tmp_path: Path) -> None:
    """The whole point. A backup taken after the DELETE would be worthless."""
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"
    _write_migration(migrations, "9001", "DELETE FROM precious;")

    MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    # The live database lost the row — the migration did apply.
    live = sqlite3.connect(db)
    assert live.execute("SELECT COUNT(*) FROM precious").fetchone()[0] == 0
    live.close()

    # The backup still has it.
    saved = sqlite3.connect(_backup_dirs(backups)[0] / "stackowl.db")
    assert saved.execute("SELECT COUNT(*) FROM precious").fetchone()[0] == 1
    saved.close()


# =========================================================================== #
# 2. Cost control — no pending migration, no backup
# =========================================================================== #


def test_a_boot_with_nothing_pending_takes_no_backup(tmp_path: Path) -> None:
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"
    _write_migration(migrations, "9001", "SELECT 1;")

    runner = MigrationRunner(db, migrations_dir=migrations, backup_root=backups)
    runner.run()
    assert len(_backup_dirs(backups)) == 1

    # Second boot: everything already applied.
    MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()
    assert len(_backup_dirs(backups)) == 1, "a no-op boot copied the database again"


# =========================================================================== #
# 3. Fail CLOSED — an unbacked-up migration does not run
# =========================================================================== #


def test_migrations_do_not_apply_when_the_backup_cannot_be_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"
    _write_migration(migrations, "9001", "DELETE FROM precious;")

    from stackowl.db.migrations import runner as runner_mod

    def _boom(self: object, output_dir: Path | None = None) -> Path:
        raise OSError("no space left on device")

    monkeypatch.setattr(runner_mod.BackupManager, "backup", _boom)

    with pytest.raises(RuntimeError, match="backup"):
        MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    # The row survives: nothing was applied.
    live = sqlite3.connect(db)
    assert live.execute("SELECT COUNT(*) FROM precious").fetchone()[0] == 1
    live.close()


def test_a_backup_that_is_claimed_but_absent_is_not_believed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-08-30 shape: a record naming a backup that does not exist.

    ``backup()`` returning a path is a CLAIM. The runner must observe the file.
    """
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"
    _write_migration(migrations, "9001", "DELETE FROM precious;")

    from stackowl.db.migrations import runner as runner_mod

    def _lies(self: object, output_dir: Path | None = None) -> Path:
        # Reports success, writes nothing.
        return Path(output_dir) if output_dir else Path("/nowhere")

    monkeypatch.setattr(runner_mod.BackupManager, "backup", _lies)

    with pytest.raises(RuntimeError, match="backup"):
        MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    live = sqlite3.connect(db)
    assert live.execute("SELECT COUNT(*) FROM precious").fetchone()[0] == 1
    live.close()


# =========================================================================== #
# 4. Bounded — anything that only appends will poison its reader
# =========================================================================== #


def test_pre_migration_backups_are_bounded(tmp_path: Path) -> None:
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    _seed_db(db)
    migrations = tmp_path / "migrations"

    keep = MigrationRunner.BACKUPS_RETAINED
    for i in range(keep + 3):
        _write_migration(migrations, f"90{i:02d}", "SELECT 1;")
        MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    assert len(_backup_dirs(backups)) == keep, _backup_dirs(backups)


def test_retention_only_ever_removes_its_own_backups(tmp_path: Path) -> None:
    """Scoped deletion. Something already ate one backup on this box once."""
    db = tmp_path / "stackowl.db"
    backups = tmp_path / "backups"
    backups.mkdir()
    bystander = backups / "learned-skills-purge-2026-08-29"
    bystander.mkdir()
    (bystander / "keepme.json").write_text("{}", encoding="utf-8")
    _seed_db(db)
    migrations = tmp_path / "migrations"

    for i in range(MigrationRunner.BACKUPS_RETAINED + 2):
        _write_migration(migrations, f"90{i:02d}", "SELECT 1;")
        MigrationRunner(db, migrations_dir=migrations, backup_root=backups).run()

    assert bystander.is_dir(), "retention deleted a directory it did not create"
    assert (bystander / "keepme.json").is_file()
