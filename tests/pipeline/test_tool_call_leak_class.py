"""A leaked tool call must not be recorded as a TIMEOUT.

OBSERVED LIVE 2026-08-07, on one of five messages sent in a row:

    [pipeline] execute: plain-stream final answer looks like an unparsed
                        tool call — flooring instead of leaking raw text
    [pipeline] execute: owl timeout
    OwlTimeoutError: Owl 'secretary' timed out after 0.0s
    task_outcomes: success=0, failure_class='OwlTimeoutError', latency=6.2s

"Timed out after 0.0s" is not a duration. The leak guard was raising
OwlTimeoutError purely to reach the existing flooring handler, so a turn that
never timed out was filed as an infrastructure timeout.

That is not cosmetic. classify_incident_retryability reads the class NAME, so
this was triaged as a recurring infra fault and sent for a three-stage RCA that
could never find a timeout — while genuine timeout statistics were polluted by
turns that were nothing of the kind.
"""

from __future__ import annotations

from stackowl.exceptions import OwlTimeoutError, ToolCallLeakError
from stackowl.scheduler.handlers.incident_escalation import (
    classify_incident_retryability,
)


def test_the_leak_is_still_caught_by_the_flooring_handler():
    """SUBCLASS on purpose: every existing `except OwlTimeoutError` — including
    the flooring path this is raised to reach — must keep working untouched."""
    assert issubclass(ToolCallLeakError, OwlTimeoutError)
    try:
        raise ToolCallLeakError("secretary")
    except OwlTimeoutError as exc:
        assert isinstance(exc, ToolCallLeakError)
    else:  # pragma: no cover
        raise AssertionError("the flooring handler would no longer catch this")


def test_it_no_longer_claims_a_zero_second_timeout():
    """The message a human reads in the log must describe what happened."""
    msg = str(ToolCallLeakError("secretary"))
    assert "timed out" not in msg, msg
    assert "0.0s" not in msg, msg
    assert "tool call" in msg


def test_the_recorded_failure_class_is_distinct():
    """failure_class is the class name, and it is what every downstream reader
    keys on — so the two failures must not share one."""
    assert type(ToolCallLeakError("x")).__name__ == "ToolCallLeakError"
    assert type(OwlTimeoutError("x", 30.0)).__name__ == "OwlTimeoutError"


def test_a_real_timeout_is_unchanged():
    real = OwlTimeoutError("secretary", 30.0)
    assert "timed out after 30.0s" in str(real)
    assert not isinstance(real, ToolCallLeakError)


def test_both_still_warrant_a_diagnosis_but_are_no_longer_conflated():
    """Neither is dismissed — a leaked tool call IS worth analysing. The point
    is that it is analysed as ITSELF rather than as a phantom timeout."""
    assert classify_incident_retryability("OwlTimeoutError") == "analyze"
    assert classify_incident_retryability("ToolCallLeakError") == "analyze"
