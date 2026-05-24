"""Clock protocol — injectable time source for testable time-based components."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable


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
