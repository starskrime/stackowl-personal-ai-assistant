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
evidence, and the graceful
max-out ("Phase F") still guarantees an answer rather than a dead loop. A refund
can never raise ``used`` above the cap or below zero.

THIS PARAGRAPH USED TO SAY "the 600s ``DEFAULT_TURN_MAX_TIME_S`` still applies".
IT DOES NOT, AND HAS NOT. Measured 2026-09-05: the constant is defined
(``authz/bounds.py:70``), a green test asserts it exists, three comments describe
it — and it is ASSIGNED to nothing in ``src/``. ``BudgetGovernor`` enforces
``caps.max_time_s`` faithfully; no code path ever supplies a value, and all live
owls carry ``max_time_s: null``. Of 205 traces with 20+ model calls since
2026-08-01, **86 exceeded 600 seconds of wall clock and 35 exceeded an hour**, the
longest running 13.1 hours over 294 calls. A sentence asserting a bound that does
not run is worse than no sentence: it is why nobody looked. The wire-or-delete
decision is ESC-148 — it is a behaviour change (it would start ending long turns),
so it is the operator's, not a diagnosis being deferred.
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
        # A refund gives an iteration back, so a path that refunds EVERY round
        # would never advance `_used` and the loop would never end. That is not
        # hypothetical: it hung test_enforce_exit_safety, whose steer stub folds a
        # steer at the give-up boundary on every round — the exact shape of a
        # model stuck being corrected forever. Refunds are therefore themselves
        # bounded, so the worst case is 2x max_total rounds and the loop always
        # terminates.
        self._max_refunds = self.max_total
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
            if self._refunded >= self._max_refunds:
                # The turn has now been corrected as many times as it had
                # iterations. It is not converging, and continuing to refund
                # would keep it alive forever. Let this round be CHARGED so the
                # budget drains and the graceful max-out produces an answer.
                log.engine.warning(
                    "[iteration_budget] refund cap reached — charging this round "
                    "so the loop can terminate",
                    extra={"_fields": {
                        "reason": reason, "refunded_total": self._refunded,
                        "max_refunds": self._max_refunds,
                    }},
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
