"""SandboxGovernor — bounds total concurrent sandbox runs on one host (E11-S6).

N concurrent sandbox runs each capped at ``caps.mem_mib`` (default 2048 MiB) can,
in aggregate, OOM the HOST. This governor enforces a SINGLE global ceiling via a
shared :class:`asyncio.Semaphore` so the aggregate worst-case memory is bounded at
``MAX_CONCURRENT_SANDBOXES × DEFAULT_MEM_MIB`` regardless of how many turns ask to
run code at once (see the math in :mod:`stackowl.sandbox.limits`).

ONE instance is constructed in the startup orchestrator and injected onto
``StepServices`` so the ``execute_code`` tool draws from a SINGLE budget. Mirrors
:class:`stackowl.owls.concurrency.ConcurrencyGovernor`:

* The slot is an async context manager that releases in ``finally``, so a
  crashed/cancelled run NEVER leaks a permit or wedges the budget (self-healing).
* The acquire is BOUNDED: past ``timeout`` seconds without a free permit the
  governor REFUSES with a typed :class:`SandboxSaturatedError` (B5) — it NEVER
  blocks forever and NEVER deadlocks. The tool maps the refusal to a clean
  "too many code executions running right now" result; nothing runs.

The bounded wait uses ``asyncio.wait_for`` (mirroring the sibling
:class:`ConcurrencyGovernor`); the deterministic TTL reaping lives in the
clock-injected :class:`~stackowl.scheduler.handlers.sandbox_sweep.SandboxSweepHandler`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from stackowl.exceptions import InfrastructureError
from stackowl.infra.observability import log
from stackowl.sandbox.limits import MAX_CONCURRENT_SANDBOXES

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.sandbox.base import SandboxBackend
    from stackowl.sandbox.spec import ExecResult, ExecSpec

__all__ = ["SandboxGovernor", "SandboxSaturatedError", "run_under_slot"]

# Bounded default wait for a free slot before REFUSING. Short: a saturated host
# should fail fast with a clean "try again in a moment" rather than make the user
# wait. The caller may override per-acquire.
DEFAULT_SLOT_TIMEOUT_S = 0.5


class SandboxSaturatedError(InfrastructureError):
    """Raised when a bounded :meth:`SandboxGovernor.slot` acquire cannot get a permit.

    A typed signal (an :class:`InfrastructureError` subclass) the ``execute_code``
    tool catches and maps to a clean structured refusal — never a deadlock, never
    an unbounded wait, never a host-exec fallback.
    """


class SandboxGovernor:
    """Bounds total concurrent sandbox runs via a shared :class:`asyncio.Semaphore`.

    Callers acquire a slot with ``async with governor.slot(): ...``. When the
    budget is saturated the acquire is BOUNDED by ``timeout``; if no permit frees
    in time the governor raises :class:`SandboxSaturatedError` (fail-fast refusal)
    rather than blocking. The permit is always released in ``finally``.
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SANDBOXES) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")
        # 1. ENTRY
        log.tool.debug(
            "[sandbox.governor] __init__: entry",
            extra={"_fields": {"max_concurrent": max_concurrent}},
        )
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        """Number of sandbox slots currently held — introspection for health/logging."""
        return self._in_flight

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @asynccontextmanager
    async def slot(
        self, timeout: float | None = DEFAULT_SLOT_TIMEOUT_S
    ) -> AsyncIterator[None]:
        """Acquire a sandbox slot; release it on exit (even on crash/cancel).

        ``timeout`` BOUNDS the wait: if no permit frees within ``timeout`` seconds
        :class:`SandboxSaturatedError` is raised so a saturated host fails fast (the
        tool maps it to a clean refusal) instead of blocking. ``timeout=None`` waits
        for a free permit (bounded only by the natural completion of in-flight runs;
        used by callers that explicitly want to queue). Self-healing: the permit is
        released in ``finally`` so a cancelled/crashed run never leaks it.
        """
        # 2. DECISION — acquire (bounded refuse when saturated unless timeout=None).
        log.tool.debug(
            "[sandbox.governor] slot: acquiring",
            extra={"_fields": {"in_flight": self._in_flight,
                               "max": self._max_concurrent, "timeout": timeout}},
        )
        if timeout is None:
            await self._sem.acquire()
        else:
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout)
            except TimeoutError as exc:
                log.tool.warning(
                    "[sandbox.governor] slot: saturated — acquire timed out, REFUSING",
                    extra={"_fields": {"in_flight": self._in_flight,
                                       "max": self._max_concurrent, "timeout": timeout}},
                )
                raise SandboxSaturatedError(
                    f"no sandbox slot free within {timeout}s "
                    f"({self._in_flight}/{self._max_concurrent} runs in flight)"
                ) from exc
        self._in_flight += 1
        # 3. STEP — slot held.
        log.tool.debug(
            "[sandbox.governor] slot: acquired",
            extra={"_fields": {"in_flight": self._in_flight, "max": self._max_concurrent}},
        )
        try:
            yield
        finally:
            self._in_flight -= 1
            self._sem.release()
            # 4. EXIT — slot released (never leaks on exception/cancel).
            log.tool.debug(
                "[sandbox.governor] slot: released",
                extra={"_fields": {"in_flight": self._in_flight,
                                   "max": self._max_concurrent}},
            )


async def run_under_slot(
    governor: SandboxGovernor | None,
    backend: SandboxBackend,
    spec: ExecSpec,
) -> ExecResult:
    """Run ``backend.run(spec)`` while holding a governor slot (if a governor is set).

    ``governor=None`` runs ungated (back-compat). When a governor IS set the run
    holds one slot for its whole duration; on saturation past the bounded wait the
    governor raises :class:`SandboxSaturatedError`, which the caller maps to a clean
    refusal — the backend is NEVER invoked in that case (nothing runs).
    """
    if governor is None:
        return await backend.run(spec)
    async with governor.slot():
        return await backend.run(spec)
