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
    asyncio_task: asyncio.Task[None] | None = None
    started_at: float = field(default_factory=time.monotonic)


class Supervisor:
    """Manages a set of SupervisedTask instances with exponential-backoff restarts."""

    def __init__(self, *, clock: Clock = WallClock()) -> None:
        self._clock = clock
        self._tasks: dict[str, _TaskState] = {}

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
                await state.task.run()
                state.consecutive_failures = 0
                backoff = _BACKOFF_INITIAL
                duration_ms = (self._clock.monotonic() - t0) * 1000
                log.startup.debug(
                    "[supervisor] task: completed cleanly — restarting",
                    extra={"_fields": {"task_id": state.task.task_id, "duration_ms": duration_ms}},
                )
            except asyncio.CancelledError:
                log.startup.info(
                    "[supervisor] task: cancelled",
                    extra={"_fields": {"task_id": state.task.task_id}},
                )
                state.status = "stopped"
                return
            except Exception as exc:
                state.consecutive_failures += 1
                duration_ms = (self._clock.monotonic() - t0) * 1000
                attempt = state.consecutive_failures
                log.startup.warning(
                    "[supervisor] %s: restarting (attempt %d, backoff %.0fs)",
                    state.task.task_id,
                    attempt,
                    backoff,
                    extra={"_fields": {"task_id": state.task.task_id, "exc": str(exc), "duration_ms": duration_ms}},
                )
                if state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    log.startup.error(
                        "[supervisor] %s: max consecutive failures reached — marking failed",
                        state.task.task_id,
                        extra={"_fields": {"task_id": state.task.task_id, "failures": state.consecutive_failures}},
                    )
                    state.status = "failed"
                    return

            await self._clock.async_sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


def make_supervised_task(task_id: str, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> SupervisedTask:
    """Wrap a plain coroutine function as a SupervisedTask."""

    class _FnTask(SupervisedTask):
        @property
        def task_id(self) -> str:
            return task_id

        async def run(self) -> None:
            await coro_fn()

    return _FnTask()
