"""AuditCommand — ``/audit`` and ``/audit export`` slash commands."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.audit.logger import AuditLogger
    from stackowl.pipeline.state import PipelineState

_OUTPUT_RE = re.compile(r"--output\s+(\S+)")

_AUDIT_META = CommandMeta(
    grammar="verb",
    group="Security & Governance",
    subcommands=(
        SubCommand(
            name="export",
            summary="Export the full audit log as signed JSON",
            description=(
                "You write the entire audit log to a file plus an HMAC-SHA256 "
                "signature so it can be verified later. Refused if no signing "
                "key is configured — an unsigned 'signed' export is worthless."
            ),
            args=(
                Arg(
                    name="--output",
                    required=False,
                    summary="destination path (default: knowledge dir)",
                ),
            ),
            examples=(
                Example(invocation="/audit export", note="Write to the default path"),
                Example(
                    invocation="/audit export --output /tmp/audit.json",
                    note="Choose the destination",
                ),
            ),
        ),
    ),
)


class AuditCommand(SlashCommand):
    """``/audit`` slash command — show last 50 audit log entries with integrity status.

    Subcommand ``/audit export [--output <path>]`` exports the full audit log
    as signed JSON (HMAC-SHA256).  An empty or missing signing key is refused
    with an honest error — signing with ``b""`` produces a worthless signature.
    """

    def __init__(self, audit_logger: AuditLogger | None = None, export_key: str = "") -> None:
        # 1. ENTRY
        log.gateway.debug("[commands] audit.init: entry")
        self._logger = audit_logger
        self._export_key = export_key
        # 4. EXIT
        log.gateway.debug("[commands] audit.init: exit")

    @property
    def command(self) -> str:
        return "audit"

    @property
    def description(self) -> str:
        return "Show the last 50 audit log entries with integrity status"

    @property
    def meta(self) -> CommandMeta:
        return _AUDIT_META

    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute /audit (default), /audit export [--output <path>], or show
        auto-generated usage for an unknown sub-command."""
        if self._logger is None:
            return "✗ /audit: not configured"

        # 1. ENTRY
        log.gateway.debug(
            "[commands] audit.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )

        stripped = args.strip()
        if stripped == "export" or stripped.startswith("export "):
            return await self._handle_export(stripped)

        # An unrecognised token is a wrong sub-command — surface the auto-usage
        # instead of silently dumping the chain (the historical discoverability
        # gap). A bare `/audit` (no token) keeps the default tail view.
        if stripped:
            log.gateway.debug(
                "[commands] audit.handle: unknown subcommand — returning usage",
                extra={"_fields": {"token": stripped.split(maxsplit=1)[0]}},
            )
            return render_usage("audit", _AUDIT_META)

        try:
            # 2. DECISION
            log.gateway.debug("[commands] audit.handle: decision — fetching tail(50) and verifying chain")
            # 3. STEP
            rows = self._logger.tail(50)
            intact, broken_id = self._logger.verify_chain()
            lines = self._format_table(rows)
            if intact:
                lines.append("✓ Chain intact")
            else:
                lines.append(f"✗ Chain broken at record {broken_id}")
            result = "\n".join(lines)
        except Exception as exc:
            log.gateway.error("[commands] audit.handle: failed", exc_info=exc)
            return f"✗ /audit: {exc}"

        # 4. EXIT
        log.gateway.debug(
            "[commands] audit.handle: exit",
            extra={"_fields": {"intact": intact}},
        )
        return result

    # ------------------------------------------------------------------
    # Export subcommand
    # ------------------------------------------------------------------

    async def _handle_export(self, args: str) -> str:
        """Execute /audit export [--output <path>]."""
        # 1. ENTRY
        log.gateway.debug(
            "[commands] audit._handle_export: entry",
            extra={"_fields": {"args_len": len(args)}},
        )

        # 2. DECISION — refuse empty signing key; a HMAC over b"" is worthless
        if not self._export_key:
            log.gateway.warning(
                "[commands] audit._handle_export: export_key is empty — refusing unsigned export"
            )
            return (
                "✗ /audit export: no signing key configured — "
                "refusing to write an unsigned 'signed' export. "
                "Set governance.audit_export_key in your settings."
            )

        try:
            # Parse optional --output path
            match = _OUTPUT_RE.search(args)
            if match:
                out_path = Path(match.group(1))
                log.gateway.debug(
                    "[commands] audit._handle_export: custom output path",
                    extra={"_fields": {"path": str(out_path)}},
                )
            else:
                from stackowl.paths import StackowlHome
                out_path = StackowlHome.knowledge_dir() / "audit-export.json"
                log.gateway.debug(
                    "[commands] audit._handle_export: default output path",
                    extra={"_fields": {"path": str(out_path)}},
                )

            sig_path = out_path.with_suffix(out_path.suffix + ".sig")

            # 3. STEP — fetch, serialize, write, sign
            rows = self._fetch_all_audit_rows()
            log.gateway.debug(
                "[commands] audit._handle_export: fetched rows",
                extra={"_fields": {"row_count": len(rows)}},
            )

            content = json.dumps(rows, default=str, ensure_ascii=False, indent=2).encode("utf-8")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(content)

            key_bytes = self._export_key.encode("utf-8")
            sig = hmac.new(key_bytes, content, hashlib.sha256).hexdigest()
            sig_path.write_text(sig, encoding="utf-8")

            result = (
                f"Audit export written to: {out_path}\n"
                f"HMAC-SHA256 signature:   {sig_path}\n"
                f"Rows exported: {len(rows)}"
            )
        except Exception as exc:
            log.gateway.error("[commands] audit._handle_export: failed", exc_info=exc)
            return f"✗ /audit export: {exc}"

        # 4. EXIT
        log.gateway.debug(
            "[commands] audit._handle_export: exit",
            extra={"_fields": {"out_path": str(out_path), "rows": len(rows)}},
        )
        return result

    def _fetch_all_audit_rows(self) -> list[dict[str, object]]:
        """Return all audit_log rows ordered by audit_id ASC."""
        assert self._logger is not None  # guarded by caller
        log.gateway.debug("[commands] audit._fetch_all_audit_rows: entry")
        db_path: Path = self._logger.db_path
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY audit_id ASC"
            ).fetchall()
            result = [dict(r) for r in rows]
        finally:
            conn.close()
        log.gateway.debug(
            "[commands] audit._fetch_all_audit_rows: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_table(self, rows: list[dict[str, object]]) -> list[str]:
        """Format rows as a simple text table."""
        header = f"{'id':<8} {'timestamp':<17} {'event_type':<22} {'actor':<18} {'target':<18} details"
        separator = "-" * len(header)
        lines: list[str] = [header, separator]
        for r in rows:
            ts_raw = r.get("timestamp", 0.0)
            ts_str = datetime.fromtimestamp(float(str(ts_raw)), tz=UTC).strftime("%Y-%m-%d %H:%M")
            details_raw = r.get("details", "{}")
            try:
                details_obj = json.loads(str(details_raw))
                details_summary = json.dumps(details_obj)[:80]
            except Exception as exc:
                log.gateway.debug(
                    "[commands] audit.format_table: details not valid JSON — "
                    "falling back to raw string",
                    exc_info=exc,
                    extra={"_fields": {"audit_id": r.get("audit_id")}},
                )
                details_summary = str(details_raw)[:80]
            line = (
                f"{str(r.get('audit_id', '')):<8} "
                f"{ts_str:<17} "
                f"{str(r.get('event_type', ''))[:22]:<22} "
                f"{str(r.get('actor', ''))[:18]:<18} "
                f"{str(r.get('target') or '')[:18]:<18} "
                f"{details_summary}"
            )
            lines.append(line)
        return lines
