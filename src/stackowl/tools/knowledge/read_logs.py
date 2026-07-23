"""read_logs — query StackOwl's own structured JSONL operational log.

Read-only, bounded query over the live ``~/.stackowl/logs/stackowl.jsonl``
file (``infra/observability.py``'s ``JsonlFormatter`` output). CLAUDE.md has
documented this capability with example queries ("errors in the last hour",
"what did the shell tool receive/return", "slowest tool calls") since before
this tool existed — a real implementation was missing entirely
(PATHFINDER-2026-07-22 Proposal 5, confirmed via a zero-hit
``grep -rn "read_logs" src/`` at the time).

Filters compose (AND): ``trace_id``, ``level``, ``tool`` (matches
``fields.tool``), ``since_minutes``. Returns the most recent matches within
the window, in chronological order. Only the LIVE file is scanned — rotated
backups (``stackowl-YYYY-MM-DD.jsonl``, see ``setup_logging``'s
``TimedRotatingFileHandler``) are NOT — this system is single-process/
single-tenant on one box, so the live file already covers "since midnight
UTC" at minimum, and scanning rotated history is out of scope for a
diagnostic tool (a caller wanting older history should ask for a narrower
recent window; there is no product need for unbounded historical log
mining here).

Severity ``read``; no owner-scoping — log records carry no ``owner_id``
(unlike ``task_outcomes``/etc, which use ``OwnedRepository``): this is a
single-tenant local file, not a per-user store.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500
_DEFAULT_SINCE_MINUTES = 60
# The live file itself rotates daily, so this rarely matters in practice —
# bounds a pathological caller-supplied value rather than expressing a real
# product ceiling.
_MAX_SINCE_MINUTES = 7 * 24 * 60
_MAX_OUTPUT_CHARS = 20_000
_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class ReadLogsTool(Tool):
    """Query the live stackowl.jsonl operational log."""

    @property
    def name(self) -> str:
        return "read_logs"

    @property
    def description(self) -> str:
        return (
            "Query StackOwl's own structured operational log (the live "
            "stackowl.jsonl file) for self-diagnosis: what errors happened "
            "recently, what a specific tool received/returned, which calls "
            "are slow, or every log line for one trace_id (a full request/"
            "turn). Filters compose: trace_id, level, tool, since_minutes. "
            "Returns the most recent matches within the window. "
            "LANE: inspecting THIS process's own recent behavior/errors. "
            "ANTI-LANE: past conversation content (use transcripts/"
            "session_search); durable facts (use memory)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "Return only log lines for this trace_id (one full request/turn).",
                },
                "level": {
                    "type": "string",
                    "enum": sorted(_VALID_LEVELS),
                    "description": "Return only log lines at this level.",
                },
                "tool": {
                    "type": "string",
                    "description": "Return only log lines whose fields.tool matches this tool name.",
                },
                "since_minutes": {
                    "type": "integer",
                    "default": _DEFAULT_SINCE_MINUTES,
                    "description": f"Only lines from the last N minutes (1-{_MAX_SINCE_MINUTES}).",
                },
                "limit": {
                    "type": "integer",
                    "default": _DEFAULT_LIMIT,
                    "description": f"Max matching lines to return (1-{_MAX_LIMIT}).",
                },
            },
            "required": [],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="knowledge",
        )

    # ------------------------------------------------------------------ execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        trace_id = self._str_or_none(kwargs.get("trace_id"))
        level = self._str_or_none(kwargs.get("level"))
        if level is not None:
            level = level.upper()
        tool_name = self._str_or_none(kwargs.get("tool"))
        since_minutes = self._coerce(kwargs.get("since_minutes"), _DEFAULT_SINCE_MINUTES, _MAX_SINCE_MINUTES)
        limit = self._coerce(kwargs.get("limit"), _DEFAULT_LIMIT, _MAX_LIMIT)
        log.tool.debug(
            "read_logs.execute: entry",
            extra={"_fields": {
                "trace_id": trace_id, "level": level, "tool": tool_name,
                "since_minutes": since_minutes, "limit": limit,
            }},
        )

        # 2. DECISION — no file yet (fresh install / logs not created) is a
        # valid, non-error empty result, not a failure.
        log_path = self._log_path()
        if not log_path.exists():
            duration_ms = (time.monotonic() - t0) * 1000
            return ToolResult(success=True, output="(no log file yet)", duration_ms=duration_ms)

        try:
            # 3. STEP — blocking file scan off the event loop.
            matches = await asyncio.to_thread(
                self._scan, log_path,
                trace_id=trace_id, level=level, tool_name=tool_name,
                since_minutes=since_minutes, limit=limit,
            )
        except OSError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error("read_logs.execute: file read failed", exc_info=exc)
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)

        output = self._render(matches)
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.tool.info(
            "read_logs.execute: exit",
            extra={"_fields": {"matches": len(matches), "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _log_path() -> Path:
        from stackowl.paths import StackowlHome
        return StackowlHome.logs_dir() / "stackowl.jsonl"

    @staticmethod
    def _scan(
        log_path: Path, *, trace_id: str | None, level: str | None,
        tool_name: str | None, since_minutes: int, limit: int,
    ) -> list[dict[str, object]]:
        """Blocking line-by-line scan (never loads the whole file into memory)
        — run via ``asyncio.to_thread``. Keeps only the most recent ``limit``
        matches via a bounded deque, in chronological order within that set.
        """
        cutoff = time.time() - since_minutes * 60
        matched: deque[dict[str, object]] = deque(maxlen=limit)
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if trace_id is not None and record.get("trace_id") != trace_id:
                    continue
                if level is not None and record.get("level") != level:
                    continue
                if tool_name is not None:
                    fields = record.get("fields") or {}
                    if fields.get("tool") != tool_name:
                        continue
                ts = record.get("ts")
                if isinstance(ts, str):
                    try:
                        record_epoch = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        record_epoch = None
                    if record_epoch is not None and record_epoch < cutoff:
                        continue
                matched.append(record)
        return list(matched)

    @staticmethod
    def _render(matches: list[dict[str, object]]) -> str:
        if not matches:
            return "(no matching log lines)"
        lines: list[str] = []
        for r in matches:
            parts = [f"{r.get('ts', '?')} {r.get('level', '?'):<7} {r.get('module', '?')}: {r.get('msg', '')}"]
            trace = r.get("trace_id")
            if trace:
                parts.append(f"trace={trace}")
            duration = r.get("duration_ms")
            if duration is not None:
                parts.append(f"duration_ms={duration}")
            fields = r.get("fields") or {}
            if fields:
                parts.append(f"fields={json.dumps(fields, default=str, ensure_ascii=False)}")
            lines.append(" | ".join(parts))
        output = "\n".join(lines)
        if len(output) > _MAX_OUTPUT_CHARS:
            output = (
                output[:_MAX_OUTPUT_CHARS]
                + f"\n... (truncated at {_MAX_OUTPUT_CHARS} chars — narrow trace_id/level/tool/since_minutes)"
            )
        return output

    @staticmethod
    def _str_or_none(raw: object) -> str | None:
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    @staticmethod
    def _coerce(raw: object, default: int, hi: int, *, lo: int = 1) -> int:
        val = default
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int):
            val = raw
        elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            val = int(raw.strip())
        return max(lo, min(val, hi))
