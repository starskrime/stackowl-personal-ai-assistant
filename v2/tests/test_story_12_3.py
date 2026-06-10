"""Tests for Story 12.3 — export / import / backup / restore."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

import pytest

from stackowl.exceptions import SecurityError
from stackowl.export.sanitizer import ExportSanitizer


def _open_export_tar(archive_path: Path) -> tarfile.TarFile:
    """Open an export archive, supporting both .tar.gz and .tar.zst formats."""
    name = archive_path.name
    if name.endswith(".tar.zst"):
        try:
            import zstandard as zstd  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("zstandard not installed — cannot read .tar.zst")
        dctx = zstd.ZstdDecompressor()
        fh = archive_path.open("rb")
        reader = dctx.stream_reader(fh)
        return tarfile.open(fileobj=reader, mode="r|")  # type: ignore[arg-type]
    return tarfile.open(archive_path, "r:gz")


# ---------------------------------------------------------------------------
# ExportSanitizer tests
# ---------------------------------------------------------------------------


class TestExportSanitizerText:
    def test_replaces_openai_key(self) -> None:
        sanitizer = ExportSanitizer()
        # Exactly 48 alphanumeric chars after "sk-"
        key = "sk-" + "A" * 48
        text = f"my api key is {key} and more text"
        result = sanitizer.sanitize_text(text)
        assert "<REDACTED>" in result
        assert key not in result

    def test_replaces_anthropic_key(self) -> None:
        sanitizer = ExportSanitizer()
        key = "sk-ant-" + "a" * 93
        text = f"anthropic key: {key}"
        result = sanitizer.sanitize_text(text)
        assert "<REDACTED>" in result
        assert "sk-ant-" not in result

    def test_replaces_github_pat(self) -> None:
        sanitizer = ExportSanitizer()
        key = "ghp_" + "A" * 36
        text = f"token={key}"
        result = sanitizer.sanitize_text(text)
        assert "<REDACTED>" in result
        assert key not in result

    def test_replaces_aws_key(self) -> None:
        sanitizer = ExportSanitizer()
        key = "AKIA" + "A" * 16
        text = f"aws key: {key}"
        result = sanitizer.sanitize_text(text)
        assert "<REDACTED>" in result
        assert key not in result

    def test_clean_text_unchanged(self) -> None:
        sanitizer = ExportSanitizer()
        text = "hello world, no secrets here"
        assert sanitizer.sanitize_text(text) == text


class TestExportSanitizerDict:
    def test_redacts_api_key_field(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"api_key": "sk-" + "A" * 48, "name": "test"}
        result = sanitizer.sanitize_dict(data)
        assert result["api_key"] == "<REDACTED>"
        assert result["name"] == "test"

    def test_redacts_token_field(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"access_token": "some-secret-value", "user": "bakir"}
        result = sanitizer.sanitize_dict(data)
        assert result["access_token"] == "<REDACTED>"
        assert result["user"] == "bakir"

    def test_redacts_password_field(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"password": "hunter2", "username": "admin"}
        result = sanitizer.sanitize_dict(data)
        assert result["password"] == "<REDACTED>"
        assert result["username"] == "admin"

    def test_redacts_secret_field(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"webhook_secret": "abc123", "url": "https://example.com"}
        result = sanitizer.sanitize_dict(data)
        assert result["webhook_secret"] == "<REDACTED>"

    def test_redacts_api_in_key_name(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"openai_api": "sk-" + "A" * 48}
        result = sanitizer.sanitize_dict(data)
        assert result["openai_api"] == "<REDACTED>"

    def test_recursive_dict(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"provider": {"api_key": "secret-value", "name": "openai"}}
        result = sanitizer.sanitize_dict(data)
        assert result["provider"]["api_key"] == "<REDACTED>"
        assert result["provider"]["name"] == "openai"

    def test_list_values_sanitized(self) -> None:
        sanitizer = ExportSanitizer()
        openai_key = "sk-" + "B" * 48
        data = {"items": [{"api_key": "val1"}, "clean text", openai_key]}
        result = sanitizer.sanitize_dict(data)
        # dict in list — api_key redacted
        assert result["items"][0]["api_key"] == "<REDACTED>"
        # clean string in list
        assert result["items"][1] == "clean text"
        # raw secret string in list — sanitize_text applied
        assert "<REDACTED>" in result["items"][2]

    def test_non_string_values_preserved(self) -> None:
        sanitizer = ExportSanitizer()
        data = {"count": 42, "flag": True, "ratio": 3.14}
        result = sanitizer.sanitize_dict(data)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["ratio"] == 3.14


class TestExportSanitizerCheckAndRaise:
    def test_raises_on_openai_key(self) -> None:
        sanitizer = ExportSanitizer()
        key = "sk-" + "A" * 48
        with pytest.raises(SecurityError) as exc_info:
            sanitizer.check_and_raise(f"text with {key}", "test_field")
        assert exc_info.value.category == "export_sanitization_failed"
        assert "test_field" in str(exc_info.value)

    def test_raises_on_anthropic_key(self) -> None:
        sanitizer = ExportSanitizer()
        key = "sk-ant-" + "b" * 93
        with pytest.raises(SecurityError) as exc_info:
            sanitizer.check_and_raise(key, "config_field")
        assert exc_info.value.category == "export_sanitization_failed"

    def test_raises_on_github_pat(self) -> None:
        sanitizer = ExportSanitizer()
        key = "ghp_" + "C" * 36
        with pytest.raises(SecurityError):
            sanitizer.check_and_raise(f"token: {key}", "env")

    def test_raises_on_aws_key(self) -> None:
        sanitizer = ExportSanitizer()
        key = "AKIA" + "Z" * 16
        with pytest.raises(SecurityError):
            sanitizer.check_and_raise(key, "aws_section")

    def test_passes_for_clean_text(self) -> None:
        sanitizer = ExportSanitizer()
        # Should not raise
        sanitizer.check_and_raise("hello world, perfectly normal config", "field")

    def test_passes_for_empty_string(self) -> None:
        sanitizer = ExportSanitizer()
        sanitizer.check_and_raise("", "empty")

    def test_generic_pattern_does_not_trigger_raise(self) -> None:
        """Generic 40-char pattern should not trigger check_and_raise (too many false positives)."""
        sanitizer = ExportSanitizer()
        # Exactly 40 alphanumeric chars — matches generic pattern but not named ones
        value = "a" * 40
        # Should NOT raise
        sanitizer.check_and_raise(value, "some_field")


# ---------------------------------------------------------------------------
# BackupManager tests
# ---------------------------------------------------------------------------


class TestBackupManager:
    def test_backup_creates_output_dir(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager

        # Create a minimal SQLite DB
        db_path = tmp_path / "data" / "stackowl.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        output_dir = tmp_path / "backups" / "my-backup"
        manager = BackupManager(db_path=db_path)
        result = manager.backup(output_dir=output_dir)

        assert result == output_dir
        assert output_dir.exists()
        assert (output_dir / "stackowl.db").exists()
        assert (output_dir / "backup-manifest.json").exists()

    def test_backup_manifest_has_valid_sha256(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager

        db_path = tmp_path / "stackowl.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (id INTEGER)")
        conn.commit()
        conn.close()

        output_dir = tmp_path / "backup"
        manager = BackupManager(db_path=db_path)
        manager.backup(output_dir=output_dir)

        manifest = json.loads((output_dir / "backup-manifest.json").read_text(encoding="utf-8"))
        sha256 = manifest["files"]["stackowl.db"]

        # Validate it's a valid hex SHA-256 (64 hex chars)
        assert len(sha256) == 64
        assert all(c in "0123456789abcdef" for c in sha256)

        # Verify matches actual file
        actual = hashlib.sha256((output_dir / "stackowl.db").read_bytes()).hexdigest()
        assert sha256 == actual

    def test_backup_manifest_has_stackowl_version(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager
        from stackowl.version import VERSION

        db_path = tmp_path / "stackowl.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        output_dir = tmp_path / "backup"
        manager = BackupManager(db_path=db_path)
        manager.backup(output_dir=output_dir)

        manifest = json.loads((output_dir / "backup-manifest.json").read_text(encoding="utf-8"))
        assert manifest["stackowl_version"] == VERSION

    def test_backup_creates_lancedb_and_kuzu_stubs(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager

        db_path = tmp_path / "stackowl.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        output_dir = tmp_path / "backup"
        manager = BackupManager(db_path=db_path)
        manager.backup(output_dir=output_dir)

        assert (output_dir / "lancedb").is_dir()
        assert (output_dir / "kuzu").is_dir()

    def test_restore_from_valid_backup(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager

        # Setup original DB
        source_db = tmp_path / "source.db"
        conn = sqlite3.connect(str(source_db))
        conn.execute("CREATE TABLE original (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO original VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        backup_dir = tmp_path / "backup"
        manager_src = BackupManager(db_path=source_db)
        manager_src.backup(output_dir=backup_dir)

        # Restore to a different location
        restore_target = tmp_path / "restored.db"
        manager_dst = BackupManager(db_path=restore_target)
        manager_dst.restore(backup_dir)

        # Verify restored DB has the original data
        conn2 = sqlite3.connect(str(restore_target))
        rows = conn2.execute("SELECT value FROM original WHERE id=1").fetchall()
        conn2.close()
        assert rows == [("hello",)]

    def test_restore_raises_on_hash_mismatch(self, tmp_path: Path) -> None:
        from stackowl.export.backup import BackupManager

        db_path = tmp_path / "stackowl.db"
        conn = sqlite3.connect(str(db_path))
        conn.close()

        backup_dir = tmp_path / "backup"
        manager = BackupManager(db_path=db_path)
        manager.backup(output_dir=backup_dir)

        # Tamper with the DB file
        with (backup_dir / "stackowl.db").open("ab") as f:
            f.write(b"\x00\x00\x00\x00")

        restore_target = tmp_path / "restored.db"
        manager2 = BackupManager(db_path=restore_target)
        with pytest.raises(ValueError, match="integrity check failed"):
            manager2.restore(backup_dir)


# ---------------------------------------------------------------------------
# Importer tests
# ---------------------------------------------------------------------------


class TestImporter:
    async def test_raises_security_error_on_hash_mismatch(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.importer import Importer

        # Build a fake archive with a corrupt file
        archive_dir = tmp_path / "archive_src"
        archive_dir.mkdir()

        # Create a small committed_facts.json
        facts_file = archive_dir / "committed_facts.json"
        facts_file.write_text("[]", encoding="utf-8")

        # Build manifest with wrong hash
        manifest = {
            "stackowl_version": "2.0.0",
            "exported_at": "2026-01-01T00:00:00+00:00",
            "files": {
                "committed_facts.json": "a" * 64,  # wrong hash
            },
        }
        (archive_dir / "export-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        # Create tar.gz archive
        archive_path = tmp_path / "export.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(facts_file, arcname="committed_facts.json")
            tar.add(archive_dir / "export-manifest.json", arcname="export-manifest.json")

        importer = Importer(db=tmp_db)
        with pytest.raises(SecurityError) as exc_info:
            await importer.run(archive_path)
        assert exc_info.value.category == "audit_integrity_broken"
        assert "hash mismatch" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Exporter tests
# ---------------------------------------------------------------------------


class TestExporter:
    async def test_export_produces_valid_manifest(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.exporter import Exporter

        output_path = tmp_path / "test-export.tar.gz"
        exporter = Exporter(db=tmp_db)
        result = await exporter.export(output_path=output_path)

        assert result.exists()

        # Extract and verify manifest — use helper to support gz and zst
        with _open_export_tar(result) as tar:
            manifest_member = tar.getmember("export-manifest.json")
            f = tar.extractfile(manifest_member)
            assert f is not None
            manifest = json.loads(f.read().decode("utf-8"))

        assert "stackowl_version" in manifest
        assert manifest["stackowl_version"] == "2.0.0"
        assert "files" in manifest
        assert isinstance(manifest["files"], dict)

    async def test_export_manifest_has_sha256_hashes(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.exporter import Exporter

        output_path = tmp_path / "test-export2.tar.gz"
        exporter = Exporter(db=tmp_db)
        result = await exporter.export(output_path=output_path)

        with _open_export_tar(result) as tar:
            f = tar.extractfile("export-manifest.json")
            assert f is not None
            manifest = json.loads(f.read().decode("utf-8"))

        # All file hashes should be valid hex SHA-256 strings
        for _name, sha in manifest["files"].items():
            assert len(sha) == 64, f"hash for {_name!r} has unexpected length: {len(sha)}"
            assert all(c in "0123456789abcdef" for c in sha)

    async def test_export_includes_expected_members(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.exporter import Exporter

        output_path = tmp_path / "test-export3.tar.gz"
        exporter = Exporter(db=tmp_db)
        result = await exporter.export(output_path=output_path)

        with _open_export_tar(result) as tar:
            names = tar.getnames()

        expected = {
            "committed_facts.json",
            "staged_facts.json",
            "owl_dna.json",
            "parliament_sessions.json",
            "audit_log.json",
            "stackowl.yaml",
            "export-manifest.json",
        }
        assert expected.issubset(set(names))


# ---------------------------------------------------------------------------
# Migration count check
# ---------------------------------------------------------------------------


def test_migration_count(migration_runner: Any) -> None:
    """Ensure the runner applies EVERY migration .sql file on disk.

    Expected count is derived dynamically from the actual ``.sql`` files (no
    more manual bumps on every new migration).
    """
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    results = migration_runner.run()
    assert len(results) == expected
    assert all(r.action == "applied" for r in results)
