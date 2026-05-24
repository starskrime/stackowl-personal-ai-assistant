"""OwlResourceGuard — enforces per-owl resource budgets (token, timeout, concurrency).

One instance per owl. The semaphore is held for the lifetime of the provider call
and released in a ``finally`` block. Timeout is enforced by polling elapsed time
between yielded chunks (we cannot wrap an async generator with ``asyncio.wait_for``
directly). Token count is approximated by whitespace-splitting yielded text.

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


def _try_acquire_nowait(sem: asyncio.Semaphore) -> bool:
    """Acquire a semaphore slot without awaiting; return False when none available.

    ``asyncio.Semaphore`` does not expose a public non-blocking acquire and
    ``asyncio.wait_for(sem.acquire(), timeout=0.0)`` always times out before the
    coroutine runs. Inspecting the internal counter is the only reliable
    single-thread / single-loop pattern.
    """
    value: int = sem._value  # noqa: SLF001 — documented private invariant
    if value <= 0:
        return False
    # ``Semaphore.acquire`` returns immediately when ``_value > 0`` and no
    # waiters are queued — call it and discard the awaitable's already-resolved
    # result by stepping the coroutine once.
    coro = sem.acquire()
    try:
        coro.send(None)
    except StopIteration:
        return True
    # If the acquire actually suspended, cancel it and report failure so we
    # never leave a dangling waiter on the semaphore.
    coro.close()
    return False


class OwlResourceGuard:
    """Wrap a ModelProvider call with timeout, token-limit, and concurrency guards.

    One instance per owl. Reusable across many ``stream()`` calls. Tracks a
    rolling window of timeout violations for downstream health reporting.
    """

    def __init__(self, manifest: OwlAgentManifest) -> None:
        self._manifest = manifest
        self._semaphore = asyncio.Semaphore(manifest.max_concurrent_requests)
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
        2. Timeout — elapsed-time check between chunks (raises OwlTimeoutError).
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

        # 2. DECISION — non-blocking semaphore acquire
        if not _try_acquire_nowait(self._semaphore):
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
        try:
            async for text in provider.stream(messages, model, **kwargs):
                # 3a. STEP — timeout check
                elapsed = time.monotonic() - t0
                if elapsed > self._manifest.timeout_seconds:
                    self.record_timeout()
                    log.engine.warning(
                        "[guard] stream: timeout exceeded",
                        extra={
                            "_fields": {
                                "owl": self._manifest.name,
                                "elapsed_s": round(elapsed, 3),
                                "limit_s": self._manifest.timeout_seconds,
                            }
                        },
                    )
                    raise OwlTimeoutError(
                        self._manifest.name,
                        self._manifest.timeout_seconds,
                    )

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
            # 4. EXIT — always release semaphore, always log result
            self._semaphore.release()
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
