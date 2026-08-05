"""IterationBudget — a consumable ReAct-loop budget with refunds (D02.2).

Replaces ``for _iter_idx in range(resolved_iterations)`` in both provider loops.
The bare range was already a correct BOUND; what it could not express is that not
every pass through the loop is work.

THE MEASURED REASON THIS EXISTS. Three sites `continue` without the model having
made progress — they are CORRECTIONS applied to the model, not steps toward the
answer:

* a user steer folded in at the give-up boundary,
* the leak guard rejecting a "final answer" that was really an unparsed tool call,
* the persistence judge injecting a give-up directive.

Measured over four days of production logs: 10-16 leak/loop-guard events and
18-42 persistence-judge directives PER DAY. Against a 20-iteration interactive
budget, a turn corrected three times has silently lost 15% of the budget it needs
to finish — and the correction exists precisely because that turn was already
struggling. Refunding those rounds gives the budget back to the work.

WHAT THIS DELIBERATELY DOES NOT CHANGE. The ceiling itself is untouched:
``DEFAULT_TURN_MAX_STEPS`` (20 interactive) and
``DEFAULT_SCHEDULED_TURN_MAX_STEPS`` (45 background) are sized from live incident
evidence, the 600s ``DEFAULT_TURN_MAX_TIME_S`` still applies, and the graceful
max-out ("Phase F") still guarantees an answer rather than a dead loop. A refund
can never raise ``used`` above the cap or below zero.
"""

from __future__ import annotations

import threading

from stackowl.infra.observability import log

__all__ = ["IterationBudget"]


class IterationBudget:
    """A bounded, refundable iteration counter for one ReAct loop.

    Thread-safe because a provider loop can be driven from more than one task
    (escalation tiers rebuild and re-enter), and an off-by-one here is either a
    truncated turn or an unbounded one.
    """

    def __init__(self, max_total: int) -> None:
        self.max_total = max(int(max_total), 0)
        self._used = 0
        self._refunded = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Take one iteration. False when the budget is spent (stop looping)."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self, reason: str) -> None:
        """Give one iteration back — the round did not advance the answer.

        ``reason`` is required rather than optional: an unexplained refund is
        indistinguishable from a budget bug, and this is the one method that can
        make a bounded loop run longer than its nominal cap suggests.

        Clamped at zero. A refund without a matching consume would otherwise let
        the loop exceed ``max_total``, which is the one thing the budget exists
        to prevent.
        """
        with self._lock:
            if self._used <= 0:
                log.engine.warning(
                    "[iteration_budget] refund with nothing consumed — ignored",
                    extra={"_fields": {"reason": reason}},
                )
                return
            self._used -= 1
            self._refunded += 1
            log.engine.debug(
                "[iteration_budget] refunded a corrective round",
                extra={"_fields": {
                    "reason": reason, "used": self._used,
                    "refunded_total": self._refunded, "max_total": self.max_total,
                }},
            )

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def refunded(self) -> int:
        """Corrective rounds given back — the number worth watching in the logs."""
        with self._lock:
            return self._refunded

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(self.max_total - self._used, 0)
