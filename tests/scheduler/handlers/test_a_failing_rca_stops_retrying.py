"""An RCA that cannot produce a verdict must stop retrying, like every other retry.

MEASURED across every retained log, 2026-08-29::

    [rca] staged.analyze: entry ........................................ 602
    incident_escalation: verdict UNVERIFIED — suppressing chat alert .... 345
    incident_escalation: RCA produced no verdict — NOT marking handled,
                         will retry next tick ........................... 94

Those 94 are an UNBOUNDED retry. The code says so in its own comment: a verdict of
None means the signature is deliberately NOT registered "so the NEXT tick retries
the RCA for this same persistent incident instead of silently giving up on it
forever after one failed attempt". The intent is right and is preserved here. What
is missing is a ceiling — so a signature whose RCA reliably fails is re-run every
tick, for ever, at roughly 70,000 input tokens a time.

THE COST THAT MAKES IT WORTH BOUNDING. incident-* lanes account for 77,079,388
input tokens all-time (11.8% of every input token the platform has spent) across
1,106 incidents at a mean of 69,692 each — and in the last 24 hours, 19,901,541
tokens, **52.8% of all input**. 601 incidents were opened for only 13 DISTINCT
signatures.

WHY THIS PART NEEDS NO OPERATOR DECISION, while the rest of ESC-66 does. Bounding a
retry that never succeeds cannot suppress a diagnosis that would have worked: a
signature whose RCA produces a verdict registers on the FIRST attempt and never
enters this path at all. The broader question — how long to decline re-diagnosing a
signature that HAS been diagnosed — is a suppression policy and stays escalated.

Same shape as two other fixes today: `fail_and_requeue` had a ceiling while the
reclaim paths did not, and the token meter was per-attempt where the bound needed
to be per-task. A retry without a ceiling is not persistence, it is a loop.
"""

from __future__ import annotations

import pytest

from stackowl.scheduler.handlers.incident_escalation import (
    _MAX_VERDICT_ATTEMPTS,
    IncidentEscalationHandler,
)


def _handler() -> IncidentEscalationHandler:
    return IncidentEscalationHandler.__new__(IncidentEscalationHandler)


def _fresh(h: IncidentEscalationHandler) -> IncidentEscalationHandler:
    h._open_incidents = {}
    h._verdict_failures = {}
    return h


def test_the_ceiling_is_above_one_so_a_transient_failure_still_retries() -> None:
    """The stated intent must survive: never give up after ONE failed attempt.

    A provider outage during an incident is precisely when the RCA call is most
    likely to also fail, and that must not permanently bury the signature.
    """
    assert _MAX_VERDICT_ATTEMPTS >= 2


def test_a_repeatedly_failing_signature_is_eventually_given_up_on() -> None:
    """The defect: 94 no-verdict RCAs, each re-run every tick for ever."""
    h = _fresh(_handler())
    sig = "outcome:shell:stop"

    gave_up_at = None
    for attempt in range(1, 12):
        should_retry = h._record_verdict_failure(sig)
        if not should_retry:
            gave_up_at = attempt
            break

    assert gave_up_at is not None, (
        "a signature whose RCA never produces a verdict retries for ever — at "
        "~70,000 input tokens per attempt"
    )
    assert gave_up_at == _MAX_VERDICT_ATTEMPTS


def test_giving_up_CLOSES_the_dedup_so_the_tick_stops_re_running_it() -> None:
    """Bounding the counter is useless if the signature still re-opens each tick.

    The retry happens because the signature is not registered in _open_incidents.
    Giving up must therefore register it — measure the EFFECT, not the counter.
    """
    h = _fresh(_handler())
    sig = "outcome:shell:stop"
    for _ in range(_MAX_VERDICT_ATTEMPTS):
        h._record_verdict_failure(sig)

    assert not h._should_retry_verdict(sig), (
        "the signature is still eligible for another RCA after the ceiling"
    )


def test_a_SUCCEEDING_signature_never_enters_this_path() -> None:
    """The guard must be narrow — a working RCA is untouched."""
    h = _fresh(_handler())
    assert h._should_retry_verdict("outcome:web_fetch:stop") is True
    assert h._verdict_failures == {}


def test_success_RESETS_the_counter() -> None:
    """Two failures then a verdict must not leave the signature one strike down.

    The failures were transient; the signature has proven it can be diagnosed.
    """
    h = _fresh(_handler())
    sig = "outcome:memory:stop"
    h._record_verdict_failure(sig)
    h._record_verdict_failure(sig)
    h._clear_verdict_failures(sig)

    assert h._should_retry_verdict(sig) is True
    assert sig not in h._verdict_failures


def test_signatures_are_counted_INDEPENDENTLY() -> None:
    """One exhausted signature must not bury the other twelve."""
    h = _fresh(_handler())
    for _ in range(_MAX_VERDICT_ATTEMPTS):
        h._record_verdict_failure("outcome:shell:stop")

    assert h._should_retry_verdict("outcome:shell:stop") is False
    assert h._should_retry_verdict("outcome:browser_navigate:stop") is True
