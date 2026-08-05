"""ADR-19 — don't diagnose an outage using the thing that is out.

MEASURED 2026-08-05. On 2026-07-29 the LLM backend was down and the platform
attempted 1,294 turns against a normal day's 60-150:

    hypothesis   / rca  292      <- invisible on a healthy day
    rca_gatherer / rca  291
    verifier     / rca  288
    ------------------------
                        871 of 1,294 turns were the RCA channel

The loop: provider dies -> turns fail -> failures open incidents -> RCA runs to
diagnose them -> RCA's own three stages call the same dead provider -> more
failures. The self-healing machinery consumed the outage as input and multiplied
it ~10x, producing 258 barren verdicts that day.

The code already knew: a comment in the dispatch loop says "a provider outage
during the incident is precisely when the RCA call itself is most likely to also
fail" — and answered "retry next tick", which is what generated ~290 cycles.
"""

from __future__ import annotations

import pytest

from stackowl.scheduler.handlers.incident_escalation import (
    classify_incident_retryability,
)


@pytest.mark.parametrize(
    "failure_class",
    ["AllProvidersUnavailableError", "CircuitOpenError"],
)
def test_a_substrate_outage_is_deferred_not_analyzed(failure_class):
    """Diagnosing "the model is unreachable" REQUIRES the model. An RCA here
    cannot succeed and its three stages become three more failures."""
    assert classify_incident_retryability(failure_class) == "defer"


def test_these_would_ALL_have_been_analyzed_before():
    """The regression guard. Every one of these is an InfrastructureError, so
    the "analyze" branch claimed them — which is exactly what happened, 290
    times in one day. If the defer check is ever moved after the class
    resolution, this is what breaks."""
    from stackowl import exceptions as exc_mod

    for name in ("AllProvidersUnavailableError", "CircuitOpenError"):
        cls = getattr(exc_mod, name)
        assert issubclass(cls, exc_mod.InfrastructureError), name
        assert classify_incident_retryability(name) != "analyze", name


def test_a_real_provider_defect_is_STILL_analyzed():
    """ProviderError is deliberately NOT deferred. A 400 bad-request is our own
    payload bug and is exactly the kind of thing an RCA should look at — the
    measured 461 BadRequestErrors from the conversation miner (DEBT-40) are that
    case. Deferring it would hide a real defect behind an outage rule."""
    assert classify_incident_retryability("ProviderError") == "analyze"


def test_a_rate_limit_is_NOT_deferred():
    """It was in my first version of the defer set and an existing test caught
    it, correctly. A rate limit is OUR limiter failing closed (F124), not the
    substrate vanishing — a zero refill_rate is a real misconfiguration worth
    diagnosing. It also appeared ZERO times in the measured outage, so
    deferring it was scope with no evidence behind it."""
    assert classify_incident_retryability("RateLimitError") == "analyze"


def test_a_timeout_is_still_analyzed():
    """A recurring timeout survived the retry/recycle loop and warrants a real
    diagnosis — it is not a statement that the substrate is gone."""
    assert classify_incident_retryability("OwlTimeoutError") == "analyze"


def test_a_domain_failure_still_short_circuits():
    """Unchanged: a deterministic domain failure goes straight to a substitution
    verdict without an RCA cycle."""
    assert classify_incident_retryability("OwlNotFoundError") == "non_retryable"


def test_an_unknown_failure_class_still_errs_toward_analysis():
    """The never-skip-a-diagnosis-on-uncertainty rule must survive. Defer is an
    exemption for the ONE case we know analysis is impossible, not a new
    default."""
    assert classify_incident_retryability("down") == "analyze"
    assert classify_incident_retryability("") == "analyze"
    assert classify_incident_retryability("SomeUnknownThing") == "analyze"


def test_defer_is_decided_structurally_not_by_message_text():
    """Standing rule. The decision keys on the exception CLASS NAME the outcome
    store recorded — a stable code identifier — never on prose."""
    assert classify_incident_retryability("all providers are unavailable") == "analyze"
    assert classify_incident_retryability("AllProvidersUnavailableError") == "defer"
