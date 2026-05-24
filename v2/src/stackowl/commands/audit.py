"""AuditCommand — ``/audit`` slash command for viewing the audit log."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.audit.logger import AuditLogger
    from stackowl.pipeline.state import PipelineState

log = logging.getLogger("stackowl.audit")


class AuditCommand(SlashCommand):
    """``/audit`` slash command — show last 50 audit log entries with integrity status."""

    def __init__(self, audit_logger: AuditLogger) -> None:
        # 1. ENTRY
        log.debug("[commands] audit.init: entry")
        self._logger = audit_logger
        # 4. EXIT
        log.debug("[commands] audit.init: exit")

    @property
    def command(self) -> str:
        return "audit"

    @property
    def description(self) -> str:
        return "Show the last 50 audit log entries with integrity status"

    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute /audit — fetch tail and render as a text table."""
        # 1. ENTRY
        log.debug(
            "[commands] audit.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        try:
            # 2. DECISION
            log.debug("[commands] audit.handle: decision — fetching tail(50) and verifying chain")
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
            log.error("[commands] audit.handle: failed", exc_info=exc)
            return f"✗ /audit: {exc}"

        # 4. EXIT
        log.debug(
            "[commands] audit.handle: exit",
            extra={"_fields": {"row_count": len(rows), "intact": intact}},
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
            ts_str = datetime.fromtimestamp(float(ts_raw), tz=UTC).strftime("%Y-%m-%d %H:%M")
            details_raw = r.get("details", "{}")
            try:
                details_obj = json.loads(str(details_raw))
                details_summary = json.dumps(details_obj)[:80]
            except Exception:
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
