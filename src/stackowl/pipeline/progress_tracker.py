"""TurnProgressTracker — the per-turn "is this turn advancing?" model.

Unifies the same-tool circuit breaker (P2) and closes the timeout (G1) and
no-op-refusal (G2) spiral gaps. INDEPENDENT of tool_outcome_ledger: this counter
drives CONTAINMENT (bounce a tool that makes no progress); the ledger's
side_effect_committed semantics drive the HONEST FLOOR. The two never read each
other (see the spec's honesty invariants). Turn-scoped; one per _run_with_tools.
"""

from __future__ import annotations

from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD

# Consecutive zero-progress dispatches of the SAME tool before it is bounced for
# the rest of the turn. Host-agnostic fixed default; scaled by window below.
NO_PROGRESS_THRESHOLD = 3


def resolve_no_progress_threshold(model_window: int | None) -> int:
    """A weak/lean-window model spirals faster and reasons worse about failure —
    contain it sooner. Capability-probed (reads the resolved window), never pinned
    to a host. A normal/strong or unknown window keeps the default."""
    if model_window is not None and model_window <= LEAN_WINDOW_THRESHOLD:
        return 2
    return NO_PROGRESS_THRESHOLD


class TurnProgressTracker:
    def __init__(self, threshold: int = NO_PROGRESS_THRESHOLD) -> None:
        self._threshold = threshold
        self._streak: dict[str, int] = {}
        self._open: list[str] = []
        self._made_progress = False

    def reset(self) -> None:
        """Clear streaks + open breakers on tier escalation so a stronger tier is
        not pre-bounced by the weak tier's open breaker. Leaves ``_made_progress``
        untouched — whether the turn ever advanced is a turn-level fact, not a
        per-tier one."""
        self._streak.clear()
        self._open.clear()

    def record_progress(self, name: str) -> None:
        self._streak[name] = 0
        self._made_progress = True

    def record_no_progress(self, name: str) -> bool:
        self._streak[name] = self._streak.get(name, 0) + 1
        if self._streak[name] >= self._threshold and name not in self._open:
            self._open.append(name)
            return True
        return False

    def is_open(self, name: str) -> bool:
        return name in self._open

    @property
    def made_progress(self) -> bool:
        return self._made_progress

    @property
    def opened_tools(self) -> tuple[str, ...]:
        return tuple(self._open)
