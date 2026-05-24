"""AuditExportCommand — ``/audit export`` slash command with HMAC-SHA256 signature."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

from stackowl.commands.base import SlashCommand

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.pipeline.state import PipelineState

log = logging.getLogger("stackowl.audit")

_OUTPUT_RE = re.compile(r"--output\s+(\S+)")


class AuditExportCommand(SlashCommand):
    """``/audit export`` slash command — export the full audit log as signed JSON.

    Signs the export with HMAC-SHA256 using ``settings.governance.audit_export_key``
    and writes a companion ``.sig`` file alongside the JSON output.
    """

    def __init__(self, db_path: Path, export_key: str = "") -> None:
        # 1. ENTRY
        log.debug("[commands] audit_export.init: entry")
        self._db_path = db_path
        self._export_key = export_key
        # 4. EXIT
        log.debug("[commands] audit_export.init: exit")

    @property
    def command(self) -> str:
        return "audit export"

    @property
    def description(self) -> str:
        return "Export the full audit log as signed JSON (HMAC-SHA256)"

    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute /audit export [--output <path>]."""
        # 1. ENTRY
        log.debug(
            "[commands] audit_export.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        try:
            # 2. DECISION — parse output path
            match = _OUTPUT_RE.search(args)
            if match:
                out_path = Path(match.group(1))
                log.debug(
                    "[commands] audit_export.handle: decision — custom output path",
                    extra={"_fields": {"path": str(out_path)}},
                )
            else:
                out_path = Path(platformdirs.user_documents_dir()) / "audit-export.json"
                log.debug(
                    "[commands] audit_export.handle: decision — default output path",
                    extra={"_fields": {"path": str(out_path)}},
                )

            sig_path = out_path.with_suffix(out_path.suffix + ".sig")

            # 3. STEP — fetch all rows
            rows = self._fetch_all()
            log.debug(
                "[commands] audit_export.handle: step — fetched rows",
                extra={"_fields": {"row_count": len(rows)}},
            )

            # Serialize
            content = json.dumps(rows, default=str, ensure_ascii=False, indent=2).encode("utf-8")

            # Write JSON
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(content)

            # Sign with HMAC-SHA256
            key_bytes = self._export_key.encode("utf-8") if self._export_key else b""
            sig = hmac.new(key_bytes, content, hashlib.sha256).hexdigest()
            sig_path.write_text(sig, encoding="utf-8")

            result = (
                f"Audit export written to: {out_path}\n"
                f"HMAC-SHA256 signature:   {sig_path}\n"
                f"Rows exported: {len(rows)}"
            )
        except Exception as exc:
            log.error("[commands] audit_export.handle: failed", exc_info=exc)
            return f"Export failed: {exc}"

        # 4. EXIT
        log.debug(
            "[commands] audit_export.handle: exit",
            extra={"_fields": {"out_path": str(out_path), "rows": len(rows)}},
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_all(self) -> list[dict[str, object]]:
        """Return all audit_log rows as dicts, ordered by audit_id ASC."""
        log.debug("[commands] audit_export._fetch_all: entry")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY audit_id ASC"
            ).fetchall()
            result = [dict(r) for r in rows]
        finally:
            conn.close()
        log.debug(
            "[commands] audit_export._fetch_all: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result


def verify_export(export_path: Path, sig_path: Path, export_key: str) -> bool:
    """Verify HMAC-SHA256 signature of an exported audit log.

    Returns ``True`` if the signature matches, ``False`` otherwise.
    """
    log.debug(
        "[commands] audit_export.verify_export: entry",
        extra={"_fields": {"export_path": str(export_path)}},
    )
    try:
        content = export_path.read_bytes()
        stored_sig = sig_path.read_text(encoding="utf-8").strip()
        key_bytes = export_key.encode("utf-8") if export_key else b""
        expected_sig = hmac.new(key_bytes, content, hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(stored_sig, expected_sig)
    except Exception as exc:
        log.error("[commands] audit_export.verify_export: failed", exc_info=exc)
        return False
    log.debug(
        "[commands] audit_export.verify_export: exit",
        extra={"_fields": {"valid": ok}},
    )
    return ok
