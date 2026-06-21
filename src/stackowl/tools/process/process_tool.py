"""ProcessTool — the agent-callable interface to the supervised ProcessRegistry.

ONE action-discriminated tool (start / poll / log / write / submit / kill / close
/ list) that lets an owl run and interact with a LONG-RUNNING background OS process
without wedging the turn. It is a THIN surface over the S0 substrate: it resolves
``get_services().process_registry`` at execute time (never building one, so the
concurrency-cap / mandatory-TTL / aggregate-buffer / checkpoint rails stay a single
source of truth). The real rails (the catastrophic-shape consent gate + the count
cap + the mandatory TTL) live INSIDE ``ProcessRegistry.start``; this tool merely
surfaces the registry's structured refusals as clean tool results.

``start`` takes an ARGV LIST (like ``shell``) so the registry's catastrophic check
runs on argv and the no-shell-injection property holds (``shell=False`` upstream).
Every process is session-scoped (Fork E): query actions default to the caller's
session; ``list all=True`` is the audited cross-session view (registry-audited).

Self-healing throughout (B5): a missing registry, an unknown ``process_id``, an
unknown ``action``, or any registry error degrades to a STRUCTURED result — the
tool NEVER raises. Severity ``write`` (ungated — the catastrophic gate is inside
``start``); ``toolset_group`` ``process``.
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.process.handle import ProcessHandle
from stackowl.process.registry import ProcessRegistry, ProcessRegistryError
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.process.schema import (
    PROCESS_ACTIONS,
    PROCESS_DESCRIPTION,
    PROCESS_PARAMETERS,
    sanitized_errors,
)

_TOOLSET_GROUP = "process"
_DEFAULT_LOG_TAIL = 64 * 1024  # cap a log read so a chatty process can't flood the model


class ProcessArgs(BaseModel):
    """Validated arguments for one ``process`` invocation (action-discriminated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    command: list[str] | None = Field(default=None, description="ARGV list (start).")
    cwd: str | None = None
    env: dict[str, str] | None = None
    process_id: str | None = None
    stream: str | None = None
    tail: int | None = None
    data: str | None = None
    line: str | None = None
    all: bool = False


class ProcessTool(Tool):
    """Run and interact with a supervised background OS process (start/poll/...)."""

    @property
    def name(self) -> str:
        return "process"

    @property
    def description(self) -> str:
        return PROCESS_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return PROCESS_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="unconfirmed",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        # 1. ENTRY — log the action + bounded arg shapes only (never stdin/env values).
        t0 = time.monotonic()
        try:
            args = ProcessArgs(**kwargs)  # type: ignore[arg-type]
        except ValidationError as exc:
            log.tool.warning(
                "process.execute: invalid args",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {sanitized_errors(exc)!r}", t0, committed=False)
        log.tool.info(
            "process.execute: entry",
            extra={"_fields": {"action": args.action, "argv_len": len(args.command or [])}},
        )
        if args.action not in PROCESS_ACTIONS:
            return self._err(
                f"unknown action {args.action!r} — must be one of {', '.join(PROCESS_ACTIONS)}",
                t0, committed=False,
            )

        # 2. DECISION — resolve the registry; self-heal to a structured result if absent.
        registry = get_services().process_registry
        if registry is None:
            log.tool.warning("process.execute: no process_registry wired — unavailable")
            return self._err(
                "process substrate unavailable (no process registry configured)", t0, committed=False,
            )
        session_id = self._session_id()

        # 3. STEP — dispatch; any registry error degrades to a structured result (B5).
        try:
            return await self._dispatch(args, registry, session_id, t0)
        except ProcessRegistryError as exc:
            # Structured refusal from start (too_many_processes / catastrophic_denied /
            # spawn_failed / empty_command) — surface cleanly, never a raise.
            log.tool.info(
                "process.execute: registry refusal — surfacing structured",
                extra={"_fields": {"action": args.action, "reason": exc.reason}},
            )
            return self._ok({"action": args.action, "refused": True,
                             "reason": exc.reason, "detail": exc.detail}, t0)
        except Exception as exc:  # B5 — never raise out of a tool
            log.tool.error(
                "process.execute: registry error — degrading",
                exc_info=exc,
                extra={"_fields": {"action": args.action}},
            )
            return self._err(f"process action failed ({args.action}): {exc}", t0)

    # --------------------------------------------------------------- dispatch
    async def _dispatch(
        self, args: ProcessArgs, registry: ProcessRegistry, session_id: str, t0: float
    ) -> ToolResult:
        if args.action == "start":
            return await self._start(args, registry, session_id, t0)
        if args.action == "list":
            return self._list(args, registry, session_id, t0)
        # All remaining actions are by-process_id.
        if not args.process_id:
            return self._err(f"{args.action} requires 'process_id'", t0, committed=False)
        pid = args.process_id
        if args.action == "poll":
            return await self._poll(registry, pid, session_id, t0)
        if args.action == "log":
            return self._log(args, registry, pid, session_id, t0)
        if args.action == "kill":
            return await self._kill(registry, pid, session_id, t0)
        if args.action == "close":
            return await self._close(registry, pid, session_id, t0)
        # write / submit
        return await self._write(args, registry, pid, session_id, t0)

    async def _start(
        self, args: ProcessArgs, registry: ProcessRegistry, session_id: str, t0: float
    ) -> ToolResult:
        if not args.command:
            return self._err("start requires a non-empty 'command' argv list", t0, committed=False)
        handle = await registry.start(
            args.command, session_id=session_id, cwd=args.cwd, env=args.env
        )
        log.tool.info(
            "process.start: launched",
            extra={"_fields": {"process_id": handle.process_id, "pid": handle.pid}},
        )
        return self._ok(
            {"action": "start", "process_id": handle.process_id,
             "pid": handle.pid, "status": handle.status}, t0
        )

    async def _poll(
        self, registry: ProcessRegistry, pid: str, session_id: str, t0: float
    ) -> ToolResult:
        handle = await registry.poll(pid, session_id)
        if handle is None:
            return self._err(f"no such process: {pid!r}", t0, committed=False)
        return self._ok(
            {"action": "poll", "process_id": pid, "status": handle.status,
             "running": handle.is_running, "exit_code": handle.exit_code,
             "stdout_truncated": handle.stdout_buffer.truncated,
             "stderr_truncated": handle.stderr_buffer.truncated,
             "dropped_bytes": handle.stdout_buffer.dropped_bytes
             + handle.stderr_buffer.dropped_bytes}, t0
        )

    def _log(
        self, args: ProcessArgs, registry: ProcessRegistry, pid: str, session_id: str, t0: float
    ) -> ToolResult:
        snapshot = registry.read_log(pid, session_id)
        if snapshot is None:
            return self._err(f"no such process: {pid!r}", t0, committed=False)
        stdout, stderr = snapshot
        stream = args.stream or "both"
        if stream not in ("stdout", "stderr", "both"):
            return self._err(f"invalid stream {stream!r} — use stdout|stderr|both", t0, committed=False)
        tail = args.tail if (args.tail is not None and args.tail > 0) else _DEFAULT_LOG_TAIL
        payload: dict[str, object] = {"action": "log", "process_id": pid, "stream": stream}
        if stream in ("stdout", "both"):
            payload["stdout"] = self._tail(stdout, tail)
        if stream in ("stderr", "both"):
            payload["stderr"] = self._tail(stderr, tail)
        return self._ok(payload, t0)

    async def _write(
        self, args: ProcessArgs, registry: ProcessRegistry, pid: str, session_id: str, t0: float
    ) -> ToolResult:
        # submit is a convenience over write: append a newline to feed a line to a REPL.
        if args.action == "submit":
            if args.line is None:
                return self._err("submit requires 'line'", t0, committed=False)
            data = args.line + "\n"
        else:  # write
            if args.data is None:
                return self._err("write requires 'data'", t0, committed=False)
            data = args.data
        ok = await registry.write_stdin(pid, data, session_id)
        if not ok:
            return self._err(
                f"could not write to {pid!r} (unknown, terminated, or stdin closed)",
                t0, committed=False,
            )
        return self._ok({"action": args.action, "process_id": pid, "written": len(data)}, t0)

    async def _kill(
        self, registry: ProcessRegistry, pid: str, session_id: str, t0: float
    ) -> ToolResult:
        existed = await registry.kill(pid, session_id)
        if not existed:
            return self._err(f"no such process: {pid!r}", t0, committed=False)
        return self._ok({"action": "kill", "process_id": pid, "killed": True}, t0)

    async def _close(
        self, registry: ProcessRegistry, pid: str, session_id: str, t0: float
    ) -> ToolResult:
        ok = await registry.close(pid, session_id)
        if not ok:
            return self._err(f"could not close stdin of {pid!r} (unknown or no stdin)", t0, committed=False)
        return self._ok({"action": "close", "process_id": pid, "closed": True}, t0)

    def _list(
        self, args: ProcessArgs, registry: ProcessRegistry, session_id: str, t0: float
    ) -> ToolResult:
        # all=True is the AUDITED cross-session view (the registry audit-logs it).
        handles = registry.list(session_id, all=args.all)
        rows = [self._row(h) for h in handles]
        return self._ok({"action": "list", "all": args.all, "count": len(rows), "processes": rows}, t0)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _row(handle: ProcessHandle) -> dict[str, object]:
        """One compact row for ``list`` — id/status/command-summary/session."""
        return {
            "process_id": handle.process_id,
            "status": handle.status,
            "command": handle.rendered_command,
            "session_id": handle.session_id,
            "pid": handle.pid,
        }

    @staticmethod
    def _tail(text: str, max_bytes: int) -> str:
        """Return at most ``max_bytes`` trailing bytes of ``text``; truncation made
        VISIBLE in the text itself (never silent) so the model knows the head dropped."""
        raw = text.encode("utf-8")
        if len(raw) <= max_bytes:
            return text
        clipped = raw[-max_bytes:].decode("utf-8", errors="replace")
        return f"...[{len(raw) - max_bytes} earlier bytes omitted]...\n{clipped}"

    @staticmethod
    def _session_id() -> str:
        """The caller's session id from TraceContext (scopes every registry call)."""
        sid = TraceContext.get().get("session_id")
        return str(sid) if sid else ""

    def _ok(self, payload: dict[str, object], t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "process.execute: exit",
            extra={"_fields": {"success": True, "action": payload.get("action"),
                               "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=True, output=json.dumps(payload, ensure_ascii=False),
            error=None, duration_ms=duration_ms,
        )

    def _err(self, msg: str, t0: float, *, committed: bool = True) -> ToolResult:
        """Structured failure. ``committed`` defaults True (conservative); callers
        pass False at a pre-execution / no-op refusal (invalid args, unknown action,
        registry unavailable, missing process_id, no-such-process, invalid stream,
        missing data, stdin write/close that did nothing) — none of which spawned or
        mutated a process. A post-dispatch exception (which may follow a partial
        ``start`` spawn) keeps the default True."""
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "process.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg,
            duration_ms=duration_ms, side_effect_committed=committed,
        )
