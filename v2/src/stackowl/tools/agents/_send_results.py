"""Structured ToolResult builders for SessionsSendTool (E8-S4).

Split out of ``sessions_send.py`` to keep that module under the B2 line cap. Every
builder returns a ``success=True`` ToolResult carrying a structured JSON record so
the model always learns the real outcome — a refusal / error / timeout is REPORTED
(status), never masked as a fake reply (no-hidden-errors). Only the invalid-args
case is a hard ``success=False`` failure.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.owls.delegation_limits import GOVERNOR_ACQUIRE_TIMEOUT_SECONDS
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.base import ToolResult


async def run_under_governor(backend: AsyncioBackend, sub_state: PipelineState) -> PipelineState:
    """Run a continue-run under the shared budget (mirrors A2ADelegator).

    Acquires a bounded governor slot (released in ``finally`` via the slot context
    manager so a crash never leaks a permit); under saturation the run fails fast (a
    structured StackOwlError caught by the caller). No governor wired (early-stage
    tests) → run ungated + log a warning.
    """
    governor = get_services().delegation_governor
    if governor is None:
        log.tool.warning(
            "sessions_send.run_under_governor: no delegation_governor — running ungated",
            extra={"_fields": {"trace_id": sub_state.trace_id, "owl": sub_state.owl_name}},
        )
        return await backend.run(sub_state)
    async with governor.slot(timeout=GOVERNOR_ACQUIRE_TIMEOUT_SECONDS):
        return await backend.run(sub_state)


def ok(record: dict[str, object], t0: float, *, note: str) -> ToolResult:
    """Wrap a structured ``record`` into a success ToolResult (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "sessions_send.execute: exit",
        extra={"_fields": {"success": True, "status": record.get("status"), "duration_ms": duration_ms}},
    )
    payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
    return ToolResult(success=True, output=payload, duration_ms=duration_ms)


def refused(t0: float, reason: str, detail: str) -> ToolResult:
    """A structured (success=True) refusal — a safety rail, not a crash."""
    return ok({"status": "refused", "reason": reason, "detail": detail}, t0, note=detail)


def error(t0: float, label: str, owl: str, status: str, detail: str) -> ToolResult:
    """A structured failure record (success=True) — the model learns it failed.

    ``status`` is ``'error'`` or ``'timeout'``; the failure is REPORTED, never
    masked as a fake reply (no-hidden-errors).
    """
    return ok(
        {"status": status, "label": label, "owl": owl, "detail": detail},
        t0,
        note=f"sessions_send for {label!r} {status}",
    )


def failed(msg: str, t0: float) -> ToolResult:
    """A hard-failed ToolResult for invalid-argument cases (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "sessions_send.execute: exit",
        extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
    )
    return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
