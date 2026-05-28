"""Exporter — assembles and writes the export archive."""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.export.sanitizer import ExportSanitizer
from stackowl.infra.observability import log
from stackowl.version import VERSION

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.memory.sqlite_bridge import MemoryBridge

try:
    import zstandard  # type: ignore[import-untyped]

    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False


def _utc_ts() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


class Exporter:
    """Assembles and writes StackOwl export archives."""

    def __init__(
        self,
        db: DbPool,
        memory_bridge: MemoryBridge | None = None,
    ) -> None:
        self._db = db
        self._memory_bridge = memory_bridge
        self._sanitizer = ExportSanitizer()

    async def export(self, output_path: Path | None = None) -> Path:
        """Export StackOwl state to a portable archive."""

        # 1. ENTRY
        log.infra.debug(
            "[export] exporter.export: entry",
            extra={"_fields": {"output_path": str(output_path)}},
        )

        # 2. DECISION — compute output path
        ts = _utc_ts()
        if output_path is None:
            from stackowl.paths import StackowlHome
            ext = "tar.zst" if _HAS_ZSTD else "tar.gz"
            output_path = StackowlHome.knowledge_dir() / f"stackowl-export-{ts}.{ext}"

        log.infra.debug(
            "[export] exporter.export: output path resolved",
            extra={"_fields": {"output_path": str(output_path), "zstd": _HAS_ZSTD}},
        )

        # 3. STEP — gather components in temp dir
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            members: dict[str, Path] = {}

            # committed_facts
            committed = await self._fetch_table_safe("committed_facts")
            committed_clean = [self._sanitizer.sanitize_dict(r) for r in committed]
            _write_json(tmp / "committed_facts.json", committed_clean)
            members["committed_facts.json"] = tmp / "committed_facts.json"

            # staged_facts
            staged = await self._fetch_table_safe("staged_facts")
            _write_json(tmp / "staged_facts.json", staged)
            members["staged_facts.json"] = tmp / "staged_facts.json"

            # owl_dna
            owl_dna = await self._fetch_table_safe("owl_dna")
            _write_json(tmp / "owl_dna.json", owl_dna)
            members["owl_dna.json"] = tmp / "owl_dna.json"

            # parliament_sessions (last 100)
            sessions = await self._fetch_table_limited("parliament_sessions", 100)
            _write_json(tmp / "parliament_sessions.json", sessions)
            members["parliament_sessions.json"] = tmp / "parliament_sessions.json"

            # audit_log
            audit = await self._fetch_table_safe("audit_log")
            _write_json(tmp / "audit_log.json", audit)
            members["audit_log.json"] = tmp / "audit_log.json"

            # stackowl.yaml config (sensitive fields replaced with keychain refs)
            config_data = self._read_config_sanitized()
            _write_json(tmp / "stackowl.yaml", config_data)
            members["stackowl.yaml"] = tmp / "stackowl.yaml"

            # build manifest
            manifest = {
                "stackowl_version": VERSION,
                "exported_at": datetime.now(tz=UTC).isoformat(),
                "files": {
                    name: _sha256_file(path) for name, path in members.items()
                },
            }
            _write_json(tmp / "export-manifest.json", manifest)
            members["export-manifest.json"] = tmp / "export-manifest.json"

            # write archive
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_archive(output_path, tmp, list(members.keys()) + ["export-manifest.json"])

        # 4. EXIT
        log.infra.info(
            "[export] exporter.export: exit",
            extra={"_fields": {"output_path": str(output_path)}},
        )
        return output_path

    async def _fetch_table_safe(self, table: str) -> list[dict]:  # type: ignore[type-arg]
        """Fetch all rows from a table, returning [] if the table doesn't exist."""
        try:
            return await self._db.fetch_all(f"SELECT * FROM {table}")  # noqa: S608
        except Exception as exc:
            log.infra.warning(
                "[export] exporter._fetch_table_safe: table unavailable — %s: %s",
                table,
                exc,
            )
            return []

    async def _fetch_table_limited(self, table: str, limit: int) -> list[dict]:  # type: ignore[type-arg]
        """Fetch the last N rows from a table, returning [] on error."""
        try:
            return await self._db.fetch_all(
                f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?",  # noqa: S608
                (limit,),
            )
        except Exception as exc:
            log.infra.warning(
                "[export] exporter._fetch_table_limited: table unavailable — %s: %s",
                table,
                exc,
            )
            return []

    def _read_config_sanitized(self) -> dict:  # type: ignore[type-arg]
        """Read stackowl.yaml and replace sensitive values with keychain refs."""
        from stackowl.paths import StackowlHome
        config_file = StackowlHome.config_file()
        if not config_file.exists():
            return {}
        try:
            import yaml  # type: ignore[import-untyped]

            with config_file.open(encoding="utf-8") as f:
                raw: dict = yaml.safe_load(f) or {}  # type: ignore[type-arg]
            return self._replace_secrets_with_keychain_refs(raw)
        except Exception as exc:
            log.infra.warning(
                "[export] exporter._read_config_sanitized: could not read config — %s", exc
            )
            return {}

    def _replace_secrets_with_keychain_refs(
        self, data: dict, _path: str = ""  # type: ignore[type-arg]
    ) -> dict:  # type: ignore[type-arg]
        """Recursively replace sensitive string values with keychain: references."""
        from stackowl.export.sanitizer import _key_is_sensitive

        result: dict = {}  # type: ignore[type-arg]
        for key, value in data.items():
            field_path = f"{_path}.{key}" if _path else key
            if isinstance(value, dict):
                result[key] = self._replace_secrets_with_keychain_refs(value, field_path)
            elif isinstance(value, str) and _key_is_sensitive(str(key)):
                result[key] = f"keychain:stackowl:{field_path}"
            else:
                result[key] = value
        return result

    def _write_archive(self, output: Path, tmp: Path, names: list[str]) -> None:
        """Write a compressed tar archive from the temp directory.

        Format is determined by the output file extension:
        - ``.tar.zst`` — zstandard compression (requires zstandard package)
        - anything else — gzip compression (stdlib fallback)
        """
        use_zstd = _HAS_ZSTD and str(output).endswith(".tar.zst")

        if use_zstd:
            import zstandard as zstd  # type: ignore[import-untyped,no-redef]

            cctx = zstd.ZstdCompressor(level=3)
            with output.open("wb") as fh, cctx.stream_writer(fh, closefd=False) as compressor:
                with tarfile.open(fileobj=compressor, mode="w|") as tar:  # type: ignore[arg-type]
                    for name in set(names):
                        member_path = tmp / name
                        if member_path.exists():
                            tar.add(member_path, arcname=name)
        else:
            with tarfile.open(output, "w:gz") as tar:
                for name in set(names):
                    member_path = tmp / name
                    if member_path.exists():
                        tar.add(member_path, arcname=name)
