"""ProcessMaintenanceMixin — the registry's sweep / reconcile / checkpoint half.

Split out of :mod:`stackowl.process.registry` purely for the B2 ≤300-lines rule:
the lifecycle half (start/poll/kill/list/write/close) stays in ``registry.py``;
this mixin holds the recurring/boot-time maintenance — MANDATORY-TTL auto-kill,
dead-handle prune, aggregate-buffer eviction, on-disk checkpoint save, boot
reconcile, and shutdown ``clear_all``. They share the same ``self`` state on the
:class:`ProcessRegistry` (the lock, the handle map, the clock, the checkpoint),
so this is one cohesive class assembled from two files — not a second registry.

Self-healing throughout (B5): every method here logs + heals; a sweep/reconcile/
checkpoint failure never crashes the scheduler loop, boot, or a spawn.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from stackowl.infra.clock import Clock
from stackowl.infra.observability import log
from stackowl.process.checkpoint import CheckpointEntry, ProcessCheckpoint
from stackowl.process.handle import ProcessHandle

if TYPE_CHECKING:  # pragma: no cover

    class _RegistryState:
        """The shared state + lifecycle methods this mixin relies on (typing only)."""

        _procs: dict[str, ProcessHandle]
        _clock: Clock
        _checkpoint: ProcessCheckpoint
        _max_lifetime: float
        _dead_prune: float
        _aggregate_cap: int
        _lock: Lock
        _terminal_at: dict[str, float]

        async def kill(self, process_id: str, session_id: str | None = None) -> bool: ...

    _Base = _RegistryState
else:
    _Base = object


class ProcessMaintenanceMixin(_Base):
    """Sweep / reconcile / checkpoint behaviour mixed into :class:`ProcessRegistry`."""


    # ----------------------------------------------------------------- sweep
    async def sweep(self, now: float | None = None) -> dict[str, int]:
        """TTL auto-kill + dead-handle prune + aggregate-buffer enforcement.

        Returns counts ``{auto_killed, pruned, evicted}``. ``now`` defaults to the
        injected clock (a test passes an advanced value to drive deadlines). Never
        raises into the scheduler loop (B5).
        """
        moment = self._clock.monotonic() if now is None else now
        log.tool.debug("process.registry.sweep: entry", extra={"_fields": {"now": moment}})
        auto_killed = pruned = evicted = 0
        # 1) MANDATORY-TTL auto-kill: any process still running past its deadline.
        with self._lock:
            overdue = [
                h for h in self._procs.values() if h.is_running and moment >= h.ttl_deadline
            ]
        for handle in overdue:
            log.tool.warning(
                "process.registry.sweep: process exceeded max lifetime — auto-killing",
                extra={"_fields": {"process_id": handle.process_id, "pid": handle.pid}},
            )
            if await self.kill(handle.process_id, handle.session_id):
                auto_killed += 1
        # 2) Dead-handle prune: drop terminal handles retained past the prune TTL.
        with self._lock:
            stale = [
                pid for pid, ts in self._terminal_at.items()
                if (moment - ts) >= self._dead_prune and pid in self._procs
            ]
            for pid in stale:
                self._procs.pop(pid, None)
                self._terminal_at.pop(pid, None)
                pruned += 1
        # 3) Aggregate-buffer ceiling: evict OLDEST processes' captures first.
        evicted = self._enforce_aggregate_ceiling()
        if pruned or auto_killed:
            self._save_checkpoint()
        log.tool.info(
            "process.registry.sweep: exit",
            extra={"_fields": {"auto_killed": auto_killed, "pruned": pruned, "evicted": evicted}},
        )
        return {"auto_killed": auto_killed, "pruned": pruned, "evicted": evicted}

    def _enforce_aggregate_ceiling(self) -> int:
        """Evict oldest processes' captured buffers until under the aggregate cap."""
        with self._lock:
            handles = sorted(self._procs.values(), key=lambda h: h.created_at)
        total = sum(h.live_capture_bytes() for h in handles)
        evicted = 0
        for handle in handles:
            if total <= self._aggregate_cap:
                break
            freed = handle.stdout_buffer.release() + handle.stderr_buffer.release()
            if freed:
                total -= freed
                evicted += 1
        return evicted

    # ----------------------------------------------------------- reconcile
    def reconcile(self) -> None:
        """Boot-time reconcile of the on-disk checkpoint against live pids.

        Re-adopts still-alive persisted processes as DETACHED running handles (no
        output pipe — the original transport is gone) and records dead ones as
        terminal. Never raises (B5 — a reconcile failure starts empty).
        """
        log.tool.debug("process.registry.reconcile: entry", extra={"_fields": {}})
        try:
            result = self._checkpoint.reconcile()
        except Exception as exc:  # B5 — never let reconcile crash boot
            log.tool.error("process.registry.reconcile: failed — starting empty", exc_info=exc)
            return
        now = self._clock.monotonic()
        with self._lock:
            for entry in result.adopted:
                handle = ProcessHandle(
                    command=entry.command,
                    session_id=entry.session_id,
                    transport=None,  # detached — survived restart, no live pipe
                    pid=entry.pid,
                    created_at=now,
                    ttl_deadline=now + self._max_lifetime,
                )
                handle.process_id = entry.process_id or handle.process_id
                self._procs[handle.process_id] = handle
        log.tool.info(
            "process.registry.reconcile: exit",
            extra={"_fields": {"adopted": len(result.adopted), "exited": len(result.exited)}},
        )
        self._save_checkpoint()

    # --------------------------------------------------------------- shutdown
    async def clear_all(self) -> int:
        """Terminate every live process and checkpoint. Returns the count cleared."""
        log.tool.debug(
            "process.registry.clear_all: entry", extra={"_fields": {"live": len(self._procs)}}
        )
        with self._lock:
            handles = list(self._procs.values())
        cleared = 0
        for handle in handles:
            if handle.is_running:
                try:
                    await self.kill(handle.process_id, handle.session_id)
                except Exception as exc:  # B5 — one bad kill must not stop shutdown
                    log.tool.error(
                        "process.registry.clear_all: kill error during shutdown — continuing",
                        exc_info=exc,
                        extra={"_fields": {"process_id": handle.process_id}},
                    )
                cleared += 1
            else:
                await handle.stop_readers()
        self._save_checkpoint()
        log.tool.info("process.registry.clear_all: exit", extra={"_fields": {"cleared": cleared}})
        return cleared

    # --------------------------------------------------------------- helpers
    def _save_checkpoint(self) -> None:
        """Persist running handles' metadata. Never raises (checkpoint is B5-safe)."""
        with self._lock:
            entries = [
                CheckpointEntry(
                    process_id=h.process_id,
                    pid=h.pid,
                    command=h.command,
                    session_id=h.session_id,
                    created_at=h.created_at,
                    status=h.status,
                )
                for h in self._procs.values()
                if h.is_running
            ]
        self._checkpoint.save(entries)
