"""WaitTool — a thin, read-severity primitive for pausing a turn deliberately (E9-S2).

Two modes (ONE tool, mutually-exclusive validated args):

* **duration wait** — ``seconds`` → sleep that long (clamped to
  ``WAIT_MAX_TIMEOUT_SECONDS``) via ``asyncio.sleep``. A plain "pause N seconds".
* **process-exit predicate** — ``for_process`` (a ``process_id`` started by the
  ``process`` tool) + optional ``timeout`` → BLOCK until that process reaches a
  TERMINAL state (it exited / failed / was killed) OR the deadline passes. This
  is the CORRECT way to await a background process: the model calls ``wait`` ONCE
  instead of busy-polling ``process poll`` in a loop. The loop here sleeps
  ``WAIT_POLL_INTERVAL_SECONDS`` between registry polls (never a busy spin).

Severity ``read`` — ``wait`` never spends and is never consent-gated. It is a
THIN surface over the S0 substrate: it resolves ``get_services().process_registry``
at execute time (never building one) and reads the caller's ``session_id`` from
:class:`TraceContext` so a ``wait`` on another session's ``process_id`` sees
``None`` → a structured "no such process" (Fork E scoping).

The deadline uses an INJECTED :class:`Clock` (ARCH-99) — ``deadline =
clock.monotonic() + timeout``; the loop runs ``while clock.monotonic() < deadline``
— so the wait is deterministically testable with a fake clock (no wall-time
literal in the logic).

Self-healing (B5): a missing registry → structured "unavailable"; an unknown /
absent ``process_id`` → structured "no such process"; the tool NEVER raises.
CANCELLATION IS HONORED: a new user message cancels the turn → the in-flight wait
must propagate ``asyncio.CancelledError`` (never swallow it) so the turn unwinds
promptly. Every OTHER ``except`` logs (B5). ``toolset_group`` ``process``.
"""

from __future__ import annotations

import asyncio
import json
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.process.limits import WAIT_MAX_TIMEOUT_SECONDS, WAIT_POLL_INTERVAL_SECONDS
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.process.schema import sanitized_errors

_TOOLSET_GROUP = "process"

_WAIT_DESCRIPTION = (
    "Pause the current turn deliberately. Two modes (give EXACTLY one):\n"
    "  • duration — {\"seconds\": <float>}: sleep that long (clamped to "
    f"{int(WAIT_MAX_TIMEOUT_SECONDS)}s). Use to space out polite retries or let "
    "something settle.\n"
    "  • process-exit — {\"for_process\": <process_id>, \"timeout\": <float?>}: "
    "BLOCK until the background process started by the `process` tool finishes "
    "(exited/failed/killed) or the timeout elapses. This is the CORRECT way to "
    "await a process you started — call `wait for_process=<id>` ONCE; do NOT loop "
    "`process poll` yourself. Returns satisfied=true if it exited, satisfied=false "
    "if it timed out (still running). On timeout, you may `process poll`/`log` for "
    "progress, then `wait` again, or `process kill` it."
)

_WAIT_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "seconds": {
            "type": "number",
            "description": "Duration mode: seconds to sleep (clamped to the max).",
        },
        "for_process": {
            "type": "string",
            "description": "Process mode: the process_id to wait for to terminate.",
        },
        "timeout": {
            "type": "number",
            "description": "Process mode: max seconds to wait (clamped to the max).",
        },
    },
    "additionalProperties": False,
}


class WaitArgs(BaseModel):
    """Validated arguments for one ``wait`` invocation (one of two modes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seconds: float | None = None
    for_process: str | None = None
    timeout: float | None = None


def _clamp(value: float) -> float:
    """Clamp a requested span into ``[0, WAIT_MAX_TIMEOUT_SECONDS]`` (a wait can
    never wedge a turn indefinitely; a negative span degrades to zero)."""
    if value < 0:
        return 0.0
    return min(value, WAIT_MAX_TIMEOUT_SECONDS)


class WaitTool(Tool):
    """Pause a turn for a duration OR until a background process exits (read)."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        # Clock injected (ARCH-99) so the deadline is deterministically testable;
        # defaults to the production wall clock.
        self._clock: Clock = clock or WallClock()

    @property
    def name(self) -> str:
        return "wait"

    @property
    def description(self) -> str:
        return _WAIT_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return _WAIT_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the bounded arg shape only.
        t0 = time.monotonic()
        try:
            args = WaitArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "wait.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {sanitized_errors(exc)!r}", t0)

        has_duration = args.seconds is not None
        has_process = args.for_process is not None
        log.tool.info(
            "wait.execute: entry",
            extra={"_fields": {"mode": "process" if has_process else "duration"
                               if has_duration else "none"}},
        )

        # 2. DECISION — exactly one mode must be given (no ambiguous double-wait).
        if has_duration and has_process:
            return self._err(
                "give EXACTLY one of 'seconds' (duration) or 'for_process' (process-exit)", t0
            )
        if not has_duration and not has_process:
            return self._err(
                "wait requires either 'seconds' (duration) or 'for_process' (process-exit)", t0
            )

        try:
            if has_duration:
                return await self._wait_duration(float(args.seconds), t0)  # type: ignore[arg-type]
            return await self._wait_for_process(args, t0)
        except asyncio.CancelledError:
            # A new user message cancelled the turn — propagate, never swallow, so
            # the parked wait unwinds promptly (the turn must not eat the cancel).
            log.tool.info("wait.execute: cancelled — propagating")
            raise
        except Exception as exc:  # B5 — never raise out of a tool (besides cancel)
            log.tool.error("wait.execute: failed — degrading", exc_info=exc)
            return self._err(f"wait failed: {exc}", t0)

    # --------------------------------------------------------- duration mode
    async def _wait_duration(self, seconds: float, t0: float) -> ToolResult:
        waited = _clamp(seconds)
        log.tool.debug("wait.execute: sleeping", extra={"_fields": {"seconds": waited}})
        await asyncio.sleep(waited)
        return self._ok({"mode": "duration", "waited": waited, "satisfied": True}, t0)

    # ---------------------------------------------------------- process mode
    async def _wait_for_process(self, args: WaitArgs, t0: float) -> ToolResult:
        registry = get_services().process_registry
        if registry is None:
            log.tool.warning("wait.execute: no process_registry wired — unavailable")
            return self._err("process substrate unavailable (no process registry configured)", t0)

        pid = args.for_process or ""
        session_id = self._session_id()
        timeout = _clamp(args.timeout if args.timeout is not None else WAIT_MAX_TIMEOUT_SECONDS)

        # Deadline via the INJECTED clock (ARCH-99) — no wall-time literal here.
        start = self._clock.monotonic()
        deadline = start + timeout
        log.tool.debug(
            "wait.execute: polling for exit",
            extra={"_fields": {"process_id": pid, "timeout": timeout}},
        )

        while True:
            handle = await registry.poll(pid, session_id)
            if handle is None:
                # Unknown / absent / another session's id (Fork E) — structured, never raise.
                return self._err(f"no such process: {pid!r}", t0)
            if not handle.is_running:
                # TERMINAL — the predicate is satisfied (it exited/failed/was killed).
                waited = self._clock.monotonic() - start
                return self._ok(
                    {"mode": "process", "process_id": pid, "satisfied": True,
                     "status": handle.status, "exit_code": handle.exit_code,
                     "waited": waited}, t0
                )
            if self._clock.monotonic() >= deadline:
                # Timed out — still running. NOT an error: a structured not-satisfied.
                waited = self._clock.monotonic() - start
                log.tool.info(
                    "wait.execute: timed out — still running",
                    extra={"_fields": {"process_id": pid, "waited": waited}},
                )
                return self._ok(
                    {"mode": "process", "process_id": pid, "satisfied": False,
                     "status": handle.status, "exit_code": handle.exit_code,
                     "waited": waited}, t0
                )
            # Efficient wait — sleep between polls (NOT a busy spin). A cancel here
            # raises CancelledError straight out of execute (honored above).
            await asyncio.sleep(WAIT_POLL_INTERVAL_SECONDS)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _session_id() -> str:
        """The caller's session id from TraceContext (scopes the registry poll)."""
        sid = TraceContext.get().get("session_id")
        return str(sid) if sid else ""

    def _ok(self, payload: dict[str, object], t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "wait.execute: exit",
            extra={"_fields": {"success": True, "mode": payload.get("mode"),
                               "satisfied": payload.get("satisfied"),
                               "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=True, output=json.dumps(payload, ensure_ascii=False),
            error=None, duration_ms=duration_ms,
        )

    def _err(self, msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "wait.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
