"""CircuitBreaker — per-provider failure state machine (ARCH-72)."""

from __future__ import annotations

import enum
from collections import deque
from collections.abc import Awaitable
from typing import TypeVar

from stackowl.exceptions import CircuitOpenError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log

T = TypeVar("T")


class CircuitState(enum.Enum):
    """Three-state circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider failure state machine.

    CLOSED → OPEN after `failure_threshold` failures within `window_seconds`.
    OPEN → HALF_OPEN after `half_open_seconds` have elapsed since the OPEN
    transition. HALF_OPEN → CLOSED on a single success; HALF_OPEN → OPEN on
    a single failure (resets the half-open clock).
    """

    def __init__(
        self,
        provider_name: str,
        failure_threshold: int = 3,
        window_seconds: int = 60,
        half_open_seconds: int = 30,
        *,
        clock: Clock = WallClock(),
    ) -> None:
        log.engine.debug(
            "[circuit] init: entry",
            extra={
                "_fields": {
                    "provider": provider_name,
                    "failure_threshold": failure_threshold,
                    "window_seconds": window_seconds,
                    "half_open_seconds": half_open_seconds,
                }
            },
        )
        self._provider_name = provider_name
        self._failure_threshold = failure_threshold
        self._window_seconds = float(window_seconds)
        self._half_open_seconds = float(half_open_seconds)
        self._clock: Clock = clock
        self._state: CircuitState = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        log.engine.debug(
            "[circuit] init: exit — initial state CLOSED",
            extra={"_fields": {"provider": provider_name}},
        )

    @property
    def state(self) -> CircuitState:
        """Return state, transitioning OPEN → HALF_OPEN if window elapsed."""
        self._maybe_promote_to_half_open()
        return self._state

    @property
    def retry_after_seconds(self) -> float:
        """Seconds until next retry. 0.0 if not OPEN."""
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return 0.0
        elapsed = self._clock.monotonic() - self._opened_at
        remaining = self._half_open_seconds - elapsed
        return max(0.0, remaining)

    async def call(self, coro: Awaitable[T]) -> T:
        """Execute coro under circuit-breaker protection.

        Raises CircuitOpenError when state is OPEN. Returns the coro result on
        success. Re-raises the underlying exception on failure (after updating
        breaker state).
        """
        log.engine.debug(
            "[circuit] call: entry",
            extra={"_fields": {"provider": self._provider_name, "state": self._state.value}},
        )
        self._maybe_promote_to_half_open()
        if self._state is CircuitState.OPEN:
            retry_after = self.retry_after_seconds
            log.engine.debug(
                "[circuit] call: decision — short-circuit (OPEN)",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "retry_after_seconds": retry_after,
                    }
                },
            )
            raise CircuitOpenError(self._provider_name, retry_after)

        prior_state = self._state
        log.engine.debug(
            "[circuit] call: decision — execute coroutine",
            extra={"_fields": {"provider": self._provider_name, "state": prior_state.value}},
        )
        try:
            result = await coro
        except Exception as exc:
            self._record_failure()
            log.engine.warning(
                "[circuit] call: step — failure recorded",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "state": self._state.value,
                        "error": str(exc),
                    }
                },
            )
            raise
        else:
            self._record_success(prior_state)
            log.engine.debug(
                "[circuit] call: exit — success",
                extra={"_fields": {"provider": self._provider_name, "state": self._state.value}},
            )
            return result

    def _maybe_promote_to_half_open(self) -> None:
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return
        elapsed = self._clock.monotonic() - self._opened_at
        if elapsed >= self._half_open_seconds:
            log.engine.warning(
                "[circuit] state transition OPEN -> HALF_OPEN",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "elapsed_seconds": elapsed,
                    }
                },
            )
            self._state = CircuitState.HALF_OPEN

    def _record_failure(self) -> None:
        now = self._clock.monotonic()
        if self._state is CircuitState.HALF_OPEN:
            log.engine.warning(
                "[circuit] state transition HALF_OPEN -> OPEN (probe failed)",
                extra={"_fields": {"provider": self._provider_name}},
            )
            self._state = CircuitState.OPEN
            self._opened_at = now
            self._failures.clear()
            return

        self._failures.append(now)
        cutoff = now - self._window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        if len(self._failures) >= self._failure_threshold:
            log.engine.warning(
                "[circuit] state transition CLOSED -> OPEN (threshold reached)",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "failures_in_window": len(self._failures),
                        "threshold": self._failure_threshold,
                    }
                },
            )
            self._state = CircuitState.OPEN
            self._opened_at = now
            self._failures.clear()

    def _record_success(self, prior_state: CircuitState) -> None:
        if prior_state is CircuitState.HALF_OPEN:
            log.engine.warning(
                "[circuit] state transition HALF_OPEN -> CLOSED (probe succeeded)",
                extra={"_fields": {"provider": self._provider_name}},
            )
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._failures.clear()
        elif prior_state is CircuitState.CLOSED and self._failures:
            now = self._clock.monotonic()
            cutoff = now - self._window_seconds
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
