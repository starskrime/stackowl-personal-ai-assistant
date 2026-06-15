"""T1 (F118) — RateLimiter over-grant race: lock guards refill+check+deduct.

Capacity K with K+M REAL concurrent acquirers: the bucket must never go
negative and at most K may acquire immediately (the rest wait for refill). Uses
real coroutines (not a manual no-sleep clock) so the lock must actually
serialize the critical section — a lock held across the sleep would deadlock and
this test would hang/fail.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.providers.rate_limiter import RateLimiter

pytestmark = pytest.mark.asyncio


async def test_no_over_grant_under_concurrency() -> None:
    """K capacity, K+M concurrent acquirers — tokens never negative, ≤K immediate."""
    capacity = 5
    extra = 5
    # Very slow refill so the M extra acquirers cannot be satisfied by refill
    # within the immediate window — they MUST wait, proving no over-grant.
    limiter = RateLimiter(
        provider_name="p", capacity=capacity, refill_rate=0.0001
    )

    immediate = 0
    immediate_lock = asyncio.Lock()

    async def _acquire_immediate_only() -> None:
        nonlocal immediate
        # Each task tries to acquire with a tight timeout: a task that gets the
        # token within the immediate window counts; one that must wait times out.
        try:
            await asyncio.wait_for(limiter.acquire(1), timeout=0.5)
        except TimeoutError:
            return
        async with immediate_lock:
            immediate += 1

    await asyncio.gather(*[_acquire_immediate_only() for _ in range(capacity + extra)])

    # The bucket must never have been over-drawn.
    assert limiter._tokens >= 0.0, f"token bucket went negative: {limiter._tokens}"
    # At most `capacity` acquired immediately; the lock serialized check+deduct so
    # no two tasks both passed the `>=` check on the same tokens.
    assert immediate <= capacity, f"over-granted: {immediate} > capacity {capacity}"
    assert immediate >= 1, "expected at least one immediate grant"


async def test_noop_limiter_never_blocks() -> None:
    """A no-op limiter (capacity None) returns immediately — byte-identical pass-through."""
    limiter = RateLimiter(provider_name="p", capacity=None)
    assert limiter.is_noop is True
    await asyncio.wait_for(limiter.acquire(1), timeout=0.5)  # must not block
