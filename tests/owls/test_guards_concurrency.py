"""CONC-4 (F077) — OwlResourceGuard concurrency accounting must not depend on
CPython ``asyncio.Semaphore._value``.

The race: with ``max_concurrent_requests=1`` two overlapping ``stream()`` calls
must let exactly ONE through and refuse the other with ``OwlConcurrencyError``;
the slot must then be released so a third (sequential) call succeeds. A barrier
forces the two coroutines to overlap inside the guarded region.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from stackowl.exceptions import OwlConcurrencyError
from stackowl.owls.guards import OwlResourceGuard
from stackowl.owls.manifest import OwlAgentManifest


class _BarrierProvider:
    """A fake ModelProvider whose stream blocks on a shared event mid-flight.

    The first chunk is yielded, then the coroutine awaits ``hold`` so the slot
    is demonstrably occupied while a second concurrent call attempts to acquire.
    """

    name = "barrier"

    def __init__(self, hold: asyncio.Event, entered: asyncio.Event) -> None:
        self._hold = hold
        self._entered = entered

    async def stream(
        self, messages: object, model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        self._entered.set()
        yield "first "
        await self._hold.wait()
        yield "second"


def _manifest() -> OwlAgentManifest:
    return OwlAgentManifest(
        name="concowl",
        role="tester",
        system_prompt="x",
        model_tier="standard",
        max_concurrent_requests=1,
        max_tokens=10_000,
        timeout_seconds=30.0,
    )


async def _drain(guard: OwlResourceGuard, provider: object) -> str:
    out = ""
    async for text in guard.stream(provider, [], "m"):
        out += text
    return out


@pytest.mark.asyncio
async def test_concurrent_acquire_refuses_second_and_releases() -> None:
    guard = OwlResourceGuard(_manifest())
    hold = asyncio.Event()
    entered = asyncio.Event()
    provider = _BarrierProvider(hold, entered)

    # First call enters the guarded region and parks inside the provider stream.
    holder = asyncio.create_task(_drain(guard, provider))
    await asyncio.wait_for(entered.wait(), timeout=2.0)

    # Second concurrent call must be refused — the single slot is occupied.
    with pytest.raises(OwlConcurrencyError):
        await _drain(guard, _BarrierProvider(asyncio.Event(), asyncio.Event()))

    # Release the holder; the slot must come back.
    hold.set()
    assert await asyncio.wait_for(holder, timeout=2.0) == "first second"

    # A subsequent sequential call must now succeed (slot was released).
    done = asyncio.Event()
    done.set()
    out = await _drain(guard, _BarrierProvider(done, asyncio.Event()))
    assert out == "first second"


@pytest.mark.asyncio
async def test_guard_does_not_probe_semaphore_private_value() -> None:
    """The non-blocking acquire path must not read ``Semaphore._value`` —
    a guard against re-introducing the CPython-internal dependency (F077)."""
    import inspect

    from stackowl.owls import guards as guards_mod

    src = inspect.getsource(guards_mod)
    assert "._value" not in src, "guard must not probe asyncio.Semaphore._value"
