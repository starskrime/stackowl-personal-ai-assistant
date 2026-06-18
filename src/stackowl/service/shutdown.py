"""ShutdownHandler — graceful SIGTERM/stop with 30-second timeout."""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from stackowl.infra.observability import log
from stackowl.service.pid_manager import PidManager

_SHUTDOWN_TIMEOUT_S = 30


class ShutdownHandler:
    """Coordinates graceful shutdown on SIGTERM (Unix) or service stop (Windows).

    Usage::

        handler = ShutdownHandler()
        handler.register(loop, stop_accepting=server.close)
        handler.add_task(background_task)
        # ... later, SIGTERM arrives ...
        await handler.trigger()
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []
        self._stop_accepting: Callable[[], None | Awaitable[None]] | None = None
        self._contributors: list[Any] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        event_loop: asyncio.AbstractEventLoop,
        stop_accepting: Callable[[], None | Awaitable[None]] | None = None,
    ) -> None:
        """Register signal handlers for graceful shutdown.

        On Windows, ``signal.SIGTERM`` is still registered (works for
        ``os.kill(pid, signal.SIGTERM)`` from the stop command).

        Args:
            event_loop: The running asyncio event loop.
            stop_accepting: Optional async or sync callable that stops accepting
                new work (e.g., close a server socket).
        """
        # 1. ENTRY
        log.infra.debug("[shutdown] register: entry")

        self._stop_accepting = stop_accepting

        # 2. DECISION
        if sys.platform != "win32":
            log.infra.debug("[shutdown] register: decision — Unix signal handler path")
            # 3. STEP — add_signal_handler is more reliable than signal.signal in asyncio
            try:
                event_loop.add_signal_handler(
                    signal.SIGTERM,
                    lambda: asyncio.ensure_future(self.trigger()),
                )
                log.infra.info("[shutdown] register: SIGTERM handler registered via event loop")
            except NotImplementedError:
                # Some platforms don't support add_signal_handler (Windows event loops)
                log.infra.warning("[shutdown] register: add_signal_handler not supported — falling back to signal.signal")
                signal.signal(signal.SIGTERM, lambda _sig, _frame: asyncio.ensure_future(self.trigger()))
        else:
            log.infra.debug("[shutdown] register: decision — Windows signal.signal path")
            signal.signal(signal.SIGTERM, lambda _sig, _frame: asyncio.ensure_future(self.trigger()))
            log.infra.info("[shutdown] register: SIGTERM handler registered via signal.signal")

        # 4. EXIT
        log.infra.debug("[shutdown] register: exit")

    def add_task(self, task: asyncio.Task[Any]) -> None:
        """Track an asyncio task that must complete before shutdown finishes."""
        self._tasks.append(task)

    def add_contributor(self, contributor: Any) -> None:
        """Register a health contributor whose ``shutdown()`` will be called on exit."""
        self._contributors.append(contributor)

    async def trigger(self) -> None:
        """Execute the shutdown sequence with a 30-second hard timeout.

        Steps:
        1. Log initiation.
        2. Stop accepting new work.
        3. Wait up to 30s for tracked tasks to finish.
        4. Call ``shutdown()`` on each health contributor.
        5. Release the PID file.
        6. Exit 0 on clean completion, 1 if tasks remain.
        """
        # 1. ENTRY — shutdown initiated
        log.infra.info("[shutdown] trigger: entry — graceful shutdown initiated")

        # 2. STEP — stop accepting new work
        if self._stop_accepting is not None:
            log.infra.debug("[shutdown] trigger: step — calling stop_accepting")
            try:
                result = self._stop_accepting()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log.infra.error("[shutdown] trigger: stop_accepting raised — %s", exc, exc_info=exc)

        # 3. STEP — wait for tasks
        tasks_ok = True
        if self._tasks:
            log.infra.info(
                "[shutdown] trigger: step — waiting for %d task(s)",
                len(self._tasks),
                extra={"_fields": {"task_count": len(self._tasks), "timeout_s": _SHUTDOWN_TIMEOUT_S}},
            )
            done, pending = await asyncio.wait(self._tasks, timeout=_SHUTDOWN_TIMEOUT_S)
            if pending:
                log.infra.warning(
                    "[shutdown] trigger: %d task(s) did not finish within %ds — cancelling",
                    len(pending),
                    _SHUTDOWN_TIMEOUT_S,
                )
                for t in pending:
                    t.cancel()
                tasks_ok = False
            else:
                log.infra.info("[shutdown] trigger: all tasks completed cleanly")

        # 4. STEP — call shutdown() on contributors
        for contributor in self._contributors:
            shutdown_fn = getattr(contributor, "shutdown", None)
            if shutdown_fn is not None:
                try:
                    result = shutdown_fn()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    log.infra.error(
                        "[shutdown] trigger: contributor.shutdown() raised — %s", exc, exc_info=exc
                    )

        # 5. STEP — release PID file
        log.infra.debug("[shutdown] trigger: step — releasing PID file")
        try:
            PidManager().release()
        except Exception as exc:
            log.infra.error("[shutdown] trigger: PID release failed — %s", exc, exc_info=exc)

        # 6. EXIT — choose exit code
        exit_code = 0 if tasks_ok else 1
        log.infra.info(
            "[shutdown] trigger: exit — shutdown complete",
            extra={"_fields": {"exit_code": exit_code, "tasks_ok": tasks_ok}},
        )
        raise SystemExit(exit_code)
