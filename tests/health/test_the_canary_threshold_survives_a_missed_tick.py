"""The send-canary threshold sat on top of its own distribution's tail.

MEASURED 2026-08-31 across every retained log — 250 confirmed canary sends, 249
gaps between them::

    median gap   1230s   (the 20-minute schedule, exactly)
    gaps > 2400s    10   (the staleness threshold)

and here is what those ten look like::

    6226s  22:31:23 -> 00:15:09     <- a REAL outage, deserved its alert
    2479s  16:32:23 -> 17:13:42
    2458s  03:07:53 -> 03:48:51
    2458s  02:38:39 -> 03:19:37
    2447s  13:12:24 -> 13:53:11
    2445s  07:02:46 -> 07:43:31
    2438s  15:11:32 -> 15:52:10
    2434s  08:04:01 -> 08:44:35
    2419s  14:31:13 -> 15:11:32
    2419s  09:04:56 -> 09:45:15

Nine of the ten are over the line by 19 to 79 SECONDS. That is not a detector; it
is a coin flip on the tail of the normal distribution. Each one produced a
critical "UNHEALTHY subsystems detected — degraded: ['telegram_canary_send']"
page, and the next sweep found ok=11/11 and sent the recovery notice, also
critical.

THE CONSTANT DOES NOT DO WHAT ITS OWN COMMENT SAYS. From scheduler/assembly.py::

    # stale_after_s = 2x the 20-min canary interval so one missed tick isn't a
    # false alarm, but a genuinely dead canary is caught within ~40 min.
    kind="send", stale_after_s=2400.0,

One missed tick puts the next confirmed send TWO intervals later — 2400s — so the
threshold is exactly the value it is meant to tolerate, and any jitter at all
trips it. The measurement is that jitter: 19-79 seconds of it, ten times. To
tolerate one missed tick the threshold has to EXCEED two intervals, not equal
them.

AND IT WAS TWO COPIES OF ONE RULE. The interval is written as ``every 20m`` /
``interval_minutes=20`` at the seed, and again as the arithmetic ``2400.0`` six
hundred lines later. Changing the schedule would have silently left the threshold
behind. One constant now, and both sites ask it.
"""

from __future__ import annotations

import inspect

from stackowl.scheduler.assembly import (
    TELEGRAM_CANARY_INTERVAL_MINUTES,
    TELEGRAM_CANARY_STALE_AFTER_S,
    TELEGRAM_CANARY_STALE_INTERVALS,
)

#: The nine near-miss gaps measured on 2026-08-31, in seconds. Every one produced
#: a pair of critical operator pages for a send path that was working.
_FALSE_ALARM_GAPS_S = (2419, 2419, 2434, 2438, 2445, 2447, 2458, 2458, 2479)

#: The one genuine gap in the same window — the canary really did stop.
_REAL_OUTAGE_GAP_S = 6226

_INTERVAL_S = TELEGRAM_CANARY_INTERVAL_MINUTES * 60


def test_the_threshold_is_DERIVED_from_the_interval() -> None:
    """Not a literal that happens to equal the arithmetic. Changing the schedule
    must move the threshold with it."""
    assert TELEGRAM_CANARY_STALE_AFTER_S == (
        TELEGRAM_CANARY_INTERVAL_MINUTES * 60 * TELEGRAM_CANARY_STALE_INTERVALS
    )


def test_it_actually_tolerates_ONE_missed_tick() -> None:
    """What the old comment claimed and the old number could not deliver: a gap of
    exactly two intervals is what ONE missed tick produces, so the threshold has
    to be strictly greater than that — with room for the jitter that was measured
    at 19 to 79 seconds."""
    one_missed_tick = 2 * _INTERVAL_S
    assert TELEGRAM_CANARY_STALE_AFTER_S > one_missed_tick, (
        f"{TELEGRAM_CANARY_STALE_AFTER_S}s does not exceed the {one_missed_tick}s "
        f"gap a single missed tick produces — this is the 2026-08-31 flap"
    )


def test_every_measured_FALSE_ALARM_is_now_below_the_line() -> None:
    """Replayed against the real gaps, not against a hypothetical."""
    for gap in _FALSE_ALARM_GAPS_S:
        assert gap < TELEGRAM_CANARY_STALE_AFTER_S, (
            f"a {gap}s gap still pages the operator; it did so on 2026-08-31 and "
            f"the send path was healthy both before and after"
        )


def test_the_REAL_outage_is_still_caught() -> None:
    """The expensive direction. Widening a threshold to stop noise is only
    correct while it still catches the thing it exists for."""
    assert _REAL_OUTAGE_GAP_S > TELEGRAM_CANARY_STALE_AFTER_S


def test_the_threshold_stays_USEFUL_and_does_not_drift_to_infinity() -> None:
    """A bound on the fix itself: a detector that needs hours is not a detector.
    The receive-loop contributor stays at 120s, so a dead INBOUND path is still
    caught in two minutes — this one is the slow end-to-end signal."""
    assert TELEGRAM_CANARY_STALE_AFTER_S <= 4 * _INTERVAL_S


def test_the_SEED_and_the_THRESHOLD_read_the_same_constant() -> None:
    """Structural, over the source. The whole defect was one number written twice
    — ``every 20m``/``interval_minutes=20`` at the seed and ``2400.0`` six hundred
    lines away — so a later reader changing the schedule would leave the threshold
    behind exactly as it was left behind here."""
    from stackowl.scheduler import assembly

    source = inspect.getsource(assembly)
    assert "stale_after_s=2400.0" not in source, "the literal is back"
    assert source.count("TELEGRAM_CANARY_INTERVAL_MINUTES") >= 3, (
        "the seed, the threshold and the definition must all name one constant"
    )
    assert 'schedule="every 20m"' not in source or (
        "TELEGRAM_CANARY_INTERVAL_MINUTES" in source
    )


def test_the_receive_contributor_is_UNCHANGED() -> None:
    """Only the send canary moves. The inbound loop's 120s staleness is a
    different signal with a different cadence (a 30s heartbeat) and it was never
    part of this flap."""
    from stackowl.health.contributors import _STALE_AFTER_S

    assert _STALE_AFTER_S == 120.0
