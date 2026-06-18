"""ProcessRegistry — the DI singleton owning supervised OS-process lifecycle (E9).

ONE narrow DI singleton (constructed in the startup gateway phase, injected onto
``StepServices.process_registry`` — never module-level, ARCH-88), Clock-injected
(ARCH-99) so every TTL/deadline is deterministically testable. It owns the
``process_id → ProcessHandle`` map plus the FULL lifecycle (Fork B): spawn,
eager-reap on poll, kill, dead-handle prune, MANDATORY-TTL auto-kill, aggregate
buffer enforcement, on-disk checkpoint + boot reconcile.

The REAL rails on an ungated ``process.start`` (it inherits the shell's
max-autonomy: any command runs, only the narrow catastrophic set needs consent —
reused via the shell seam, never reimplemented) are: a hard CONCURRENCY CAP
(``MAX_CONCURRENT_PROCESSES`` — start refuses past it, structured) and a MANDATORY
per-process TTL (``PROCESS_MAX_LIFETIME_SECONDS`` — the sweep auto-kills a runaway)
so an ungated spawn can neither fork-bomb nor leak forever. ``kill`` is always
allowed (de-escalation). Every handle carries ``session_id``; query methods default
to the caller's session, with an explicit ``all=True`` cross-session view (Fork E).

Self-healing throughout (B5): kill-of-a-dead-pid is a no-op success, eager-reap
closes the orphaned-reader window, a corrupt checkpoint reconciles to empty, and
every ``except`` logs + heals or surfaces — never a silent swallow.

Phase-2 backlog (Fork C — deferred, tracked): NO PTY today (pipe-only stdout/
stderr). An interactive PTY-backed variant (for REPLs / TUIs that need a tty) is
the documented extension seam — it would add a PTY transport + reader path on
:class:`ProcessHandle` and a ``use_pty`` flag on :meth:`start`, leaving these
rails untouched. Revisit after S1/S2 land the tools.
"""

from __future__ import annotations

import asyncio
from threading import Lock

from stackowl.exceptions import StackOwlError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.process.checkpoint import ProcessCheckpoint
from stackowl.process.handle import ProcessHandle
from stackowl.process.io_ops import ProcessIoMixin
from stackowl.process.kill_platform import terminate_tree
from stackowl.process.limits import (
    AGGREGATE_BUFFER_BYTES,
    DEAD_HANDLE_PRUNE_SECONDS,
    MAX_CONCURRENT_PROCESSES,
    PROCESS_MAX_LIFETIME_SECONDS,
)
from stackowl.process.maintenance import ProcessMaintenanceMixin
from stackowl.tools.system.shell import _gate_catastrophic, is_catastrophic


class ProcessRegistryError(StackOwlError):
    """A structured process-lifecycle refusal (cap reached / catastrophic deny).

    A :class:`StackOwlError` so the (S1) ``process`` tool's ``except`` degrades it
    to a structured refusal rather than crashing — never a fake-success.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"process {reason}: {detail}")


class ProcessRegistry(ProcessIoMixin, ProcessMaintenanceMixin):
    """Supervised OS processes; bounds count + lifetime; owns its checkpoint.

    Assembled from three files for the B2 split (one cohesive class, shared
    state): this file holds the per-process lifecycle (start/poll/kill/list);
    :class:`ProcessIoMixin` the stdin/stdout I/O (read_log/write_stdin/close);
    :class:`ProcessMaintenanceMixin` the sweep/reconcile/clear_all/checkpoint.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        checkpoint: ProcessCheckpoint | None = None,
        max_processes: int = MAX_CONCURRENT_PROCESSES,
        max_lifetime_seconds: float = PROCESS_MAX_LIFETIME_SECONDS,
        dead_prune_seconds: float = DEAD_HANDLE_PRUNE_SECONDS,
        aggregate_buffer_bytes: int = AGGREGATE_BUFFER_BYTES,
    ) -> None:
        self._procs: dict[str, ProcessHandle] = {}
        # F154 — in-flight reservations counted toward the concurrency cap under
        # ``_lock`` to close the start() check-then-act (TOCTOU) window. A slot is
        # reserved BEFORE the lock is released for the awaited spawn, and committed
        # (handed to a live handle) or rolled back on EVERY exit path. Transient —
        # never checkpointed (boot reconcile must not restore reservations).
        self._reserved: int = 0
        self._clock: Clock = clock or WallClock()
        self._checkpoint = checkpoint or ProcessCheckpoint()
        self._max = max_processes
        self._max_lifetime = max_lifetime_seconds
        self._dead_prune = dead_prune_seconds
        self._aggregate_cap = aggregate_buffer_bytes
        self._lock = Lock()
        self._terminal_at: dict[str, float] = {}  # process_id → monotonic terminal ts
        log.tool.debug(
            "process.registry.__init__: entry",
            extra={"_fields": {"max": max_processes, "max_lifetime_s": max_lifetime_seconds}},
        )

    # ----------------------------------------------------------------- start
    async def start(
        self,
        command: list[str],
        *,
        session_id: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessHandle:
        """Spawn a supervised background process. Enforces the count cap + TTL.

        Refuses (structured :class:`ProcessRegistryError`) past
        ``MAX_CONCURRENT_PROCESSES``. Reuses the SHELL's catastrophic check +
        fail-closed-off-TTY consent gate (never reimplemented). Spawns with
        ``start_new_session=True`` (own POSIX group → kill reaps the tree) and PIPE
        stdio. Sets the MANDATORY ``ttl_deadline``, starts readers, checkpoints.
        """
        # 1. ENTRY
        log.tool.debug(
            "process.registry.start: entry",
            extra={"_fields": {"session_id": session_id, "argv": command[:3],
                               "live": len(self._procs)}},
        )
        if not command:
            raise ProcessRegistryError("empty_command", "no command given to start")
        # 2. DECISION — cap is the hard concurrency rail on an ungated spawn. The
        # check-and-RESERVE is one atomic critical section under ``_lock`` so N
        # concurrent starts cannot all read ``live < max`` and all spawn (F154
        # TOCTOU). Live occupancy + in-flight reservations are both counted.
        with self._lock:
            live = sum(1 for h in self._procs.values() if h.is_running)
            if live + self._reserved >= self._max:
                log.tool.warning(
                    "process.registry.start: concurrency cap reached — refusing",
                    extra={"_fields": {"live": live, "reserved": self._reserved,
                                       "cap": self._max}},
                )
                raise ProcessRegistryError(
                    "too_many_processes",
                    f"too many live processes ({live + self._reserved} >= {self._max}); "
                    "kill one before starting another.",
                )
            self._reserved += 1
            log.tool.debug(
                "process.registry.start: slot reserved",
                extra={"_fields": {"live": live, "reserved": self._reserved,
                                   "cap": self._max}},
            )
        # The reservation MUST be released on EVERY exit path below (commit, deny,
        # spawn-fail, cancel) or the subsystem wedges into refuse-everything
        # (leak-DOWN is worse than leak-up). ``committed`` flips only once the slot
        # is owned by a live handle in ``_procs``; ``finally`` rolls back otherwise.
        committed = False
        try:
            # CATASTROPHIC gate — reuse the shell seam (fails closed off-TTY for the
            # narrow catastrophic set only); never reimplemented here.
            catastrophic, reason = is_catastrophic(command)
            if catastrophic:
                decision = await _gate_catastrophic(
                    tool_name="process", command=" ".join(command), reason=reason
                )
                if decision is not None:
                    raise ProcessRegistryError("catastrophic_denied", decision.error or reason)
            # 3. STEP — spawn in its own session/group so kill reaps the tree.
            try:
                transport = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=cwd or None,
                    env=env,
                    start_new_session=True,
                )
            except OSError as exc:  # B5 — surface a structured failure, never crash
                log.tool.error(
                    "process.registry.start: spawn failed",
                    exc_info=exc,
                    extra={"_fields": {"argv": command[:3]}},
                )
                raise ProcessRegistryError("spawn_failed", str(exc)) from exc
            now = self._clock.monotonic()
            handle = ProcessHandle(
                command=command,
                session_id=session_id,
                transport=transport,
                pid=transport.pid,
                created_at=now,
                ttl_deadline=now + self._max_lifetime,
                cwd=cwd,
            )
            handle.start_readers()
            # COMMIT — insert and drop the reservation in ONE critical section so
            # occupancy is never under-counted between the two.
            with self._lock:
                self._procs[handle.process_id] = handle
                self._reserved -= 1
                committed = True
            self._save_checkpoint()
            # 4. EXIT
            log.tool.info(
                "process.registry.start: exit",
                extra={"_fields": {"process_id": handle.process_id, "pid": handle.pid,
                                   "session_id": session_id}},
            )
            return handle
        finally:
            if not committed:
                # ROLLBACK on deny / spawn-fail / CancelledError — release the slot.
                with self._lock:
                    self._reserved -= 1
                    rolled = self._reserved
                log.tool.debug(
                    "process.registry.start: reservation rolled back",
                    extra={"_fields": {"reserved": rolled}},
                )

    # ------------------------------------------------------------------ poll
    async def poll(self, process_id: str, session_id: str | None = None) -> ProcessHandle | None:
        """Return the handle, eager-reaping it if its transport has exited.

        Eager-reap (self-healing): if the transport reports a return code, await it
        (reap the zombie) and record the terminal status+exit_code. ``session_id``
        scopes the lookup (Fork E): a mismatch returns ``None``.
        """
        handle = self._get_scoped(process_id, session_id)
        if handle is None:
            return None
        await self._reap_if_exited(handle)
        return handle

    async def _reap_if_exited(self, handle: ProcessHandle) -> None:
        """If the transport has exited, await it and record terminal state. B5-safe."""
        if not handle.is_running or handle.transport is None:
            return
        rc = handle.transport.returncode
        if rc is None:
            return  # still running — legitimate
        try:
            await handle.transport.wait()  # reap the zombie
        except Exception as exc:  # B5
            log.tool.debug(
                "process.registry._reap_if_exited: wait error",
                extra={"_fields": {"process_id": handle.process_id, "error": str(exc)}},
            )
        await handle.stop_readers()
        handle.exit_code = rc
        handle.status = "exited" if rc == 0 else "failed"
        handle.last_active = self._clock.monotonic()
        self._mark_terminal(handle)
        log.tool.debug(
            "process.registry._reap_if_exited: reaped",
            extra={"_fields": {"process_id": handle.process_id, "exit_code": rc,
                               "status": handle.status}},
        )

    # ------------------------------------------------------------------ kill
    async def kill(self, process_id: str, session_id: str | None = None) -> bool:
        """Terminate a scoped process (kill is ALWAYS allowed — de-escalation).

        Kill-of-an-already-dead process is a no-op SUCCESS (self-healing). Returns
        True if the process existed (whether it was live or already terminal).
        """
        # 1. ENTRY
        log.tool.debug("process.registry.kill: entry", extra={"_fields": {"process_id": process_id}})
        handle = self._get_scoped(process_id, session_id)
        if handle is None:
            return False
        if not handle.is_running:
            log.tool.debug(
                "process.registry.kill: already terminal — no-op success",
                extra={"_fields": {"process_id": process_id, "status": handle.status}},
            )
            return True
        await terminate_tree(handle.pid)
        try:
            if handle.transport is not None:
                await asyncio.wait_for(handle.transport.wait(), timeout=5)
                handle.exit_code = handle.transport.returncode
        except Exception as exc:  # B5 — incl. TimeoutError from wait_for
            log.tool.debug(
                "process.registry.kill: post-kill wait error",
                extra={"_fields": {"process_id": process_id, "error": str(exc)}},
            )
        await handle.stop_readers()
        handle.status = "killed"
        handle.last_active = self._clock.monotonic()
        self._mark_terminal(handle)
        self._save_checkpoint()
        # 4. EXIT
        log.tool.info("process.registry.kill: exit", extra={"_fields": {"process_id": process_id}})
        return True

    # ------------------------------------------------------------------ list
    def list(self, session_id: str | None = None, *, all: bool = False) -> list[ProcessHandle]:
        """List handles scoped to ``session_id`` (Fork E); ``all=True`` is cross-session.

        ``all=True`` is the audited cross-session view (the S1 tool logs it); the
        default returns only the caller's own processes.
        """
        with self._lock:
            handles = list(self._procs.values())
        if all:
            log.tool.info(
                "process.registry.list: cross-session listing (all=True)",
                extra={"_fields": {"count": len(handles), "caller_session": session_id}},
            )
            return handles
        return [h for h in handles if session_id is None or h.session_id == session_id]

    # --------------------------------------------------------------- helpers
    def _get_scoped(self, process_id: str, session_id: str | None) -> ProcessHandle | None:
        """Fetch a handle, enforcing session scoping (Fork E). None on mismatch."""
        with self._lock:
            handle = self._procs.get(process_id)
        if handle is None:
            return None
        if session_id is not None and handle.session_id != session_id:
            log.tool.debug(
                "process.registry._get_scoped: session mismatch — hidden",
                extra={"_fields": {"process_id": process_id, "caller": session_id,
                                   "owner": handle.session_id}},
            )
            return None
        return handle

    def _mark_terminal(self, handle: ProcessHandle) -> None:
        """Record the monotonic instant a handle reached a terminal state."""
        with self._lock:
            self._terminal_at[handle.process_id] = self._clock.monotonic()
