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

# Per-table export registry (C7 / F131). Every exported table declares a
# sanitization STRATEGY so the default for any table is "scrubbed", never "leak":
#   flat      — sanitize_dict per row (key-redaction + value-regex). committed_facts
#               stays byte-identical because it carries no secret-shaped content.
#   json_text — JSON-decode structured TEXT columns first so structural
#               key-redaction actually fires, then value-sanitize + re-serialize.
#   free_text — sanitize_text over every string field (transcripts). The
#               generic-40 may corrupt benign 40-char tokens — an ACCEPTED,
#               documented trade-off for a security export.
#   audit     — like json_text over audit_log.details, but stamps `_redacted`
#               on any changed row and leaves integrity_hash/chain_version intact.
#               (R3) The exported audit_log is a REDACTED DERIVATIVE: it is NOT
#               chain-verifiable by design — the live DB remains source of truth.
# (filename, table, limit_or_None, strategy)
_EXPORTED_TABLES: tuple[tuple[str, str, int | None, str], ...] = (
    ("committed_facts.json", "committed_facts", None, "flat"),
    ("staged_facts.json", "staged_facts", None, "free_text"),
    ("owl_dna.json", "owl_dna", None, "flat"),
    ("parliament_sessions.json", "parliament_sessions", 100, "free_text"),
    ("audit_log.json", "audit_log", None, "audit"),
)

# Keys in a JSON-TEXT / audit row that must survive value-sanitization untouched
# (forensic metadata, not secret carriers). They do not match the sensitive-key
# denylist so they already survive sanitize_dict; listed here for the audit
# strategy's change-detection so flipping them never spuriously stamps _redacted.
_AUDIT_PRESERVE_KEYS = frozenset({"integrity_hash", "chain_version", "audit_id"})

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

            # Table-driven sanitize loop — every exported table goes through a
            # declared strategy so the default for any table is "scrubbed".
            for filename, table, limit, strategy in _EXPORTED_TABLES:
                if limit is None:
                    rows = await self._fetch_table_safe(table)
                else:
                    rows = await self._fetch_table_limited(table, limit)
                cleaned = self._sanitize_rows(table, rows, strategy)
                path = tmp / filename
                _write_json(path, cleaned)
                members[filename] = path

            # stackowl.yaml config (sensitive fields replaced with keychain refs).
            # NOT routed through sanitize_dict — _read_config_sanitized already
            # swaps secrets for keychain: refs and must keep round-trip-restore.
            config_data = self._read_config_sanitized()
            _write_json(tmp / "stackowl.yaml", config_data)
            members["stackowl.yaml"] = tmp / "stackowl.yaml"

            # Fail-closed tripwire (R4): scan every serialized member for a
            # still-matching named-vendor secret BEFORE writing the archive. A
            # hit raises SecurityError(export_sanitization_failed) — no half-
            # sanitized tar is ever produced.
            self._tripwire_scan(members)

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

    def _sanitize_rows(
        self, table: str, rows: list[dict], strategy: str  # type: ignore[type-arg]
    ) -> list[dict]:  # type: ignore[type-arg]
        """Sanitize ``rows`` per ``strategy``; the F131 leak-stop chokepoint.

        4-point logging: entry (table + row_count + strategy), exit (rows out).
        """
        # 1. ENTRY + 2. DECISION
        log.infra.debug(
            "[export] exporter._sanitize_rows: entry",
            extra={"_fields": {"table": table, "rows": len(rows), "strategy": strategy}},
        )
        if strategy == "flat":
            cleaned = [self._sanitizer.sanitize_dict(r) for r in rows]
        elif strategy == "free_text":
            cleaned = [self._sanitize_free_text_row(r) for r in rows]
        elif strategy == "audit":
            cleaned = [self._sanitize_audit_row(r) for r in rows]
        else:  # B5 — an unknown strategy must fail loud, never silent-leak.
            log.infra.error(
                "[export] exporter._sanitize_rows: unknown strategy — refusing to leak",
                extra={"_fields": {"table": table, "strategy": strategy}},
            )
            raise ValueError(f"unknown export sanitize strategy: {strategy!r}")
        # 4. EXIT
        log.infra.debug(
            "[export] exporter._sanitize_rows: exit",
            extra={"_fields": {"table": table, "rows_out": len(cleaned)}},
        )
        return cleaned

    def _sanitize_free_text_row(self, row: dict) -> dict:  # type: ignore[type-arg]
        """Sanitize every string field of a transcript-bearing row.

        JSON-TEXT structural columns (e.g. parliament ``rounds``) are decoded so
        structural key-redaction fires, then re-serialized; plain strings get
        ``sanitize_text``. Generic-40 corruption of benign tokens is the accepted,
        documented trade-off for a security export.
        """
        out: dict = {}  # type: ignore[type-arg]
        for key, value in row.items():
            if isinstance(value, str):
                out[key] = self._sanitize_maybe_json_text(value)
            else:
                out[key] = value
        return out

    def _sanitize_maybe_json_text(self, value: str) -> str:
        """If ``value`` is a JSON object/array string, decode→sanitize→re-encode.

        Falls back to plain ``sanitize_text`` for non-JSON strings. This is what
        makes structural ``_key_is_sensitive`` redaction fire on JSON-as-TEXT
        columns (R4 trap-b) instead of relying on the value-regex alone.
        """
        stripped = value.strip()
        if stripped[:1] in ("{", "["):
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return self._sanitizer.sanitize_text(value)
            cleaned = self._sanitize_structure(decoded)
            return json.dumps(cleaned, ensure_ascii=False, default=str)
        return self._sanitizer.sanitize_text(value)

    def _sanitize_structure(self, obj: object) -> object:
        """Recursively sanitize a decoded JSON structure (dict / list / str)."""
        if isinstance(obj, dict):
            return self._sanitizer.sanitize_dict(obj)
        if isinstance(obj, list):
            return [self._sanitize_structure(item) for item in obj]
        if isinstance(obj, str):
            return self._sanitizer.sanitize_text(obj)
        return obj

    def _sanitize_audit_row(self, row: dict) -> dict:  # type: ignore[type-arg]
        """Value-sanitize ``audit_log.details``; stamp ``_redacted`` on change.

        (R3) The exported audit_log is a REDACTED DERIVATIVE: a secret pasted into
        an audited detail MUST NOT leak, even though value-sanitizing the row
        makes the EXPORTED copy's hash chain non-verifiable. ``integrity_hash`` /
        ``chain_version`` keys are preserved for human forensics; the live DB
        stays the chain-verifiable source of truth.
        """
        out: dict = {}  # type: ignore[type-arg]
        changed = False
        for key, value in row.items():
            if key in _AUDIT_PRESERVE_KEYS or not isinstance(value, str):
                out[key] = value
                continue
            sanitized = self._sanitize_maybe_json_text(value)
            if sanitized != value:
                changed = True
            out[key] = sanitized
        if changed:
            out["_redacted"] = True
        return out

    def _tripwire_scan(self, members: dict[str, Path]) -> None:
        """Fail-closed post-sanitization gate (R4) over every serialized member.

        Raises ``SecurityError(export_sanitization_failed)`` if a NAMED vendor
        secret (or key-scoped entropy hit) survived sanitization, BEFORE the
        archive is written. The config file is excluded (keychain-ref path).
        """
        # 1. ENTRY
        log.infra.debug(
            "[export] exporter._tripwire_scan: entry",
            extra={"_fields": {"members": len(members)}},
        )
        for name, path in members.items():
            if name == "stackowl.yaml":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:  # B5 — never silent
                log.infra.error(
                    "[export] exporter._tripwire_scan: member unreadable",
                    exc_info=exc,
                    extra={"_fields": {"member": name}},
                )
                raise
            # check_and_raise raises on a named-vendor hit; field_name=member so a
            # key-scoped entropy gate stays inert here (member names aren't keys).
            self._sanitizer.check_and_raise(content, name)
        # 4. EXIT
        log.infra.debug("[export] exporter._tripwire_scan: exit — clean")

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
