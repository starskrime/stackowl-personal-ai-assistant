"""RateLimiter — token-bucket back-pressure for provider calls."""

from __future__ import annotations

import asyncio

from stackowl.exceptions import RateLimitError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log

_SLOW_WAIT_THRESHOLD_SECONDS = 10.0
#: FX-01 — a 429 means the server wants us slower NOW, not that it's dead; a
#: temporary refill-rate cut paces future calls without waiting for the
#: window-based circuit breaker to notice a pattern of them.
_DEFAULT_PENALTY_FACTOR = 0.75
_DEFAULT_PENALTY_SECONDS = 60.0


class RateLimiter:
    """Token-bucket limiter for outbound provider calls.

    If `capacity` is None (rate_limit_rpm not set), this becomes a no-op
    pass-through (acquire() returns immediately). Otherwise tokens regenerate
    at `refill_rate` tokens/second up to `capacity`.
    """

    def __init__(
        self,
        provider_name: str,
        capacity: int | None = None,
        refill_rate: float | None = None,
        *,
        clock: Clock = WallClock(),
    ) -> None:
        log.engine.debug(
            "[rate_limiter] init: entry",
            extra={
                "_fields": {
                    "provider": provider_name,
                    "capacity": capacity,
                    "refill_rate": refill_rate,
                }
            },
        )
        self._provider_name = provider_name
        self._capacity: int | None = capacity
        self._refill_rate: float = float(refill_rate) if refill_rate is not None else 0.0
        self._clock: Clock = clock
        self._tokens: float = float(capacity) if capacity is not None else 0.0
        self._last_refill: float = clock.monotonic()
        # FX-01 — temporary refill-rate penalty window (see penalize()).
        self._penalty_factor: float = 1.0
        self._penalty_until: float = 0.0
        # F118 — per-instance lock guards refill+check+deduct as ONE critical
        # section so two concurrent acquirers cannot both pass the ``>=`` check on
        # the same tokens and over-draw the bucket. The sleep happens OUTSIDE the
        # lock (re-loop + re-check after), so a slow back-pressure wait never
        # serializes the whole process. No fairness promise (documented).
        self._lock = asyncio.Lock()
        log.engine.debug(
            "[rate_limiter] init: exit",
            extra={
                "_fields": {
                    "provider": provider_name,
                    "is_noop": capacity is None,
                    "initial_tokens": self._tokens,
                }
            },
        )

    @classmethod
    def from_rpm(
        cls,
        provider_name: str,
        rate_limit_rpm: int | None,
        *,
        clock: Clock = WallClock(),
    ) -> RateLimiter:
        """Construct from RPM config. Returns no-op limiter if rpm is None."""
        log.engine.debug(
            "[rate_limiter] from_rpm: entry",
            extra={"_fields": {"provider": provider_name, "rpm": rate_limit_rpm}},
        )
        if rate_limit_rpm is None or rate_limit_rpm <= 0:
            log.engine.debug(
                "[rate_limiter] from_rpm: decision — no-op (rpm not set)",
                extra={"_fields": {"provider": provider_name}},
            )
            return cls(provider_name, capacity=None, refill_rate=None, clock=clock)
        refill_rate = rate_limit_rpm / 60.0
        log.engine.debug(
            "[rate_limiter] from_rpm: exit — limiter created",
            extra={
                "_fields": {
                    "provider": provider_name,
                    "capacity": rate_limit_rpm,
                    "refill_per_sec": refill_rate,
                }
            },
        )
        return cls(provider_name, capacity=rate_limit_rpm, refill_rate=refill_rate, clock=clock)

    @property
    def is_noop(self) -> bool:
        return self._capacity is None

    def penalize(
        self, *, factor: float = _DEFAULT_PENALTY_FACTOR, duration_seconds: float = _DEFAULT_PENALTY_SECONDS,
    ) -> None:
        """Shrink the effective refill rate for ``duration_seconds`` (FX-01).

        Called on a classified RATE_LIMIT (429) failure: the server just told us
        to slow down, which is a pacing problem, not an outage — this eases off
        without touching the circuit breaker's outage-detection threshold. A
        no-op on a no-op (uncapped) limiter. Sync and cheap; safe to call from
        an exception handler.
        """
        if self._capacity is None:
            return
        # Floor the factor so a penalty can never zero out the effective rate
        # (that's the fail-closed job of the base refill_rate check in acquire(),
        # not a temporary pacing penalty) and cause a division by zero there.
        self._penalty_factor = max(0.05, factor)
        self._penalty_until = self._clock.monotonic() + duration_seconds
        log.engine.warning(
            "[rate_limiter] penalize: entry — shrinking refill rate",
            extra={
                "_fields": {
                    "provider": self._provider_name,
                    "factor": factor,
                    "duration_seconds": duration_seconds,
                }
            },
        )

    def _effective_refill_rate(self) -> float:
        if self._clock.monotonic() < self._penalty_until:
            return self._refill_rate * self._penalty_factor
        return self._refill_rate

    async def acquire(self, tokens: int = 1) -> None:
        """Wait until `tokens` are available, then deduct. Logs WARNING on long waits."""
        if self._capacity is None:
            log.engine.debug(
                "[rate_limiter] acquire: exit — no-op pass-through",
                extra={"_fields": {"provider": self._provider_name}},
            )
            return

        log.engine.debug(
            "[rate_limiter] acquire: entry",
            extra={
                "_fields": {
                    "provider": self._provider_name,
                    "requested_tokens": tokens,
                    "current_tokens": self._tokens,
                }
            },
        )

        wait_started = self._clock.monotonic()
        total_waited = 0.0

        while True:
            # Critical section: refill + check + deduct atomically under the lock so
            # no two coroutines both pass the ``>=`` check on the same tokens (F118).
            # The sleep is computed here but performed OUTSIDE the lock below.
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    if total_waited >= _SLOW_WAIT_THRESHOLD_SECONDS:
                        log.engine.warning(
                            "[rate_limiter] acquire: slow back-pressure (>%.0fs wait)",
                            _SLOW_WAIT_THRESHOLD_SECONDS,
                            extra={
                                "_fields": {
                                    "provider": self._provider_name,
                                    "waited_seconds": total_waited,
                                }
                            },
                        )
                    log.engine.debug(
                        "[rate_limiter] acquire: exit — tokens granted",
                        extra={
                            "_fields": {
                                "provider": self._provider_name,
                                "remaining_tokens": self._tokens,
                                "waited_seconds": total_waited,
                            }
                        },
                    )
                    return

                if self._refill_rate <= 0.0:
                    # FAIL CLOSED (F124): a zero-refill bucket can never recover the
                    # deficit, so the call cannot be granted. Raising a typed error
                    # (instead of returning, which silently GRANTED the call past
                    # the cap) makes the cap real. The exception carries no secret.
                    log.engine.error(
                        "[rate_limiter] acquire: refill_rate is zero — refusing (fail closed)",
                        extra={
                            "_fields": {
                                "provider": self._provider_name,
                                "requested_tokens": tokens,
                                "capacity": self._capacity,
                            }
                        },
                    )
                    raise RateLimitError(self._provider_name, tokens, self._capacity)

                deficit = tokens - self._tokens
                sleep_seconds = max(deficit / self._effective_refill_rate(), 0.01)
            log.engine.debug(
                "[rate_limiter] acquire: step — sleeping for tokens",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "deficit": deficit,
                        "sleep_seconds": sleep_seconds,
                    }
                },
            )
            # OUTSIDE the lock — never hold it across an await sleep (would
            # serialize the whole process and risk deadlock). Re-loop & re-check.
            await self._clock.async_sleep(sleep_seconds)
            total_waited = self._clock.monotonic() - wait_started

    def _refill(self) -> None:
        if self._capacity is None:
            return
        now = self._clock.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0.0:
            return
        added = elapsed * self._effective_refill_rate()
        if added > 0.0:
            self._tokens = min(float(self._capacity), self._tokens + added)
        self._last_refill = now
