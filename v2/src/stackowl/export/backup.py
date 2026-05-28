"""BackupManager — atomic SQLite backup and restore with LanceDB/Kuzu stubs."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from stackowl.paths import StackowlHome

from stackowl.infra.observability import log
from stackowl.version import VERSION


def _utc_ts() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class BackupManager:
    """Atomic backup and restore for StackOwl data stores."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def backup(self, output_dir: Path | None = None) -> Path:
        """Create a backup of all StackOwl data stores.

        Returns the backup directory path.
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] backup.backup: entry",
            extra={"_fields": {"db_path": str(self._db_path), "output_dir": str(output_dir)}},
        )

        # 2. DECISION — compute output directory
        ts = _utc_ts()
        if output_dir is None:
            output_dir = StackowlHome.knowledge_dir() / "backups" / f"backup-{ts}"

        output_dir.mkdir(parents=True, exist_ok=True)
        log.infra.debug(
            "[export] backup.backup: output dir created",
            extra={"_fields": {"output_dir": str(output_dir)}},
        )

        # 3. STEP — SQLite VACUUM INTO
        db_dest = output_dir / "stackowl.db"
        if self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
                conn.execute(f"VACUUM INTO '{db_dest!s}'")
                conn.close()
                log.infra.debug(
                    "[export] backup.backup: vacuum into complete",
                    extra={"_fields": {"dest": str(db_dest)}},
                )
            except Exception as exc:
                log.infra.error("[export] backup.backup: vacuum failed — %s", exc)
                raise
        else:
            log.infra.warning(
                "[export] backup.backup: source db does not exist — creating empty placeholder",
                extra={"_fields": {"db_path": str(self._db_path)}},
            )
            db_dest.touch()

        # LanceDB stub dir
        lancedb_stub = output_dir / "lancedb"
        lancedb_stub.mkdir(exist_ok=True)
        (lancedb_stub / "README.txt").write_text(
            "LanceDB snapshot deferred — adapter snapshot() not yet implemented.\n",
            encoding="utf-8",
        )

        # Kuzu stub dir
        kuzu_stub = output_dir / "kuzu"
        kuzu_stub.mkdir(exist_ok=True)
        (kuzu_stub / "README.txt").write_text(
            "Kuzu snapshot deferred — adapter snapshot() not yet implemented.\n",
            encoding="utf-8",
        )

        # Write manifest
        db_hash = _sha256_file(db_dest)
        manifest = {
            "stackowl_version": VERSION,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "files": {
                "stackowl.db": db_hash,
            },
        }
        manifest_path = output_dir / "backup-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 4. EXIT
        log.infra.info(
            "[export] backup.backup: exit",
            extra={"_fields": {"output_dir": str(output_dir), "db_hash": db_hash}},
        )
        return output_dir

    def restore(self, backup_path: Path) -> None:
        """Restore from a backup directory.

        Takes a pre-restore snapshot before overwriting; rolls back on failure.
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] backup.restore: entry",
            extra={"_fields": {"backup_path": str(backup_path)}},
        )

        # 2. DECISION — read and verify manifest
        manifest_path = backup_path / "backup-manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"backup-manifest.json not found in {backup_path}")

        manifest: dict = json.loads(manifest_path.read_text(encoding="utf-8"))  # type: ignore[type-arg]
        expected_hash = manifest.get("files", {}).get("stackowl.db", "")
        source_db = backup_path / "stackowl.db"

        if not source_db.exists():
            raise FileNotFoundError(f"stackowl.db not found in {backup_path}")

        actual_hash = _sha256_file(source_db)
        if actual_hash != expected_hash:
            log.infra.error(
                "[export] backup.restore: hash mismatch",
                extra={"_fields": {"expected": expected_hash, "actual": actual_hash}},
            )
            raise ValueError(
                f"Backup integrity check failed: expected {expected_hash}, got {actual_hash}"
            )

        log.infra.debug("[export] backup.restore: hash verified")

        # 3. STEP — take pre-restore snapshot
        pre_restore_dir = StackowlHome.workspace() / "pre-restore-snapshot"
        try:
            self.backup(pre_restore_dir)
            log.infra.debug(
                "[export] backup.restore: pre-restore snapshot taken",
                extra={"_fields": {"snapshot_dir": str(pre_restore_dir)}},
            )
        except Exception as exc:
            log.infra.warning(
                "[export] backup.restore: pre-restore snapshot failed — %s", exc
            )

        # Replace live DB
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_db, self._db_path)
            log.infra.info(
                "[export] backup.restore: restore_complete",
                extra={"_fields": {"db_path": str(self._db_path)}},
            )
        except Exception as exc:
            log.infra.error(
                "[export] backup.restore: restore_failed_rolled_back — %s", exc
            )
            # Attempt rollback from pre-restore snapshot
            rollback_db = pre_restore_dir / "stackowl.db"
            if rollback_db.exists():
                try:
                    shutil.copy2(rollback_db, self._db_path)
                    log.infra.info("[export] backup.restore: rollback applied from pre-restore snapshot")
                except Exception as rollback_exc:
                    log.infra.error(
                        "[export] backup.restore: rollback also failed — %s", rollback_exc
                    )
            raise

        # 4. EXIT
        log.infra.debug(
            "[export] backup.restore: exit",
            extra={"_fields": {"backup_path": str(backup_path)}},
        )
