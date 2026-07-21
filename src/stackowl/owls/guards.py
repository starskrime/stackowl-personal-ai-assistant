"""OwlResourceGuard — enforces per-owl resource budgets (token, timeout, concurrency).

One instance per owl. The semaphore is held for the lifetime of the provider call
and released in a ``finally`` block. Timeout is enforced per-item by wrapping each
``__anext__()`` call on the provider's stream in ``asyncio.wait_for`` — the standard
pattern for bounding an async generator, covering both a stalled time-to-first-chunk
and a stall between later chunks. Token count is approximated by whitespace-splitting
yielded text.

This is a class-based guard (OOP) — never a free function.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import OwlConcurrencyError, OwlTimeoutError
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.providers.base import Message, ModelProvider


# Sliding window (seconds) for tracking timeout-violation health.
_TIMEOUT_HEALTH_WINDOW_S: float = 300.0

# Threshold of violations within the window that flips ``is_degraded`` to True.
_TIMEOUT_DEGRADED_THRESHOLD: int = 3


class _NonBlockingSlots:
    """A non-blocking counting slot pool backed by a plain int + an asyncio.Lock.

    Replaces the prior private-counter probe of ``asyncio.Semaphore`` (F077):
    there is no public non-blocking ``Semaphore.acquire`` and stepping the
    acquire coroutine by hand depends on CPython's fast-path staying unchanged
    across interpreter versions. A held lock guards
    the counter so a non-blocking ``try_acquire`` and ``release`` are correct on
    any interpreter and across concurrent ``await``-interleaved callers on one
    loop. The lock is held only for the O(1) counter mutation — never across the
    guarded provider call — so it never serializes the actual work.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._in_use = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        """Take a slot without blocking; return False when all slots are in use."""
        async with self._lock:
            if self._in_use >= self._capacity:
                return False
            self._in_use += 1
            return True

    async def release(self) -> None:
        """Return a slot. Never drops below zero (idempotent under double-release)."""
        async with self._lock:
            if self._in_use > 0:
                self._in_use -= 1


class OwlResourceGuard:
    """Wrap a ModelProvider call with timeout, token-limit, and concurrency guards.

    One instance per owl. Reusable across many ``stream()`` calls. Tracks a
    rolling window of timeout violations for downstream health reporting.
    """

    def __init__(self, manifest: OwlAgentManifest) -> None:
        self._manifest = manifest
        self._slots = _NonBlockingSlots(manifest.max_concurrent_requests)
        self._timeout_violation_count: int = 0
        self._timeout_violation_window_start: float = 0.0
        log.engine.debug(
            "[guard] init: entry",
            extra={
                "_fields": {
                    "owl": manifest.name,
                    "max_concurrent": manifest.max_concurrent_requests,
                    "max_tokens": manifest.max_tokens,
                    "timeout_seconds": manifest.timeout_seconds,
                }
            },
        )

    # ------------------------------------------------------------------
    # Health-tracking surface
    # ------------------------------------------------------------------

    def record_timeout(self) -> None:
        """Track a timeout violation in the rolling window."""
        now = time.monotonic()
        if now - self._timeout_violation_window_start > _TIMEOUT_HEALTH_WINDOW_S:
            self._timeout_violation_count = 0
            self._timeout_violation_window_start = now
        self._timeout_violation_count += 1
        log.engine.debug(
            "[guard] record_timeout: tally updated",
            extra={
                "_fields": {
                    "owl": self._manifest.name,
                    "count_in_window": self._timeout_violation_count,
                }
            },
        )

    @property
    def is_degraded(self) -> bool:
        """True if >= 3 timeout violations occurred in the last 5 minutes."""
        now = time.monotonic()
        if now - self._timeout_violation_window_start > _TIMEOUT_HEALTH_WINDOW_S:
            return False
        return self._timeout_violation_count >= _TIMEOUT_DEGRADED_THRESHOLD

    @property
    def owl_name(self) -> str:
        return self._manifest.name

    # ------------------------------------------------------------------
    # Stream wrapper — the core enforcement surface
    # ------------------------------------------------------------------

    async def stream(
        self,
        provider: ModelProvider,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        """Stream tokens from ``provider`` under guard.

        Enforces:
        1. Concurrency — non-blocking semaphore acquire (raises OwlConcurrencyError).
        2. Timeout — per-item ``asyncio.wait_for`` on ``__anext__()``, covering both
           time-to-first-chunk and inter-chunk stalls (raises OwlTimeoutError).
        3. Token budget — whitespace-token count vs ``manifest.max_tokens``;
           when exceeded the stream stops cleanly (no exception bubbled).

        TestModeGuard.assert_not_test_mode is invoked before any acquire.
        """
        async for text in self._stream_impl(provider, messages, model, **kwargs):
            yield text

    async def _stream_impl(
        self,
        provider: ModelProvider,
        messages: list[Message],
        model: str,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        # 1. ENTRY
        log.engine.debug(
            "[guard] stream: entry",
            extra={
                "_fields": {
                    "owl": self._manifest.name,
                    "provider": provider.name,
                    "msg_count": len(messages),
                }
            },
        )
        TestModeGuard.assert_not_test_mode("owl.execute")

        # 2. DECISION — non-blocking slot acquire (counter + lock, not a probe
        # of the semaphore's private counter — F077).
        if not await self._slots.try_acquire():
            log.engine.warning(
                "[guard] stream: concurrency limit reached",
                extra={
                    "_fields": {
                        "owl": self._manifest.name,
                        "max_concurrent": self._manifest.max_concurrent_requests,
                    }
                },
            )
            raise OwlConcurrencyError(
                self._manifest.name,
                self._manifest.max_concurrent_requests,
            )

        token_count = 0
        t0 = time.monotonic()
        stream_iter = provider.stream(messages, model, **kwargs)
        try:
            while True:
                # 3a. STEP — per-item timeout, bounds time-to-first-chunk AND any
                # later stall (the remaining budget shrinks with elapsed time so
                # a slow-but-steady stream can't reset the deadline forever).
                remaining = self._manifest.timeout_seconds - (time.monotonic() - t0)
                try:
                    text = await asyncio.wait_for(stream_iter.__anext__(), timeout=max(remaining, 0.0))
                except TimeoutError:
                    self.record_timeout()
                    log.engine.warning(
                        "[guard] stream: timeout exceeded",
                        extra={
                            "_fields": {
                                "owl": self._manifest.name,
                                "elapsed_s": round(time.monotonic() - t0, 3),
                                "limit_s": self._manifest.timeout_seconds,
                            }
                        },
                    )
                    raise OwlTimeoutError(
                        self._manifest.name,
                        self._manifest.timeout_seconds,
                    ) from None
                except StopAsyncIteration:
                    # Normal end-of-stream — not a timeout.
                    return

                # 3b. STEP — token budget check (approximate via whitespace split)
                chunk_tokens = len(text.split())
                if token_count + chunk_tokens >= self._manifest.max_tokens:
                    log.engine.warning(
                        "[guard] stream: token limit reached — truncating",
                        extra={
                            "_fields": {
                                "owl": self._manifest.name,
                                "max_tokens": self._manifest.max_tokens,
                                "approx_tokens": token_count + chunk_tokens,
                            }
                        },
                    )
                    yield text
                    token_count += chunk_tokens
                    return

                token_count += chunk_tokens
                yield text
        finally:
            # 4. EXIT — always release the slot, always log result
            await self._slots.release()
            duration_ms = (time.monotonic() - t0) * 1000
            log.engine.debug(
                "[guard] stream: exit",
                extra={
                    "_fields": {
                        "owl": self._manifest.name,
                        "approx_tokens": token_count,
                        "duration_ms": round(duration_ms, 2),
                        "is_degraded": self.is_degraded,
                    }
                },
            )


__all__ = ["OwlResourceGuard"]
