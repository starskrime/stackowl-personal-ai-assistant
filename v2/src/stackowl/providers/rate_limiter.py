"""RateLimiter — token-bucket back-pressure for provider calls."""

from __future__ import annotations

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log

_SLOW_WAIT_THRESHOLD_SECONDS = 10.0


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
                log.engine.error(
                    "[rate_limiter] acquire: refill_rate is zero — cannot grant tokens",
                    extra={
                        "_fields": {
                            "provider": self._provider_name,
                            "requested_tokens": tokens,
                            "capacity": self._capacity,
                        }
                    },
                )
                return

            deficit = tokens - self._tokens
            sleep_seconds = max(deficit / self._refill_rate, 0.01)
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
            await self._clock.async_sleep(sleep_seconds)
            total_waited = self._clock.monotonic() - wait_started

    def _refill(self) -> None:
        if self._capacity is None:
            return
        now = self._clock.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0.0:
            return
        added = elapsed * self._refill_rate
        if added > 0.0:
            self._tokens = min(float(self._capacity), self._tokens + added)
        self._last_refill = now
