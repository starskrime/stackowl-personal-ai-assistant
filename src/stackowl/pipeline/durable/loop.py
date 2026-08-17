"""TaskLoop — the ONE loop. Bakir's architecture, 2026-08-17.

*"Loop fires every five seconds internally. Checks, process, continues. Checks,
process, continues. Till it achieves the goal."*

WHAT THIS OWNS, AND WHAT IT DOES NOT. The store owns how a task MOVES between
states — claim, requeue-with-what-failed, deliver, dead-letter. This owns only
PACING and CONCURRENCY: which rows to offer this tick, how many to run at once, and
making sure one task's crash cannot take the loop down with it. That split is why
the claim semantics could be proven without a running event loop.

IT COPIES A SHAPE THAT ALREADY WORKS. ``scheduler.py`` dispatches due jobs with
``asyncio.gather`` behind a CAS claim, and the comment there records why: a
sequential ``for row: await run(row)`` let one slow handler block every other job's
on-time firing, and ``telegram_canary``'s delivery log showed a 20m/20m/50m rhythm
locked to ``dream_worker``'s cadence rather than a real send failure. The same
mistake would be worse here, where a single slow task would stall every other one.
CLAUDE.md's rule is to copy that shape rather than invent a second — so this does.

THE PROPERTY EVERYTHING ELSE RESTS ON is that the loop never dies. A loop that
stops on an unhandled exception is worse than no loop at all: work accumulates in a
table nobody is draining, and nothing reports it. Every guard below exists for that
one reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.pipeline.durable.task import DurableTask

#: What the loop hands a worker, and what it expects back: the delivered result.
#: An empty return is NOT success — see ``_dispatch``.
TaskRunner = Callable[["DurableTask"], Awaitable[str]]


class _Store(Protocol):
    """The store surface this loop needs. Stated narrowly so the loop can be driven
    by a double in tests without importing the database."""

    async def claimable(self, *, limit: int = 10, now: Any = None) -> list[Any]: ...
    async def claim(self, task_id: str, *, worker: str, lease_seconds: int = 900) -> bool: ...
    async def mark_delivered(self, task_id: str, *, result: str) -> None: ...
    async def fail_and_requeue(
        self, task_id: str, *, error: str, failure_class: str = "",
        banned: tuple[str, ...] = (),
    ) -> str: ...
    async def reclaim_expired(self, *, now: Any = None) -> int: ...
    async def prune_completed(self, *, older_than_days: int = 1) -> int: ...


def classify_failure(exc: BaseException) -> str:
    """Name the failure so the ceiling can be spent intelligently.

    A network blip and a missing credential must not cost the same thirty
    attempts: the first is worth retrying, the second fails identically every time
    and would burn thirty guaranteed-wasted model calls — seconds of latency each
    on this hardware.

    Reuses the project's single transient oracle rather than adding a second
    opinion about what "transient" means.
    """
    try:
        from stackowl.infra.resilience import looks_like_dead_handle

        if looks_like_dead_handle(exc):
            return "transient"
    except Exception:  # pragma: no cover — the oracle must never decide the turn
        log.tasks.warning("[loop] transient oracle unavailable", exc_info=True)
    if isinstance(exc, TimeoutError | ConnectionError):
        return "transient"
    if isinstance(exc, PermissionError):
        return "auth"
    if isinstance(exc, FileNotFoundError | LookupError):
        return "not_found"
    return "error"


class TaskLoop:
    """Claims pending tasks and runs them in parallel until they land."""

    def __init__(
        self,
        *,
        store: _Store,
        runner: TaskRunner,
        max_parallel: int = 5,
        tick_seconds: float = 5.0,
        lease_seconds: int = 900,
        prune_after_days: int = 1,
        worker_prefix: str = "loop",
    ) -> None:
        self._store = store
        self._runner = runner
        self._max_parallel = max(1, int(max_parallel))
        self._tick_seconds = float(tick_seconds)
        self._lease_seconds = int(lease_seconds)
        self._prune_after_days = int(prune_after_days)
        self._worker = f"{worker_prefix}-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        #: Set by enqueue-side code to wake the loop NOW rather than wait out the
        #: tick. The tick is the safety net; this is the fast path.
        self._wake = asyncio.Event()

    @property
    def worker_id(self) -> str:
        return self._worker

    def wake(self) -> None:
        """Ask for a tick immediately. Cheap and idempotent — a burst of enqueues
        coalesces into one extra pass rather than one pass each."""
        self._wake.set()

    async def start(self) -> None:
        """Begin ticking. Idempotent: a second call is a no-op, so a double-wire at
        assembly cannot silently produce two loops racing the same table."""
        if self._task is not None and not self._task.done():
            log.tasks.info("[loop] start: already running — noop",
                           extra={"_fields": {"worker": self._worker}})
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_forever())
        log.tasks.info(
            "[loop] started",
            extra={"_fields": {"worker": self._worker, "tick_s": self._tick_seconds,
                               "max_parallel": self._max_parallel}},
        )

    async def stop(self) -> None:
        """Stop ticking and wait for the current pass. "The loop never ends" is the
        design; stopping on command is what keeps a restart from hanging."""
        self._stopping.set()
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        log.tasks.info("[loop] stopped", extra={"_fields": {"worker": self._worker}})

    async def _run_forever(self) -> None:
        while not self._stopping.is_set():
            await self.tick()
            self._wake.clear()
            # Wait for the tick OR an enqueue, whichever comes first. A plain
            # sleep would make every new task wait out the full interval, which is
            # the difference between a chat reply feeling instant and feeling
            # broken.
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick_seconds)

    async def tick(self) -> None:
        """One pass: recover, claim, run in parallel, tidy. NEVER raises.

        Housekeeping runs even on an idle tick — reclaiming a crashed worker's row
        and pruning delivered ones is exactly what the pass is FOR when the queue is
        empty. Doing it only when there is work would mean a crashed lease is
        recovered only once new work happens to arrive.
        """
        t0 = time.monotonic()
        claimed: list[Any] = []
        try:
            await self._store.reclaim_expired()
            rows = await self._store.claimable(limit=self._max_parallel)
            for row in rows:
                if await self._store.claim(
                    row.task_id, worker=self._worker,
                    lease_seconds=self._lease_seconds,
                ):
                    claimed.append(row)
            if claimed:
                # return_exceptions=True is load-bearing: without it the first
                # raise cancels its siblings, so one bad row silently drops every
                # other task claimed this pass.
                await asyncio.gather(
                    *(self._dispatch(r) for r in claimed), return_exceptions=True,
                )
            await self._store.prune_completed(older_than_days=self._prune_after_days)
        except Exception as exc:
            # The loop must outlive anything inside it. A tick that dies means work
            # piles up in a table nobody drains, with nothing reporting it.
            log.tasks.error(
                "[loop] tick failed — the loop continues",
                exc_info=exc, extra={"_fields": {"worker": self._worker}},
            )
        if claimed:
            log.tasks.info(
                "[loop] tick: exit",
                extra={"_fields": {"worker": self._worker, "ran": len(claimed),
                                   "duration_ms": (time.monotonic() - t0) * 1000}},
            )

    async def _dispatch(self, task: Any) -> None:
        """Run ONE task and record what happened. Never raises into the gather.

        A task is complete only when the runner returns something that actually
        landed. An empty result is treated as a failure, not a success: marking it
        delivered would be the overclaim shape this platform keeps finding —
        success asserted rather than observed.
        """
        try:
            result = await self._runner(task)
        except Exception as exc:
            failure_class = classify_failure(exc)
            log.tasks.warning(
                "[loop] task attempt failed",
                exc_info=exc,
                extra={"_fields": {"task_id": task.task_id,
                                   "failure_class": failure_class}},
            )
            await self._safe_fail(task, error=str(exc), failure_class=failure_class)
            return
        if not (result or "").strip():
            await self._safe_fail(
                task,
                error="the attempt produced no deliverable result",
                failure_class="empty",
            )
            return
        try:
            await self._store.mark_delivered(task.task_id, result=result)
        except Exception as exc:
            # The work may well have happened; we simply could not record it. Say
            # so loudly — the lease will expire and the row will be retried, which
            # is why an idempotency key matters for effectful tasks.
            log.tasks.error(
                "[loop] could not mark a task delivered — it will be retried when "
                "its lease expires",
                exc_info=exc, extra={"_fields": {"task_id": task.task_id}},
            )

    async def _safe_fail(
        self, task: Any, *, error: str, failure_class: str
    ) -> None:
        """Requeue, and never let the requeue itself become the unhandled error."""
        try:
            await self._store.fail_and_requeue(
                task.task_id, error=error, failure_class=failure_class,
            )
        except Exception as exc:
            log.tasks.error(
                "[loop] could not requeue a failed task — its lease will expire and "
                "the sweep will recover it",
                exc_info=exc, extra={"_fields": {"task_id": task.task_id}},
            )
