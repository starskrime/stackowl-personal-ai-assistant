"""BudgetGovernor — per-run consumption ceiling for cost/steps/time (E2-S4).

A deterministic ceiling checked once per ReAct iteration. Steps + time are exact;
cost is BEST-EFFORT (depends on provider pricing; 0 on local/unpriced models;
per run-attempt — the in-memory cost ledger resets on resume). A missing/zero
cost signal NEVER disables steps/time. All-None caps → a no-op governor.

Mutable in-memory limits support the interactive raise (raise_caps); the raise is
scoped to this drive and never persisted (durable raise is E2-S5).
"""

from __future__ import annotations

from collections.abc import Callable
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
        prior_cost_usd: float = 0.0,
        human_wait_source: Callable[[], float] | None = None,
    ) -> None:
        self._max_steps = caps.max_steps
        self._max_time_s = caps.max_time_s
        self._max_cost_usd = caps.max_cost_usd
        self._cost = cost_tracker
        self._trace_id = trace_id
        self._t0 = started_monotonic
        self._clock = clock
        # Time the turn spent BLOCKED waiting for a human answer (clarify) must not
        # count against the compute-time cap. None → no subtraction (byte-identical
        # legacy behavior). The source returns cumulative human-wait seconds so far.
        self._human_wait_source = human_wait_source
        # F093 — spend already accumulated by PRIOR durable attempts of this task
        # (the in-memory cost ledger resets to 0 on resume). Seeding it makes the
        # cost ceiling CUMULATIVE across park/resume rather than per-attempt.
        # 0.0 for an ephemeral turn or a first attempt → legacy behavior unchanged.
        self._prior_cost_usd = max(0.0, prior_cost_usd)

    def check(self, iteration: int, *, tool_calls: int | None = None) -> BudgetBreach | None:
        """Return a BudgetBreach for the FIRST set cap exceeded after this iteration.

        `iteration` is the just-completed 0-based ReAct index — round count =
        iteration + 1. `tool_calls`, when given, is the cumulative number of
        individual tool dispatches so far this turn; the step cap counts the MAX of
        rounds and tool calls. A provider can emit several tool_use blocks per
        round, so counting only rounds let a tool-spamming turn slip the step cap
        and die on the wall-clock instead — counting dispatches contains it.
        Order: steps, then time, then cost (cost last — weakest signal).
        """
        steps_done = iteration + 1 if tool_calls is None else max(iteration + 1, tool_calls)
        if self._max_steps is not None and steps_done >= self._max_steps:
            return BudgetBreach("steps", float(self._max_steps), float(steps_done))
        if self._max_time_s is not None:
            elapsed = self._compute_elapsed()
            if elapsed >= self._max_time_s:
                return BudgetBreach("time", self._max_time_s, elapsed)
        if self._max_cost_usd is not None and self._cost is not None:
            # Cumulative spend = prior durable attempts + this attempt's running
            # total (F093). For a non-durable turn _prior_cost_usd is 0.0.
            spent = self._prior_cost_usd + self._cost.turn_cost_usd(self._trace_id)
            if spent >= self._max_cost_usd:
                return BudgetBreach("cost", self._max_cost_usd, spent)
        return None

    def current_cost_usd(self) -> float:
        """Cumulative spend so far = prior durable attempts + this attempt's total.

        F093 — the durable executor persists this on the task row each iteration so
        the NEXT resume seeds its governor with it and the cost ceiling holds across
        the whole task. Returns the prior seed alone when no cost tracker is wired.
        """
        attempt = self._cost.turn_cost_usd(self._trace_id) if self._cost is not None else 0.0
        return self._prior_cost_usd + attempt

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
        return max(0.0, self._max_time_s - self._compute_elapsed())

    def _compute_elapsed(self) -> float:
        """Wall-clock since start MINUS time blocked waiting for a human answer.

        The human-wait subtraction makes the time cap measure COMPUTE time, not a
        slow human. None source → no subtraction (legacy). Floors at 0.0 so a
        clock skew or over-counted wait can never produce negative elapsed.
        """
        elapsed = self._clock.monotonic() - self._t0
        if self._human_wait_source is not None:
            elapsed -= max(0.0, self._human_wait_source())
        return max(0.0, elapsed)

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
