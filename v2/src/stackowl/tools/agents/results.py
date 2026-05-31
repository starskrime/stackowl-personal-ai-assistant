"""Structured-result builders for ``delegate_task`` (B2 split from the tool).

Pure, side-effect-free shaping of the tool's JSON result envelope: success/refusal
records, the provenance footer, and the sub-task composition. Kept here so
``delegate_task.py`` stays under the B2 line cap and the result contract is
testable in isolation. Each builder returns a frozen
:class:`stackowl.tools.base.ToolResult`; none raise.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.tools.base import ToolResult


def compose_sub_task(goal: str, context: str | None) -> str:
    """Join the goal with optional caller-supplied context for the specialist."""
    if not context:
        return goal
    return f"{goal}\n\nContext:\n{context}"


def provenance_footer(target: str) -> str:
    """Short footer flagging the result as ``target``'s delegated sub-run."""
    return f"\n\n— delegated to '{target}' (sub-run); result above is {target}'s, not the caller's."


def ok_result(record: dict[str, object], t0: float, *, note: str) -> ToolResult:
    """Wrap a structured ``record`` into a success ToolResult (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "delegate_task.execute: exit",
        extra={"_fields": {"success": True, "status": record.get("status"), "duration_ms": duration_ms}},
    )
    payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
    return ToolResult(success=True, output=payload, duration_ms=duration_ms)


def refusal_result(t0: float, *, reason: str, detail: str) -> ToolResult:
    """A structured (success=True) refusal — a safety rail, not a crash."""
    return ok_result({"status": "refused", "reason": reason, "detail": detail}, t0, note=detail)


def error_result(msg: str, t0: float) -> ToolResult:
    """A failed ToolResult for invalid-argument / hard-error cases (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "delegate_task.execute: exit",
        extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
    )
    return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
