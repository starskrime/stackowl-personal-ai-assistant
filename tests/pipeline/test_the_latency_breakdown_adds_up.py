"""A turn's latency breakdown must account for the whole turn.

MEASURED 2026-09-03 over his own chat turns (telegram, machine lanes excluded),
comparing each turn's ``latency_ms`` against the sum of its nine recorded
``step_durations``:

    unaccounted, median          6.8% of the turn
    unaccounted, p90            63.9%
    worst turn                 252,357 ms unaccounted of 359,675 ms

His median turn takes 89 SECONDS and his p90 takes 7.2 minutes, and on the slow
ones two thirds of that wait is outside every measured step. The breakdown says
nine steps summing to a third of the turn and offers nothing for the rest.

THE CAUSE IS TWO CLOCKS THAT WERE NEVER RECONCILED. ``total_ms`` is measured from
``bindings.t0`` to after the ``finally`` (asyncio_backend), while
``step_durations`` only covers what ``_run_steps`` wraps. Everything between them
is invisible by construction: ``bind_turn_context``, the progress-callback setup,
**``run_delivery_gate``** — seven honesty surfacers, one of which hands the turn
to a different owl and runs a whole second pipeline — ``_verify_turn_acceptance``
(an LLM call when enabled), ``unbind_turn_context`` and outcome capture.

AND IT CANNOT BE INSPECTED EITHER. Every line in ``delivery_gate.py`` that would
report this work is ``log.engine.debug``; production runs at INFO. Control-checked
against every log file on the box: the string "giveup_floor" appears ZERO times.
So the time is neither timed nor logged.

WHY A RESIDUAL RATHER THAN JUST MORE TIMERS. Timing today's post-step phases
fixes today's gap; the next phase added outside ``_run_steps`` reopens it silently,
which is exactly how this one arrived. A residual makes the arithmetic CLOSE by
construction: the breakdown always sums to the turn, and anything unnamed shows
up as a number with a name rather than as absence.

Counter-evidence I checked before writing this, because the obvious explanation
was wrong: ``dict(state.step_durations)`` collapses duplicate step names, so a
re-run step could have lost its time. Every measured turn carries exactly NINE
keys, fast and slow alike, so nothing is being collapsed — the time is genuinely
spent outside the steps, not overwritten inside them.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.backends.shared import close_step_accounting


def test_the_residual_makes_the_breakdown_sum_to_the_turn() -> None:
    """THE INVARIANT. 252 seconds of a 360-second turn had no name."""
    steps = [("triage", 1000.0), ("execute", 107318.0)]
    closed = close_step_accounting(steps, total_ms=359675.0)
    assert sum(v for _, v in closed) == pytest.approx(359675.0)
    assert dict(closed)["unaccounted"] == pytest.approx(359675.0 - 108318.0)


def test_a_fully_accounted_turn_gets_no_noise() -> None:
    """Some turns already add up — measured, one had 515ms unaccounted of
    541,983ms. A residual that manufactured a bucket on those would be noise in
    every breakdown forever."""
    steps = [("triage", 500.0), ("execute", 99500.0)]
    closed = close_step_accounting(steps, total_ms=100_000.0)
    assert "unaccounted" not in dict(closed)
    assert closed == steps


def test_the_residual_is_never_negative(caplog: pytest.LogCaptureFixture) -> None:
    """Steps are timed with monotonic clocks inside a span also timed with a
    monotonic clock, so the sum should never exceed the total — but a future
    step timed outside the span would make it so, and a negative duration in a
    latency breakdown is worse than a missing one. It must be reported, not
    silently clamped to zero: a clamp would hide exactly the drift it proves."""
    import logging

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        closed = close_step_accounting([("execute", 200.0)], total_ms=100.0)
    assert all(v >= 0 for _, v in closed)
    assert "unaccounted" not in dict(closed)
    assert "EXCEEDS" in caplog.text, (
        "a step sum larger than the turn must be reported, not swallowed"
    )


def test_an_empty_breakdown_still_accounts_for_the_turn() -> None:
    """A turn that died before any step ran still took time, and saying so is
    more honest than an empty object."""
    closed = close_step_accounting([], total_ms=4200.0)
    assert dict(closed)["unaccounted"] == pytest.approx(4200.0)


def test_duplicate_step_names_are_preserved_in_the_sum() -> None:
    """``dict()`` at the storage boundary collapses duplicates, so the residual
    must be computed from the LIST — otherwise a re-run step's time would be
    counted as unaccounted and the residual would lie in the other direction."""
    closed = close_step_accounting(
        [("execute", 100.0), ("execute", 300.0)], total_ms=500.0,
    )
    assert dict(closed)["unaccounted"] == pytest.approx(100.0)


def test_a_zero_length_turn_is_not_a_division(caplog: pytest.LogCaptureFixture) -> None:
    """Defensive: total_ms of 0 must not produce a negative or a crash."""
    assert close_step_accounting([], total_ms=0.0) == []
