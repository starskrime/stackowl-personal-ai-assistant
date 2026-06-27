"""Supervisor — background task management with exponential backoff restart (ARCH-102)."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log

_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 60.0
_MAX_CONSECUTIVE_FAILURES = 5

# F-75: tight-loop guard. A clean run() return faster than ``_TIGHT_LOOP_SECONDS``
# did essentially no work; ``_MAX_TIGHT_LOOP_RETURNS`` such returns in a row is a
# spin (a no-op task busy-restarting forever, resetting the failure counter each
# time), not health. Kept sub-millisecond so genuine fast work is never flagged.
_TIGHT_LOOP_SECONDS = 0.001
_MAX_TIGHT_LOOP_RETURNS = 100

# Why a task is being escalated. ``stuck_timeout`` = a single run() exceeded the
# watchdog budget (live-but-stuck, F-73); ``max_failures`` = the give-up floor
# was hit (F-74); ``tight_loop`` = repeated no-op rapid clean returns (F-75).
EscalationReason = Literal["stuck_timeout", "max_failures", "tight_loop"]


@dataclass(frozen=True)
class EscalationEvent:
    """A recoverable signal raised when a supervised task needs operator attention.

    Emitted both when the watchdog trips on a live-but-stuck task (F-73) and when
    the consecutive-failure floor parks a task as ``failed`` (F-74). Callers wire
    :class:`Supervisor`'s ``on_escalation`` hook to a real notification/alert seam;
    when unwired the supervisor still logs the escalation at ``error`` level with a
    clear ``ESCALATION`` marker.
    """

    task_id: str
    reason: EscalationReason
    consecutive_failures: int
    detail: str


# An escalation hook may be sync (returns ``None``) or async (returns a coroutine
# the supervisor awaits). Exceptions raised by the hook are caught and logged so a
# bad hook can never break supervisor bookkeeping.
EscalationHook = Callable[[EscalationEvent], "Coroutine[Any, Any, None] | None"]


class _StuckTimeout(Exception):
    """Internal: a single run() exceeded the watchdog budget (distinct from a crash)."""


class SupervisedTask(ABC):
    """ABC for tasks managed by Supervisor (ARCH-102)."""

    @property
    @abstractmethod
    def task_id(self) -> str:
        """Unique identifier for this task."""
        ...

    @abstractmethod
    async def run(self) -> None:
        """Execute the task body. Supervisor calls this in a loop."""
        ...


@dataclass
class _TaskState:
    task: SupervisedTask
    status: Literal["running", "failed", "stopped"] = "stopped"
    consecutive_failures: int = 0
    # F-75: count of consecutive sub-threshold ("did no work") clean returns.
    tight_loop_streak: int = 0
    asyncio_task: asyncio.Task[None] | None = None
    started_at: float = field(default_factory=time.monotonic)


class Supervisor:
    """Manages a set of SupervisedTask instances with exponential-backoff restarts."""

    def __init__(
        self,
        *,
        clock: Clock = WallClock(),
        on_escalation: EscalationHook | None = None,
        max_run_seconds: float | None = None,
        tight_loop_seconds: float = _TIGHT_LOOP_SECONDS,
        max_tight_loop_returns: int = _MAX_TIGHT_LOOP_RETURNS,
    ) -> None:
        self._clock = clock
        self._tasks: dict[str, _TaskState] = {}
        # F-74: optional operator-notification seam invoked on escalation.
        self._on_escalation = on_escalation
        # F-73: optional watchdog. When set, a single run() that exceeds this many
        # seconds is treated as live-but-stuck, cancelled, and restarted. Default
        # ``None`` (disabled) keeps perpetual-loop tasks (e.g. JobScheduler) intact.
        self._max_run_seconds = max_run_seconds
        # F-75: tight-loop guard thresholds (constructor-overridable). A clean
        # return faster than ``tight_loop_seconds`` did no work; this many in a row
        # is a spin and is escalated/parked rather than busy-restarted forever.
        self._tight_loop_seconds = tight_loop_seconds
        self._max_tight_loop_returns = max_tight_loop_returns

    def register(self, task: SupervisedTask) -> None:
        """Register a task. Must be called before start()."""
        log.startup.debug(
            "[supervisor] register: task registered",
            extra={"_fields": {"task_id": task.task_id}},
        )
        self._tasks[task.task_id] = _TaskState(task=task)

    async def start(self) -> None:
        """Start all registered tasks."""
        log.startup.info(
            "[supervisor] start: entry",
            extra={"_fields": {"task_count": len(self._tasks)}},
        )
        for state in self._tasks.values():
            state.asyncio_task = asyncio.create_task(
                self._run_with_backoff(state),
                name=f"supervisor.{state.task.task_id}",
            )
            state.status = "running"
        log.startup.info("[supervisor] start: all tasks launched")

    async def stop(self) -> None:
        """Cancel all running tasks and await their completion."""
        log.startup.info("[supervisor] stop: entry")
        for state in self._tasks.values():
            if state.asyncio_task and not state.asyncio_task.done():
                state.asyncio_task.cancel()
                state.status = "stopped"
        await asyncio.gather(
            *(s.asyncio_task for s in self._tasks.values() if s.asyncio_task),
            return_exceptions=True,
        )
        log.startup.info("[supervisor] stop: all tasks stopped")

    def health(self) -> dict[str, Literal["running", "failed", "stopped"]]:
        """Return the current status of every registered task."""
        return {tid: state.status for tid, state in self._tasks.items()}

    async def _run_with_backoff(self, state: _TaskState) -> None:
        backoff = _BACKOFF_INITIAL
        while True:
            t0 = self._clock.monotonic()
            try:
                log.startup.debug(
                    "[supervisor] task: starting",
                    extra={"_fields": {"task_id": state.task.task_id}},
                )
                await self._invoke(state)
            except asyncio.CancelledError:
                log.startup.info(
                    "[supervisor] task: cancelled",
                    extra={"_fields": {"task_id": state.task.task_id}},
                )
                state.status = "stopped"
                return
            except _StuckTimeout as exc:
                # F-73: live-but-stuck — watchdog cancelled the run; escalate + restart.
                duration_ms = (self._clock.monotonic() - t0) * 1000
                gave_up = await self._record_failure(
                    state, detail=str(exc), stuck=True, duration_ms=duration_ms
                )
                if gave_up:
                    return
            except Exception as exc:
                duration_ms = (self._clock.monotonic() - t0) * 1000
                gave_up = await self._record_failure(
                    state, detail=str(exc), stuck=False, duration_ms=duration_ms
                )
                if gave_up:
                    return
            else:
                duration_ms = (self._clock.monotonic() - t0) * 1000
                gave_up = await self._record_clean_return(state, duration_ms=duration_ms)
                if gave_up:
                    return
                backoff = _BACKOFF_INITIAL

            await self._clock.async_sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _invoke(self, state: _TaskState) -> None:
        """Run the task body once, applying the max-runtime watchdog if configured."""
        if self._max_run_seconds is None:
            await state.task.run()
            return
        try:
            await asyncio.wait_for(state.task.run(), timeout=self._max_run_seconds)
        except TimeoutError as exc:
            # Translate to a distinct type so a genuine crash is never mislabelled
            # as "stuck" (and vice-versa). wait_for already cancelled the run.
            raise _StuckTimeout(
                f"run exceeded max_run_seconds={self._max_run_seconds:.3g}s"
            ) from exc

    async def _record_failure(
        self, state: _TaskState, *, detail: str, stuck: bool, duration_ms: float
    ) -> bool:
        """Account one failed run; escalate when warranted. Returns True if parked failed."""
        # A real failure (or stuck trip) is not a no-op clean spin — reset that guard.
        state.tight_loop_streak = 0
        state.consecutive_failures += 1
        attempt = state.consecutive_failures
        log.startup.warning(
            "[supervisor] %s: restarting (attempt %d, stuck=%s)",
            state.task.task_id,
            attempt,
            stuck,
            extra={
                "_fields": {
                    "task_id": state.task.task_id,
                    "exc": detail,
                    "stuck": stuck,
                    "duration_ms": duration_ms,
                }
            },
        )
        # F-73: surface the live-but-stuck signal the moment the watchdog trips.
        if stuck:
            await self._escalate(state, "stuck_timeout", detail)
        # F-74: the give-up floor escalates instead of silently parking the task dead.
        if state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            log.startup.error(
                "[supervisor] ESCALATION %s: max consecutive failures reached — marking failed",
                state.task.task_id,
                extra={
                    "_fields": {
                        "task_id": state.task.task_id,
                        "failures": state.consecutive_failures,
                    }
                },
            )
            state.status = "failed"
            await self._escalate(state, "max_failures", detail)
            return True
        return False

    async def _record_clean_return(self, state: _TaskState, *, duration_ms: float) -> bool:
        """Account one clean run() return; guard against a no-op tight loop (F-75).

        A normal (slow, real-work) clean return resets every counter and behaves
        exactly as before. But a task that returns cleanly faster than
        ``tight_loop_seconds`` did essentially no work; ``max_tight_loop_returns``
        such returns in a row is a spin, not health — so we escalate via the F-74
        seam and park the task ``failed`` instead of busy-restarting forever.
        Returns True if the task was parked failed.
        """
        state.consecutive_failures = 0
        if duration_ms >= self._tight_loop_seconds * 1000:
            # Genuine work elapsed — a healthy clean return; reset the spin guard.
            state.tight_loop_streak = 0
            log.startup.debug(
                "[supervisor] task: completed cleanly — restarting",
                extra={"_fields": {"task_id": state.task.task_id, "duration_ms": duration_ms}},
            )
            return False
        state.tight_loop_streak += 1
        log.startup.debug(
            "[supervisor] task: suspiciously fast clean return",
            extra={
                "_fields": {
                    "task_id": state.task.task_id,
                    "duration_ms": duration_ms,
                    "tight_loop_streak": state.tight_loop_streak,
                    "threshold_s": self._tight_loop_seconds,
                }
            },
        )
        if state.tight_loop_streak >= self._max_tight_loop_returns:
            detail = (
                f"{state.tight_loop_streak} clean returns under "
                f"{self._tight_loop_seconds:.3g}s — spinning (no work done)"
            )
            log.startup.error(
                "[supervisor] ESCALATION %s: tight-loop spin detected — marking failed",
                state.task.task_id,
                extra={
                    "_fields": {
                        "task_id": state.task.task_id,
                        "tight_loop_streak": state.tight_loop_streak,
                    }
                },
            )
            state.status = "failed"
            await self._escalate(state, "tight_loop", detail)
            return True
        return False

    async def _escalate(self, state: _TaskState, reason: EscalationReason, detail: str) -> None:
        """Emit a recoverable escalation signal and invoke the operator hook if wired."""
        event = EscalationEvent(
            task_id=state.task.task_id,
            reason=reason,
            consecutive_failures=state.consecutive_failures,
            detail=detail,
        )
        log.startup.error(
            "[supervisor] ESCALATION: task %s — %s",
            state.task.task_id,
            reason,
            extra={
                "_fields": {
                    "task_id": state.task.task_id,
                    "reason": reason,
                    "detail": detail,
                    "failures": state.consecutive_failures,
                }
            },
        )
        hook = self._on_escalation
        if hook is None:
            return
        try:
            result = hook(event)
            if result is not None:
                await result
        except Exception as exc:
            log.startup.error(
                "[supervisor] %s: escalation hook raised — ignored",
                state.task.task_id,
                extra={"_fields": {"task_id": state.task.task_id, "exc": str(exc)}},
            )


def make_supervised_task(task_id: str, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> SupervisedTask:
    """Wrap a plain coroutine function as a SupervisedTask."""

    class _FnTask(SupervisedTask):
        @property
        def task_id(self) -> str:
            return task_id

        async def run(self) -> None:
            await coro_fn()

    return _FnTask()
