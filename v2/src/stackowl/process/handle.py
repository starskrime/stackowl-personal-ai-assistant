"""ProcessHandle — one supervised OS process, mutable, registry-owned.

A handle bundles the asyncio subprocess transport with everything the registry
and the (S1) tools need: a stable opaque ``process_id``, the host ``pid``, the
originating ``session_id`` (Fork E scoping), lifecycle ``status`` + ``exit_code``,
activity timestamps, the MANDATORY ``ttl_deadline`` (Fork D), and the two
:class:`RollingStreamBuffer` captures. Background reader tasks drain stdout/stderr
into those buffers so a poller always sees the latest tail.

This is a deliberately plain mutable class (not a frozen value): a process is a
living thing whose status and captured output change in place over its lifetime,
unlike the identity-only :class:`SessionHandle`.

NO PTY (Fork C, pipe-only). A future PTY-backed variant would subclass/extend the
reader wiring here — see the Phase-2 backlog note in the registry module docstring.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Literal

from stackowl.infra.observability import log
from stackowl.process.buffers import RollingStreamBuffer

# Lifecycle states. ``running`` is the only non-terminal one. A process EXITING
# is the NORMAL terminal state (``exited``) — never a fault to retry (Fork B).
ProcessStatus = Literal["running", "exited", "killed", "failed"]


class ProcessHandle:
    """A single tracked OS process plus its captured output and lifecycle."""

    def __init__(
        self,
        *,
        command: list[str],
        session_id: str,
        transport: asyncio.subprocess.Process | None,
        pid: int | None,
        created_at: float,
        ttl_deadline: float,
        cwd: str | None = None,
    ) -> None:
        # 1. ENTRY
        log.tool.debug(
            "process.handle.__init__: entry",
            extra={"_fields": {"pid": pid, "session_id": session_id, "argv": command[:3]}},
        )
        self.process_id: str = secrets.token_urlsafe(9)
        self.command: list[str] = command
        self.session_id: str = session_id
        self.transport: asyncio.subprocess.Process | None = transport
        self.pid: int | None = pid
        self.cwd: str | None = cwd
        self.status: ProcessStatus = "running"
        self.exit_code: int | None = None
        self.created_at: float = created_at
        self.last_active: float = created_at
        # MANDATORY TTL (Fork D): the sweep auto-kills a process still running
        # past this monotonic deadline so an ungated spawn can never leak forever.
        self.ttl_deadline: float = ttl_deadline
        self.stdout_buffer = RollingStreamBuffer(name="stdout")
        self.stderr_buffer = RollingStreamBuffer(name="stderr")
        self._reader_tasks: list[asyncio.Task[None]] = []

    # ----------------------------------------------------------- properties
    @property
    def is_running(self) -> bool:
        """True only while the process has not reached a terminal state."""
        return self.status == "running"

    @property
    def rendered_command(self) -> str:
        """The argv joined for human-readable logging/display (truncated)."""
        return " ".join(self.command)[:200]

    def live_capture_bytes(self) -> int:
        """Total bytes currently retained across both stream buffers."""
        return self.stdout_buffer.live_bytes() + self.stderr_buffer.live_bytes()

    # --------------------------------------------------------------- readers
    def start_readers(self) -> None:
        """Spawn the two background tasks draining stdout/stderr into buffers.

        Idempotent-ish: only starts readers for streams the transport actually
        exposes. Each task is named for debuggability and self-reaps on EOF.
        """
        if self.transport is None:
            log.tool.debug(
                "process.handle.start_readers: no transport — skipping readers",
                extra={"_fields": {"process_id": self.process_id}},
            )
            return
        if self.transport.stdout is not None:
            self._reader_tasks.append(
                asyncio.create_task(
                    self._drain(self.transport.stdout, self.stdout_buffer),
                    name=f"process-stdout-{self.process_id}",
                )
            )
        if self.transport.stderr is not None:
            self._reader_tasks.append(
                asyncio.create_task(
                    self._drain(self.transport.stderr, self.stderr_buffer),
                    name=f"process-stderr-{self.process_id}",
                )
            )

    async def _drain(self, stream: asyncio.StreamReader, buffer: RollingStreamBuffer) -> None:
        """Read ``stream`` to EOF, appending each chunk to ``buffer``. Never raises."""
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buffer.append(chunk)
        except asyncio.CancelledError:
            raise  # cooperative cancellation (stop_readers) — never swallow
        except Exception as exc:  # B5 — a reader failure must not crash the loop
            log.tool.debug(
                "process.handle._drain: reader ended",
                extra={"_fields": {"process_id": self.process_id, "stream": buffer.name,
                                   "error": str(exc)}},
            )

    async def stop_readers(self) -> None:
        """Cancel and await the reader tasks (on terminal state / shutdown)."""
        for task in self._reader_tasks:
            task.cancel()
        for task in self._reader_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # B5 — a reader teardown must not throw
                log.tool.debug(
                    "process.handle.stop_readers: reader teardown error",
                    extra={"_fields": {"process_id": self.process_id, "error": str(exc)}},
                )
        self._reader_tasks.clear()

    # ------------------------------------------------------------- snapshot
    def status_dict(self) -> dict[str, object]:
        """A JSON-friendly status snapshot (used by poll/list in S1)."""
        return {
            "process_id": self.process_id,
            "session_id": self.session_id,
            "command": self.rendered_command,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "stdout_truncated": self.stdout_buffer.truncated,
            "stderr_truncated": self.stderr_buffer.truncated,
        }
