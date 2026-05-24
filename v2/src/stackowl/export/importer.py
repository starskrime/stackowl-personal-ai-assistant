"""Importer — verifies and applies an export archive."""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.exceptions import SecurityError
from stackowl.infra.observability import log
from stackowl.version import VERSION

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _major(version_str: str) -> int:
    try:
        return int(version_str.split(".")[0])
    except (ValueError, IndexError):
        return 0


class Importer:
    """Verifies and applies an export archive to the StackOwl database."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def run(self, archive_path: Path, merge: bool = False) -> None:
        """Import StackOwl state from an export archive.

        Raises SecurityError on hash mismatch (tamper detection).
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] importer.run: entry",
            extra={"_fields": {"archive_path": str(archive_path), "merge": merge}},
        )

        # 2. DECISION — open archive and read manifest
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            self._extract_archive(archive_path, tmp)

            manifest_path = tmp / "export-manifest.json"
            if not manifest_path.exists():
                raise ValueError("Archive missing export-manifest.json")

            manifest: dict = json.loads(manifest_path.read_text(encoding="utf-8"))  # type: ignore[type-arg]

            # 3. STEP — verify SHA-256 hashes
            file_hashes: dict[str, str] = manifest.get("files", {})
            for name, expected_hash in file_hashes.items():
                member_path = tmp / name
                if not member_path.exists():
                    raise SecurityError(
                        f"Tamper detected: file {name} missing from archive",
                        category="audit_integrity_broken",
                    )
                actual_hash = _sha256_bytes(member_path.read_bytes())
                if actual_hash != expected_hash:
                    log.infra.error(
                        "[export] importer.run: hash mismatch",
                        extra={"_fields": {"file": name, "expected": expected_hash, "actual": actual_hash}},
                    )
                    raise SecurityError(
                        f"Tamper detected: file {name} hash mismatch",
                        category="audit_integrity_broken",
                    )

            log.infra.debug("[export] importer.run: all hashes verified")

            # version check
            archive_version = manifest.get("stackowl_version", "0.0.0")
            if _major(archive_version) != _major(VERSION):
                log.infra.warning(
                    "[export] importer.run: version mismatch",
                    extra={"_fields": {"archive": archive_version, "current": VERSION}},
                )
                answer = input(
                    f"Version mismatch (archive={archive_version}, current={VERSION})"
                    " — proceed at your own risk? Type YES to continue: "
                )
                if answer.strip() != "YES":
                    log.infra.info("[export] importer.run: aborted by user on version mismatch")
                    return

            # import committed_facts
            committed_path = tmp / "committed_facts.json"
            if committed_path.exists():
                await self._import_committed_facts(committed_path, merge=merge)

            # import owl_dna (always merged)
            dna_path = tmp / "owl_dna.json"
            if dna_path.exists():
                await self._import_owl_dna(dna_path)

        # 4. EXIT
        log.infra.info(
            "[export] importer.run: exit — import complete",
            extra={"_fields": {"archive_path": str(archive_path), "merge": merge}},
        )

    def _extract_archive(self, archive_path: Path, dest: Path) -> None:
        """Extract tar.gz or tar.zst archive to dest."""
        name = archive_path.name
        if name.endswith(".tar.zst"):
            try:
                import zstandard as zstd  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError("zstandard is required to extract .tar.zst archives") from exc
            dctx = zstd.ZstdDecompressor()
            with archive_path.open("rb") as fh:
                with dctx.stream_reader(fh) as reader:
                    with tarfile.open(fileobj=reader, mode="r|") as tar:  # type: ignore[arg-type]
                        tar.extractall(dest, filter="data")
        else:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(dest, filter="data")

    async def _import_committed_facts(self, path: Path, merge: bool) -> None:
        """Import committed facts rows into DB."""
        rows: list[dict] = json.loads(path.read_text(encoding="utf-8"))  # type: ignore[type-arg]
        if not rows:
            return

        if not merge:
            # check if table is non-empty
            existing = await self._db.fetch_all("SELECT COUNT(*) AS cnt FROM committed_facts")
            count = existing[0].get("cnt", 0) if existing else 0
            if count > 0:
                log.infra.info(
                    "[export] importer._import_committed_facts: skipping — table non-empty and merge=False"
                )
                return

        for row in rows:
            try:
                await self._db.execute(
                    "INSERT OR IGNORE INTO committed_facts "
                    "(fact_id, content, embedding, embedding_model, committed_at, source_type, source_ref, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.get("fact_id", ""),
                        row.get("content", ""),
                        row.get("embedding") or b"",
                        row.get("embedding_model", "none"),
                        row.get("committed_at", ""),
                        row.get("source_type", "user_explicit"),
                        row.get("source_ref", ""),
                        row.get("tags", "[]"),
                    ),
                )
            except Exception as exc:
                log.infra.warning(
                    "[export] importer._import_committed_facts: row insert failed — %s", exc
                )

    async def _import_owl_dna(self, path: Path) -> None:
        """Merge owl DNA rows into DB."""
        rows: list[dict] = json.loads(path.read_text(encoding="utf-8"))  # type: ignore[type-arg]
        for row in rows:
            owl_name = row.get("owl_name", "")
            dna_json = row.get("dna_json", "{}")
            try:
                await self._db.execute(
                    "INSERT OR REPLACE INTO owl_dna (owl_name, dna_json) VALUES (?, ?)",
                    (owl_name, dna_json),
                )
            except Exception as exc:
                log.infra.warning(
                    "[export] importer._import_owl_dna: row insert failed — %s", exc
                )
