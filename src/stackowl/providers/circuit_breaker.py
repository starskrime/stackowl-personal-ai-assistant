"""CircuitBreaker — per-provider failure state machine (ARCH-72)."""

from __future__ import annotations

import asyncio
import enum
from collections import deque
from collections.abc import Awaitable
from typing import TypeVar

from stackowl.exceptions import CircuitOpenError
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log

T = TypeVar("T")

# FX-02 — a stuck-OPEN provider was probed on a fixed 30s metronome forever,
# hammering a still-dead endpoint at the same rate whether it's been down 30s
# or 30 minutes. Doubling the window on each failed HALF_OPEN probe (reset to
# base on the next success) spaces probes out for a genuinely prolonged outage
# without touching the CLOSED->OPEN threshold logic below.
_HALF_OPEN_BACKOFF_CAP_SECONDS = 900.0


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
        # Mutable current backoff window (FX-02) — starts at the base and grows
        # on repeated failed probes; _half_open_seconds itself stays the base/floor.
        self._current_half_open_seconds = self._half_open_seconds
        self._clock: Clock = clock
        self._state: CircuitState = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        # F116 — per-instance lock (NEVER a default-arg lock; that would serialize
        # ALL breakers globally). Guards the mutators (record/admit_probe) only.
        # ``state`` stays a sync property (the cascade reads it cheaply for many
        # providers per selection) and tolerates benign staleness — DO NOT make it
        # async, that ripples into every cascade read and breaks happy-path identity.
        self._lock = asyncio.Lock()
        # HALF_OPEN single-probe gate: at most one in-flight probe at a time. The
        # caller (resilient_round) MUST release it in a ``finally`` (even on
        # CancelledError) so a failed/cancelled probe never wedges the breaker OPEN.
        self._probe_in_flight: bool = False
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
        remaining = self._current_half_open_seconds - elapsed
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

        log.engine.debug(
            "[circuit] call: decision — execute coroutine",
            extra={"_fields": {"provider": self._provider_name, "state": self._state.value}},
        )
        try:
            result = await coro
        except Exception as exc:
            # Delegate to the ONE locked recording impl (SP-1) so call() and the
            # per-round resilient_round site share a single write path.
            await self.record(ok=False)
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
            await self.record(ok=True)
            log.engine.debug(
                "[circuit] call: exit — success",
                extra={"_fields": {"provider": self._provider_name, "state": self._state.value}},
            )
            return result

    async def record(self, *, ok: bool) -> None:
        """The ONE public write seam: record one round's outcome (SP-1).

        Under the per-instance lock: snapshot ``prior_state`` (promoting OPEN →
        HALF_OPEN if the window elapsed) then delegate to the UNCHANGED
        ``_record_success(prior_state)`` / ``_record_failure()`` bodies. Always
        clears ``_probe_in_flight`` (both ok and not-ok) so a completed probe
        frees the half-open gate; the SP-2 caller ALSO clears it in a ``finally``
        for the cancelled/raised-before-record path (defense in depth).
        """
        async with self._lock:
            self._maybe_promote_to_half_open()
            prior_state = self._state
            if ok:
                self._record_success(prior_state)
            else:
                self._record_failure()
            self._probe_in_flight = False

    async def admit_probe(self) -> bool:
        """HALF_OPEN single-probe admission (SP-2 caller uses this).

        Under the lock: if state is HALF_OPEN and no probe is in flight, claim it
        (set the flag, return True — this caller IS the probe). If HALF_OPEN with a
        probe already in flight, return False (the caller must treat it as OPEN and
        NOT call upstream). For CLOSED/OPEN returns False — those states are gated
        by the cheap ``state`` read in resilient_round, not by probe admission.
        """
        async with self._lock:
            self._maybe_promote_to_half_open()
            if self._state is CircuitState.HALF_OPEN and not self._probe_in_flight:
                self._probe_in_flight = True
                log.engine.debug(
                    "[circuit] admit_probe: this caller is the half-open probe",
                    extra={"_fields": {"provider": self._provider_name}},
                )
                return True
            return False

    def clear_probe(self) -> None:
        """Release the in-flight probe flag WITHOUT recording an outcome.

        Used by the SP-2 caller's ``finally`` so a probe that was admitted but
        whose round raised/was-cancelled BEFORE ``record`` ran still frees the
        half-open gate (the "self-healing that can't heal" wedge guard). Idempotent
        with ``record`` (which also clears it). Sync + cheap — never blocks a
        cancellation/finally path on the lock.
        """
        self._probe_in_flight = False

    def _maybe_promote_to_half_open(self) -> None:
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return
        elapsed = self._clock.monotonic() - self._opened_at
        if elapsed >= self._current_half_open_seconds:
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
            # FX-02 — a failed probe means the outage is still live; double the
            # next cooldown (capped) instead of re-probing on the same fixed
            # cadence forever.
            self._current_half_open_seconds = min(
                self._current_half_open_seconds * 2, _HALF_OPEN_BACKOFF_CAP_SECONDS,
            )
            log.engine.warning(
                "[circuit] state transition HALF_OPEN -> OPEN (probe failed)",
                extra={
                    "_fields": {
                        "provider": self._provider_name,
                        "next_half_open_seconds": self._current_half_open_seconds,
                    }
                },
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
            # FX-02 — a healthy probe means the outage is over; reset the
            # backoff window to base so the NEXT open (a fresh, unrelated
            # incident) doesn't inherit this one's escalated cooldown.
            self._current_half_open_seconds = self._half_open_seconds
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
