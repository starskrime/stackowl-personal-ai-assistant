"""Wait for a CONDITION, never for a duration.

A concurrency test that sleeps a fixed number of milliseconds and then asserts on
state another task must have reached is not testing concurrency — it is betting
that the scheduler is fast enough today. The bet is lost under load, which is
exactly when the suite is least able to explain itself:
`test_two_sessions_no_interjection_clobber` passed alone and in its own package
and failed once in a 11,875-test run, because 50ms was its whole margin for two
tasks to register themselves.

That is the same root cause already recorded for the scheduler concurrency flake
on 2026-09-01 — a wall-clock threshold standing in for an observable property —
and it is a bet no test needs to make: the condition it actually wants can be
polled until it is true.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def await_until(
    predicate: Callable[[], bool],
    what: str,
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.005,
) -> None:
    """Poll ``predicate`` until true, or fail naming what never happened.

    ``what`` is not decoration: a bare timeout in a concurrency test says only
    "something did not happen", which is how a real deadlock gets re-run away as
    a flake. The timeout is a HANG GUARD, not a synchronisation budget — passing
    does not depend on how long it took, only on the condition becoming true.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        if predicate():
            return
        if loop.time() >= deadline:
            raise AssertionError(f"waited {timeout_s}s and never became true: {what}")
        await asyncio.sleep(interval_s)
