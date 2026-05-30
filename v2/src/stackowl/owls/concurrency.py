"""ConcurrencyGovernor — shared budget for in-flight delegated/parliament runs.

ONE instance is constructed in the startup assembly and injected into BOTH the
:class:`stackowl.owls.a2a_delegation.A2ADelegator` (via ``StepServices``) and the
parliament fan-out, so they draw from a SINGLE budget rather than two. This is
the "fork 3" shared-semaphore mitigation: total concurrent pipelines on one host
are bounded at ``MAX_INFLIGHT_PIPELINES`` regardless of which path spawned them.

Self-healing: the slot is an async context manager that releases in ``finally``,
so a crashed/cancelled specialist NEVER leaks a permit or wedges the budget.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from stackowl.exceptions import InfrastructureError
from stackowl.infra.observability import log
from stackowl.owls.delegation_limits import MAX_INFLIGHT_PIPELINES


class GovernorSaturatedError(InfrastructureError):
    """Raised when a bounded ``slot(timeout=...)`` acquire cannot get a permit in time.

    A ``StackOwlError`` subclass so the delegation/parliament call sites already
    catching ``StackOwlError`` degrade gracefully (reply empty/structured + free
    the parent) instead of deadlocking under acquire-while-holding saturation.
    """


class ConcurrencyGovernor:
    """Bounds total in-flight pipelines via a shared :class:`asyncio.Semaphore`.

    Callers acquire a slot with ``async with governor.slot(): ...``; when the
    budget is saturated the caller AWAITS a free slot (bounded by the natural
    completion of in-flight runs). The permit is always released in ``finally``.
    """

    def __init__(self, max_inflight: int = MAX_INFLIGHT_PIPELINES) -> None:
        if max_inflight <= 0:
            raise ValueError("max_inflight must be > 0")
        # 1. ENTRY
        log.engine.debug(
            "[governor] __init__: entry",
            extra={"_fields": {"max_inflight": max_inflight}},
        )
        self._max_inflight = max_inflight
        self._sem = asyncio.Semaphore(max_inflight)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        """Number of slots currently held — introspection for health checks."""
        return self._in_flight

    @property
    def max_inflight(self) -> int:
        return self._max_inflight

    @asynccontextmanager
    async def slot(self, timeout: float | None = None) -> AsyncIterator[None]:
        """Acquire a pipeline slot; release it on exit (even on crash/cancel).

        ``timeout=None`` waits indefinitely for a free permit (the original
        behaviour). A positive ``timeout`` bounds the wait: if no permit frees in
        time, :class:`GovernorSaturatedError` is raised so an acquire-while-holding
        saturation fails fast (and the caller frees its own resources) instead of
        deadlocking. Self-healing.
        """
        # 2. DECISION — acquire (awaits when saturated; bounded if timeout set)
        log.engine.debug(
            "[governor] slot: acquiring",
            extra={"_fields": {"in_flight": self._in_flight, "max": self._max_inflight,
                               "timeout": timeout}},
        )
        if timeout is None:
            await self._sem.acquire()
        else:
            try:
                await asyncio.wait_for(self._sem.acquire(), timeout)
            except TimeoutError as exc:
                log.engine.warning(
                    "[governor] slot: saturated — acquire timed out",
                    extra={"_fields": {"in_flight": self._in_flight,
                                       "max": self._max_inflight, "timeout": timeout}},
                )
                raise GovernorSaturatedError(
                    f"no pipeline slot free within {timeout}s "
                    f"({self._in_flight}/{self._max_inflight} in flight)"
                ) from exc
        self._in_flight += 1
        # 3. STEP — slot held
        log.engine.debug(
            "[governor] slot: acquired",
            extra={"_fields": {"in_flight": self._in_flight, "max": self._max_inflight}},
        )
        try:
            yield
        finally:
            self._in_flight -= 1
            self._sem.release()
            # 4. EXIT — slot released (never leaks on exception/cancel)
            log.engine.debug(
                "[governor] slot: released",
                extra={"_fields": {"in_flight": self._in_flight, "max": self._max_inflight}},
            )
