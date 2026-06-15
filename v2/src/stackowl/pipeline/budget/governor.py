"""BudgetGovernor — per-run consumption ceiling for cost/steps/time (E2-S4).

A deterministic ceiling checked once per ReAct iteration. Steps + time are exact;
cost is BEST-EFFORT (depends on provider pricing; 0 on local/unpriced models;
per run-attempt — the in-memory cost ledger resets on resume). A missing/zero
cost signal NEVER disables steps/time. All-None caps → a no-op governor.

Mutable in-memory limits support the interactive raise (raise_caps); the raise is
scoped to this drive and never persisted (durable raise is E2-S5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from stackowl.exceptions import BudgetBreach
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.authz.bounds import ResourceCaps


class _Clock(Protocol):
    def monotonic(self) -> float: ...


class _CostSource(Protocol):
    def turn_cost_usd(self, trace_id: str) -> float: ...


class BudgetGovernor:
    """Checks cost/steps/time against the acting owl's effective caps."""

    def __init__(
        self,
        caps: ResourceCaps,
        *,
        cost_tracker: _CostSource | None,
        trace_id: str,
        started_monotonic: float,
        clock: _Clock,
    ) -> None:
        self._max_steps = caps.max_steps
        self._max_time_s = caps.max_time_s
        self._max_cost_usd = caps.max_cost_usd
        self._cost = cost_tracker
        self._trace_id = trace_id
        self._t0 = started_monotonic
        self._clock = clock

    def check(self, iteration: int) -> BudgetBreach | None:
        """Return a BudgetBreach for the FIRST set cap exceeded after this iteration.

        `iteration` is the just-completed 0-based ReAct index — steps_done =
        iteration + 1. Order: steps, then time, then cost (cost last — weakest signal).
        """
        steps_done = iteration + 1
        if self._max_steps is not None and steps_done >= self._max_steps:
            return BudgetBreach("steps", float(self._max_steps), float(steps_done))
        if self._max_time_s is not None:
            elapsed = self._clock.monotonic() - self._t0
            if elapsed >= self._max_time_s:
                return BudgetBreach("time", self._max_time_s, elapsed)
        if self._max_cost_usd is not None and self._cost is not None:
            spent = self._cost.turn_cost_usd(self._trace_id)
            if spent >= self._max_cost_usd:
                return BudgetBreach("cost", self._max_cost_usd, spent)
        return None

    def remaining_seconds(self) -> float | None:
        """Residual wall-clock budget for THIS run, or None when no time cap is set.

        F027/SP-4 — the governor is the single budget owner; the execute step reads
        this value and threads it into the provider's terminal wrap-up as
        ``wrapup_deadline_s`` so a hung wrap-up cannot exceed the promised ceiling.
        Floors at 0.0 (never negative). Reuses the private cap/start/clock fields so
        the provider never reaches into the governor object.
        """
        if self._max_time_s is None:
            return None
        elapsed = self._clock.monotonic() - self._t0
        return max(0.0, self._max_time_s - elapsed)

    def raise_caps(self, cap: str) -> None:
        """In-memory raise of the breached cap (interactive Raise).

        Doubles the limit with a +1 buffer step to ensure the new ceiling is
        strictly above the iteration that triggered the breach (avoids immediate
        re-trip at the same step count). Scoped to this drive; never persisted.
        """
        if cap == "steps" and self._max_steps is not None:
            self._max_steps = self._max_steps * 2 + 1
        elif cap == "time" and self._max_time_s is not None:
            self._max_time_s *= 2
        elif cap == "cost" and self._max_cost_usd is not None:
            self._max_cost_usd *= 2
        log.engine.info("[budget] governor.raise_caps: lifted", extra={"_fields": {"cap": cap}})
