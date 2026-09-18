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

    Story 2.11 (AD-6) adds a THIRD, independent signal: the journal's WAL
    file staying over its size budget across consecutive prune passes. It
    tracks a STREAK, like the failure count above (not a lifetime count like
    the budget-exceeded count) -- "stays over budget" is about the CURRENT
    state, and a pass back under budget should clear the warning, unlike a
    per-turn overflow which stays a permanent count of how many traces ever
    misbehaved.

    Story 2.11 also adds a FOURTH, independent signal: ``journal_prune``
    itself failing (the delete/checkpoint work raising). This is NOT the
    same as a ``record()`` failure -- conflating the two via the shared
    ``note_failure``/``consecutive_failures`` would show an operator
    "journal.record has failed N times" for what is actually a prune
    failure, and a later unrelated successful ``record()`` call would
    silently clear the still-broken prune's signal via ``note_success()``.
    Tracked as its own consecutive-failure streak (mirrors
    ``consecutive_failures`` exactly: a later successful prune pass clears
    it via :meth:`note_prune_success`).
    """

    consecutive_failures: int = 0
    last_remedy: str | None = None
    last_error: str | None = None
    #: How many DISTINCT traces have crossed the write budget this process
    #: lifetime -- a count, not a streak, since a budget overflow says
    #: nothing about the NEXT turn the way a `record()` failure does.
    budget_exceeded_count: int = 0
    last_budget_trace_id: str | None = None
    #: Consecutive ``journal_prune`` passes in a row where the WAL file
    #: measured over ``wal_size_budget_bytes``. Resets to 0 the moment a pass
    #: measures back under budget (see :meth:`note_wal_size`).
    wal_over_budget_streak: int = 0
    last_wal_bytes: int | None = None
    last_wal_budget_bytes: int | None = None
    #: Consecutive ``journal_prune.execute()`` failures -- its OWN streak,
    #: never the shared ``consecutive_failures`` above (see class docstring).
    prune_consecutive_failures: int = 0
    last_prune_remedy: str | None = None
    last_prune_error: str | None = None

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

    def note_wal_size(self, bytes_: int, budget_bytes: int) -> None:
        self.last_wal_bytes = bytes_
        self.last_wal_budget_bytes = budget_bytes
        if bytes_ > budget_bytes:
            self.wal_over_budget_streak += 1
        else:
            self.wal_over_budget_streak = 0

    def note_prune_failure(self, remedy: str) -> None:
        self.prune_consecutive_failures += 1
        self.last_prune_remedy = remedy
        self.last_prune_error = remedy

    def note_prune_success(self) -> None:
        self.prune_consecutive_failures = 0
        self.last_prune_remedy = None
        self.last_prune_error = None


_state = JournalHealthState()
_state_lock = threading.Lock()

#: Consecutive over-budget ``journal_prune`` passes before the health
#: contributor degrades. 3, not 1 -- a single pass over budget can be one
#: unusually bursty hour; three in a row (the job runs hourly, see
#: ``scheduler/assembly.py``) means the WAL is not coming back down on its
#: own and is worth a human's attention.
_WAL_STREAK_THRESHOLD = 3


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


def note_wal_size(bytes_: int, budget_bytes: int) -> None:
    """Record one ``journal_prune`` pass's measured WAL file size (AD-6).

    Called only from ``scheduler/handlers/journal_prune.py``, once per pass
    (even when zero rows were pruned, so the WAL is still measured).
    Independent of both signals above -- a WAL over budget means every event
    is still present, just that the file has not been checkpointed down,
    which is its own remedy (a checkpoint or a lower budget), not a
    ``record()`` failure or a per-turn overflow.
    """
    with _state_lock:
        _state.note_wal_size(bytes_, budget_bytes)


def note_prune_failure(remedy: str) -> None:
    """Record a ``journal_prune.execute()`` failure. Called ONLY from
    ``scheduler/handlers/journal_prune.py`` -- deliberately NOT
    :func:`note_failure`, which is reserved for ``recorder.py``'s
    ``journal.record()`` failures (see that function's own docstring and
    this module's class docstring for why conflating the two is wrong)."""
    with _state_lock:
        _state.note_prune_failure(remedy)


def note_prune_success() -> None:
    """Record a ``journal_prune.execute()`` success, resetting its own
    failure streak. Called only from ``scheduler/handlers/journal_prune.py``."""
    with _state_lock:
        _state.note_prune_success()


def _snapshot() -> JournalHealthState:
    with _state_lock:
        return JournalHealthState(
            consecutive_failures=_state.consecutive_failures,
            last_remedy=_state.last_remedy,
            last_error=_state.last_error,
            budget_exceeded_count=_state.budget_exceeded_count,
            last_budget_trace_id=_state.last_budget_trace_id,
            wal_over_budget_streak=_state.wal_over_budget_streak,
            last_wal_bytes=_state.last_wal_bytes,
            last_wal_budget_bytes=_state.last_wal_budget_bytes,
            prune_consecutive_failures=_state.prune_consecutive_failures,
            last_prune_remedy=_state.last_prune_remedy,
            last_prune_error=_state.last_prune_error,
        )


def reset_for_tests() -> None:
    """Clear the tracked state. Test-only -- the state is otherwise process-lifetime."""
    with _state_lock:
        _state.consecutive_failures = 0
        _state.last_remedy = None
        _state.last_error = None
        _state.budget_exceeded_count = 0
        _state.last_budget_trace_id = None
        _state.wal_over_budget_streak = 0
        _state.last_wal_bytes = None
        _state.last_wal_budget_bytes = None
        _state.prune_consecutive_failures = 0
        _state.last_prune_remedy = None
        _state.last_prune_error = None


class JournalHealthContributor:
    """Health contributor: ``degraded`` while ``journal.record()`` is failing."""

    @property
    def contributor_name(self) -> str:
        return "journal"

    async def health_check(self) -> HealthStatus:
        snap = _snapshot()
        # PRIORITY ORDER, and why: consecutive_failures first -- a
        # `record()` failure means events may be MISSING, the most serious
        # thing this contributor can report. Next, journal_prune's own
        # failure streak: journal_events grows unbounded while it fails,
        # also a real and worsening problem. Next, the WAL-over-budget
        # streak: self-correcting (a pass back under budget clears it), so
        # it should not be permanently hidden behind a stale signal.
        # LAST, deliberately: budget_exceeded_count is a LIFETIME count that
        # NEVER resets (DW-21) -- once any trace ever crosses it, it would
        # otherwise mask every later, self-correcting signal (the WAL
        # streak included) forever. Checked last so a fresher, currently-true
        # problem is never hidden behind a permanently-stuck one.
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
        if snap.prune_consecutive_failures > 0:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(
                    f"journal_prune has failed {snap.prune_consecutive_failures} "
                    f"time(s) in a row: {snap.last_prune_error}"
                ),
                remedy=snap.last_prune_remedy,
                latency_ms=0.0,
            )
        if snap.wal_over_budget_streak >= _WAL_STREAK_THRESHOLD:
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(
                    f"the journal's WAL file has stayed over its "
                    f"{snap.last_wal_budget_bytes} byte budget for "
                    f"{snap.wal_over_budget_streak} consecutive journal_prune "
                    f"pass(es), most recently measured at "
                    f"{snap.last_wal_bytes} bytes"
                ),
                remedy=(
                    "the WAL is not checkpointing down between prune passes -- "
                    "investigate long-running readers holding it open, or "
                    "raise journal.wal_size_budget_bytes (Story 2.12 sets the "
                    "real budget)"
                ),
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
