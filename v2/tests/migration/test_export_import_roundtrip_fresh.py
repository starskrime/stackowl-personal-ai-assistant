"""Migration test — export/import round-trip on a fresh installation (Story 12.5).

Simulates moving StackOwl to a new machine:
  1. Export from source DB
  2. Import into a fresh empty DB
  3. Verify committed facts are present and intact
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.export.backup import BackupManager
from stackowl.export.exporter import Exporter
from stackowl.export.importer import Importer


def _make_db(tmp_dir: Path) -> Path:
    db_path = tmp_dir / "stackowl.db"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    MigrationRunner(db_path).run()
    return db_path


async def _make_open_pool(db_path: Path) -> DbPool:
    pool = DbPool(db_path)
    await pool.open()
    return pool


def _insert_fact(db_path: Path, fact_id: str, content: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO committed_facts (fact_id, content, embedding, embedding_model, "
        "committed_at, source_type, source_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fact_id, content, b"", "none", "2026-01-01T00:00:00", "user_explicit", ""),
    )
    conn.commit()
    conn.close()


def _count_facts(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM committed_facts").fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_backup_and_restore_roundtrip() -> None:
    """BackupManager backup → restore preserves committed facts."""
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        src_db = _make_db(tmp / "src")
        _insert_fact(src_db, "fact-001", "Boss prefers short answers")
        _insert_fact(src_db, "fact-002", "Boss works on StackOwl")

        backup_dir = tmp / "backup"
        bm = BackupManager(src_db)
        bm.backup(backup_dir)
        assert (backup_dir / "stackowl.db").exists()
        assert (backup_dir / "backup-manifest.json").exists()

        dst_db = _make_db(tmp / "dst")
        dst_bm = BackupManager(dst_db)
        dst_bm.restore(backup_dir)

        assert _count_facts(dst_db) == 2


@pytest.mark.asyncio
async def test_export_import_roundtrip_fresh_install() -> None:
    """Exporter.export() → Importer.run() on an empty DB restores committed facts."""
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        src_db = _make_db(tmp / "src")
        _insert_fact(src_db, "fact-A", "Secretary routes messages to owls")
        _insert_fact(src_db, "fact-B", "Parliament uses 3-round debate")

        pool = await _make_open_pool(src_db)
        try:
            exporter = Exporter(pool)
            archive_path = await exporter.export(tmp / "export.tar.gz")
        finally:
            await pool.close()
        assert archive_path.exists()

        import tarfile
        with tarfile.open(archive_path, "r:*") as tar:
            manifest_member = tar.getmember("export-manifest.json")
            manifest_content = json.loads(tar.extractfile(manifest_member).read())  # type: ignore[union-attr]

        assert "stackowl_version" in manifest_content
        files = manifest_content.get("files", manifest_content)
        assert any("committed_facts" in k for k in files)

        fresh_db = _make_db(tmp / "fresh")
        fresh_pool = await _make_open_pool(fresh_db)
        try:
            importer = Importer(fresh_pool)
            await importer.run(archive_path)
        finally:
            await fresh_pool.close()

        assert _count_facts(fresh_db) == 2


@pytest.mark.asyncio
async def test_export_manifest_contains_required_keys() -> None:
    """Export manifest must carry stackowl_version and SHA-256 hashes."""
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        db_path = _make_db(tmp / "db")
        pool = await _make_open_pool(db_path)
        try:
            exporter = Exporter(pool)
            archive_path = await exporter.export(tmp / "export.tar.gz")
        finally:
            await pool.close()

        import tarfile
        with tarfile.open(archive_path, "r:*") as tar:
            member = tar.getmember("export-manifest.json")
            manifest = json.loads(tar.extractfile(member).read())  # type: ignore[union-attr]

        assert "stackowl_version" in manifest
        files = manifest.get("files", {k: v for k, v in manifest.items()
                                        if k not in {"stackowl_version", "exported_at", "created_at"}})
        for fname, sha in files.items():
            assert isinstance(sha, str) and len(sha) == 64, f"bad hash for {fname}: {sha!r}"


@pytest.mark.asyncio
async def test_import_detects_tampered_archive() -> None:
    """Importer aborts with SecurityError if any file hash mismatches."""
    import io
    import tarfile

    from stackowl.exceptions import SecurityError

    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        db_path = _make_db(tmp / "db")
        pool = await _make_open_pool(db_path)
        try:
            archive_path = await Exporter(pool).export(tmp / "export.tar.gz")
        finally:
            await pool.close()

        # Tamper: rewrite the archive with a corrupted committed_facts.json
        tampered = tmp / "tampered.tar.gz"
        with tarfile.open(archive_path, "r:*") as src_tar:
            with tarfile.open(tampered, "w:gz") as dst_tar:
                for member in src_tar.getmembers():
                    if member.name == "committed_facts.json":
                        data = b'[{"corrupted": true}]'
                        member.size = len(data)
                        dst_tar.addfile(member, io.BytesIO(data))
                    else:
                        f = src_tar.extractfile(member)
                        dst_tar.addfile(member, f)

        pool2 = await _make_open_pool(db_path)
        try:
            with pytest.raises(SecurityError, match="Tamper detected"):
                await Importer(pool2).run(tampered)
        finally:
            await pool2.close()


def test_backup_manifest_sha256_valid_format() -> None:
    """backup-manifest.json SHA-256 hashes must be 64-char hex strings."""
    with tempfile.TemporaryDirectory() as tmp_raw:
        tmp = Path(tmp_raw)
        db_path = _make_db(tmp / "db")
        bm = BackupManager(db_path)
        backup_dir = bm.backup(tmp / "bk")
        manifest = json.loads((backup_dir / "backup-manifest.json").read_text())
        assert "stackowl_version" in manifest
        # Hashes are under the "files" key
        files = manifest.get("files", {})
        assert files, "manifest has no 'files' section"
        for fname, sha in files.items():
            assert isinstance(sha, str) and len(sha) == 64, f"bad hash for {fname}: {sha!r}"


def test_migration_sprint_status_exists_and_complete() -> None:
    """sprint-status.yaml exists and marks all 12 epics complete."""
    import yaml

    # Path from v2/tests/migration/this_file.py → repo root is parents[3]
    sprint_yaml = (
        Path(__file__).parents[3]
        / "_bmad-output"
        / "sprints"
        / "sprint-status.yaml"
    )
    assert sprint_yaml.exists(), f"sprint-status.yaml not found at {sprint_yaml}"
    data = yaml.safe_load(sprint_yaml.read_text())
    for epic in data["epics"]:
        assert epic["status"] == "complete", f"Epic {epic['id']} is not complete"
