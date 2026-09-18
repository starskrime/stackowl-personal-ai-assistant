"""``JournalHealthContributor`` -- surfaces a ``journal.record()`` failure in
the health sweep. Mirrors ``McpHealthContributor`` (``health/contributors.py``).

State is process-global, module-level (like ``recorder.py``'s registry
singleton): ``record()`` runs on the hot write path of every durable-task
transition and must not carry a constructed contributor instance around just
to report a failure count. ``note_failure``/``note_success`` are the only
writers; ``JournalHealthContributor.health_check()`` is the only reader.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from stackowl.health.status import HealthStatus


@dataclass
class JournalHealthState:
    """Tracks CONSECUTIVE ``record()`` failures and the most recent remedy.

    A success resets the streak -- this answers "is the journal failing RIGHT
    NOW", not "has it ever failed", matching D14.4's remedy contract: a
    ``remedy`` that is always populated is a field readers learn to skim.

    Story 2.7 (AD-38) adds a SECOND, independent degrade signal alongside the
    consecutive-failure streak: a per-turn write-budget overflow. The two
    never interact -- a budget overflow is not a `record()` failure (the
    event still gets recorded), and `note_success()` must not silently clear
    it, or a healthy `record()` right after a runaway turn would mask the
    warning ``JournalHealthContributor.health_check()`` is supposed to keep
    surfacing until a human clears it via `reset_for_tests()`/a fresh boot.
    """

    consecutive_failures: int = 0
    last_remedy: str | None = None
    last_error: str | None = None
    #: How many DISTINCT traces have crossed the write budget this process
    #: lifetime -- a count, not a streak, since a budget overflow says
    #: nothing about the NEXT turn the way a `record()` failure does.
    budget_exceeded_count: int = 0
    last_budget_trace_id: str | None = None

    def note_failure(self, remedy: str) -> None:
        self.consecutive_failures += 1
        self.last_remedy = remedy
        self.last_error = remedy

    def note_success(self) -> None:
        self.consecutive_failures = 0
        self.last_remedy = None
        self.last_error = None

    def note_budget_exceeded(self, trace_id: str) -> None:
        self.budget_exceeded_count += 1
        self.last_budget_trace_id = trace_id


_state = JournalHealthState()
_state_lock = threading.Lock()


def note_failure(remedy: str) -> None:
    """Record a ``journal.record()`` failure. Called only from ``recorder.py``."""
    with _state_lock:
        _state.note_failure(remedy)


def note_success() -> None:
    """Record a ``journal.record()`` success, resetting the failure streak."""
    with _state_lock:
        _state.note_success()


def note_budget_exceeded(trace_id: str) -> None:
    """Record that ``trace_id`` crossed the per-turn write budget (AD-38).

    Called only from ``turn_budget.py``. Independent of the consecutive-
    failure streak above -- degrades health without implying ``record()``
    itself is failing.
    """
    with _state_lock:
        _state.note_budget_exceeded(trace_id)


def _snapshot() -> JournalHealthState:
    with _state_lock:
        return JournalHealthState(
            consecutive_failures=_state.consecutive_failures,
            last_remedy=_state.last_remedy,
            last_error=_state.last_error,
            budget_exceeded_count=_state.budget_exceeded_count,
            last_budget_trace_id=_state.last_budget_trace_id,
        )


def reset_for_tests() -> None:
    """Clear the tracked state. Test-only -- the state is otherwise process-lifetime."""
    with _state_lock:
        _state.consecutive_failures = 0
        _state.last_remedy = None
        _state.last_error = None
        _state.budget_exceeded_count = 0
        _state.last_budget_trace_id = None


class JournalHealthContributor:
    """Health contributor: ``degraded`` while ``journal.record()`` is failing."""

    @property
    def contributor_name(self) -> str:
        return "journal"

    async def health_check(self) -> HealthStatus:
        snap = _snapshot()
        # The consecutive-failure streak takes priority when both are set --
        # a `record()` failure means events may be MISSING, a budget
        # overflow means every event is still present (AD-38) with only a
        # WARNING logged. The more serious signal wins the message.
        if snap.consecutive_failures > 0:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(
                    f"journal.record has failed {snap.consecutive_failures} "
                    f"time(s) in a row: {snap.last_error}"
                ),
                remedy=snap.last_remedy,
                latency_ms=0.0,
            )
        if snap.budget_exceeded_count > 0:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(
                    f"the per-turn journal write budget has been exceeded "
                    f"{snap.budget_exceeded_count} time(s) this process "
                    f"lifetime, most recently on trace {snap.last_budget_trace_id!r} "
                    "-- every event is still recorded (AD-38)"
                ),
                remedy=(
                    "investigate the offending trace for a runaway tool/"
                    "delegation loop; Story 2.12 sets the real budget"
                ),
                latency_ms=0.0,
            )
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=None,
            latency_ms=0.0,
        )
