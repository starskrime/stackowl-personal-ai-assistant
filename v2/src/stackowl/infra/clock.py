"""Clock protocol — injectable time source for testable time-based components.

Also exposes a tiny wall-clock helper pair (``now_local`` / ``local_iso``) for
code that just needs a correct, current, timezone-aware date — notably the
agentic base prompt, which injects "today" so a weak model never falls back on
its stale training cutoff. Kept simple: no Settings in scope here, so we use the
system local zone via ``datetime.now().astimezone()`` (always tz-aware).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Protocol, runtime_checkable


def now_local() -> datetime:
    """Return the current local time as a timezone-aware ``datetime``.

    ``datetime.now().astimezone()`` attaches the system's local UTC offset, so
    callers always get a tz-aware value carrying a correct, current date.
    """
    return datetime.now().astimezone()


def local_iso() -> str:
    """Return the current local time as an ISO-8601 string."""
    return now_local().isoformat()


@runtime_checkable
class Clock(Protocol):
    """Time source for all time-based components (ARCH-99)."""

    def monotonic(self) -> float:
        """Return monotonic time in fractional seconds."""
        ...

    async def async_sleep(self, seconds: float) -> None:
        """Async-compatible sleep for the given number of seconds."""
        ...


class WallClock:
    """Production clock backed by the system wall clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
