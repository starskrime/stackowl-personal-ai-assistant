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
from stackowl.pipeline.durable.failure_class import (
    classify_failure,
    wants_reshaping,
)

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
    async def enqueue(self, task: Any) -> None: ...
    async def set_dependencies(
        self, task_id: str, depends_on: tuple[str, ...],
    ) -> None: ...
    async def revive_undelivered_failures(self, *, limit: int = 50) -> int: ...
    async def count_pending_for_other_owners(self) -> int: ...
    async def heal_unreachable_owners(self, *, limit: int = 500) -> int: ...
    async def reclaim_expired(self, *, now: Any = None) -> int: ...
    async def prune_completed(self, *, older_than_days: int = 1) -> int: ...


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
        #: Consecutive failed ticks. Reset by the first healthy one.
        self._consecutive_tick_failures: int = 0
        #: True once this streak has been escalated, so a repeating failure alerts
        #: ONCE rather than flooding the channel it is alerting through.
        self._tick_escalated: bool = False

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
        # ONE-TIME RESCUE, at the only moment it is guaranteed to run before any
        # claiming happens. Rows that died terminally BEFORE the failure chokepoint
        # existed cannot be reached by the loop — it claims only 'pending'. Any of
        # them that still owes a person an answer is returned to the queue here.
        # The predicate (has a destination, never delivered) is what keeps this
        # from becoming a stampede: on the day it was written it matched one row.
        try:
            revived = await self._store.revive_undelivered_failures()
            if revived:
                log.tasks.warning(
                    "[loop] start: revived stranded tasks that owed an answer",
                    extra={"_fields": {"revived": revived, "worker": self._worker}},
                )
        except Exception as exc:
            # A rescue that can stop the loop starting is worse than the strandings.
            log.tasks.error(
                "[loop] start: the undelivered-failure sweep failed — starting anyway",
                exc_info=exc, extra={"_fields": {"worker": self._worker}},
            )
        # WHAT NOTICES WHEN THE QUEUE GROWS SOMEWHERE THIS LOOP CANNOT SEE. The
        # claim is owner-scoped and this loop is bound to ONE owner, so a row filed
        # under anything else is invisible to it forever. That happened: 387 rows
        # written under a knowledge scope instead of a principal, 72 of them still
        # pending, and nothing said a word for a day and a half. The writer is
        # fixed; this is so the CLASS of mistake cannot be silent again.
        #
        # A count and a warning, never a claim — driving another owner's work is a
        # decision (ESC-17), not a repair. Same fail-open contract as the sweep
        # above: bookkeeping must never stop the loop from starting.
        try:
            # HEAL, DO NOT MERELY COUNT. This block used to only warn, and Bakir's rule
            # of 2026-08-21 is what condemns that: "if you fix core issue platform
            # should heal himself. If it does not, then platform has issue with self
            # healing OR core issue not resolved." Both were true — the writer was
            # fixed and 387 rows stayed exactly where they were, because detection is
            # not healing and the standing rule is to build the actuator rather than
            # file the debt.
            healed = await self._store.heal_unreachable_owners()
            unreachable = await self._store.count_pending_for_other_owners()
            if healed or unreachable:
                log.tasks.warning(
                    "[loop] start: repaired work no loop could claim",
                    extra={"_fields": {"healed": healed,
                                       "still_unreachable": unreachable,
                                       "worker": self._worker}},
                )
        except Exception as exc:
            log.tasks.error(
                "[loop] start: could not count unreachable pending tasks — "
                "starting anyway",
                exc_info=exc, extra={"_fields": {"worker": self._worker}},
            )
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

    #: Consecutive failed ticks before the loop stops merely logging and ESCALATES.
    #: Small, because the failure this exists for repeats every few seconds: a
    #: corrupt DB handle produced 10,238 identical failures in one outage.
    _TICK_FAILURES_BEFORE_ESCALATION = 20

    def _note_tick_ok(self) -> None:
        """A healthy tick clears the streak and re-arms the alarm."""
        if self._consecutive_tick_failures:
            if self._tick_escalated:
                log.tasks.info(
                    "[loop] RECOVERED — ticks are succeeding again",
                    extra={"_fields": {
                        "worker": self._worker,
                        "failures_before_recovery": self._consecutive_tick_failures,
                    }},
                )
            self._consecutive_tick_failures = 0
            self._tick_escalated = False

    def _note_tick_failed(self, exc: BaseException) -> None:
        """Turn a repeating failure into ONE loud, actionable signal.

        THE GAP THIS CLOSES was already named three lines above, in the comment on
        the handler itself: "a tick that dies means work piles up in a table nobody
        drains, WITH NOTHING REPORTING IT." It then logged at ERROR and continued.

        MEASURED 2026-08-26: a corrupt WAL handle made every tick fail with
        "DatabaseError: file is not a database". The loop faithfully logged
        "the loop continues" TEN THOUSAND TWO HUNDRED AND THIRTY-EIGHT times over
        roughly five and a half hours, during which nothing durable was written and
        nobody was told. It was found by a human looking at something else.

        Repetition is not reporting. An identical line ten thousand times is
        indistinguishable from noise, and the reader who most needs it is the one
        who is not watching. So the streak escalates ONCE, at CRITICAL — the level
        an operator alerts on — and re-arms only after a healthy tick, so a flapping
        subsystem cannot re-flood the channel it just alerted through.
        """
        self._consecutive_tick_failures += 1
        if self._tick_escalated:
            return
        if self._consecutive_tick_failures < self._TICK_FAILURES_BEFORE_ESCALATION:
            return
        self._tick_escalated = True
        log.tasks.critical(
            "[loop] CRITICAL — the durable task loop has failed every tick and is "
            "not draining work; durable tasks are NOT running",
            exc_info=exc,
            extra={"_fields": {
                "worker": self._worker,
                "consecutive_failures": self._consecutive_tick_failures,
                "last_error": f"{type(exc).__name__}: {exc}"[:300],
                "impact": "queued durable tasks are not being executed",
            }},
        )

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
            self._note_tick_ok()
        except Exception as exc:
            # The loop must outlive anything inside it. A tick that dies means work
            # piles up in a table nobody drains, with nothing reporting it.
            log.tasks.error(
                "[loop] tick failed — the loop continues",
                exc_info=exc, extra={"_fields": {"worker": self._worker}},
            )
            self._note_tick_failed(exc)
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
        # CHANGE THE SHAPE BEFORE SPENDING ANOTHER ATTEMPT. Bakir's task
        # 43be4591 died on `budget:stop:steps:limit=20.0:actual=20.0` — it ran out
        # of steps. Re-running it identically spends the same twenty steps and
        # stops at the same place, which is the "blind" retry his design rejects:
        # "so next loop when it picks it, it also looks: is any previous one? Yes
        # — learn from that experience."
        #
        # `should_decompose` and `plan_subtasks` were written for exactly this and
        # then never called from anywhere — grep found zero call sites outside
        # their own module. This is the call site.
        if await self._maybe_reshape(task):
            return
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

    async def _maybe_reshape(self, task: Any) -> bool:
        """Split a task that keeps failing for a reason repetition cannot fix.

        Returns True when the task became a parent waiting on children, in which
        case this attempt must NOT also run it.

        NEVER raises and never loses the task. Every failure path here returns
        False, which simply means "run it the ordinary way" — the task stays
        exactly as retryable as it was. A reshaping step that could strand the
        work it is meant to rescue would be worse than not having it.
        """
        try:
            if not wants_reshaping(getattr(task, "last_failure_class", "") or ""):
                return False
            from stackowl.pipeline.durable.decompose import (
                plan_subtasks,
                should_decompose,
            )

            if not should_decompose(task):
                return False
            children = await plan_subtasks(task, self._decomposer())
            if not children:
                return False
            for child in children:
                await self._store.enqueue(child)
            await self._store.set_dependencies(
                task.task_id, tuple(c.task_id for c in children),
            )
            log.tasks.info(
                "[loop] task RESHAPED — repeating it could not have worked",
                extra={"_fields": {
                    "task_id": task.task_id,
                    "failure_class": getattr(task, "last_failure_class", ""),
                    "attempt": getattr(task, "attempt_count", 0),
                    "children": [c.task_id for c in children],
                }},
            )
        except Exception as exc:
            log.tasks.error(
                "[loop] could not reshape a repeatedly-failing task — it stays "
                "retryable in its original form",
                exc_info=exc, extra={"_fields": {"task_id": task.task_id}},
            )
            return False
        else:
            return True

    def _decomposer(self) -> Any:
        """The platform's existing decomposer, or None.

        Resolved lazily from services rather than taken in ``__init__`` for two
        reasons: this module must stay drivable by a test double without importing
        the database, and a constructor argument would let the feature ship
        dormant when a wiring site forgot to pass it. None simply means no split
        is attempted.
        """
        try:
            from stackowl.objectives.decomposer import ObjectiveDecomposer
            from stackowl.pipeline.services import get_services

            registry = getattr(get_services(), "provider_registry", None)
            if registry is None:
                log.tasks.warning(
                    "[loop] no provider registry — a stuck task cannot be split",
                )
                return None
            # Constructed the SAME way objective_tool and the objective driver
            # already build it. Services exposes no decomposer attribute, and
            # adding one would be a second place that knows how to make one.
            return ObjectiveDecomposer(registry)
        except Exception as exc:
            log.tasks.warning(
                "[loop] no decomposer available — a stuck task cannot be split",
                exc_info=exc,
            )
            return None

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
