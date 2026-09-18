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
    """

    consecutive_failures: int = 0
    last_remedy: str | None = None
    last_error: str | None = None

    def note_failure(self, remedy: str) -> None:
        self.consecutive_failures += 1
        self.last_remedy = remedy
        self.last_error = remedy

    def note_success(self) -> None:
        self.consecutive_failures = 0
        self.last_remedy = None
        self.last_error = None


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


def _snapshot() -> JournalHealthState:
    with _state_lock:
        return JournalHealthState(
            consecutive_failures=_state.consecutive_failures,
            last_remedy=_state.last_remedy,
            last_error=_state.last_error,
        )


def reset_for_tests() -> None:
    """Clear the tracked state. Test-only -- the state is otherwise process-lifetime."""
    with _state_lock:
        _state.consecutive_failures = 0
        _state.last_remedy = None
        _state.last_error = None


class JournalHealthContributor:
    """Health contributor: ``degraded`` while ``journal.record()`` is failing."""

    @property
    def contributor_name(self) -> str:
        return "journal"

    async def health_check(self) -> HealthStatus:
        snap = _snapshot()
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
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=None,
            latency_ms=0.0,
        )
