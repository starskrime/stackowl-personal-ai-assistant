"""D02.6 — the extended failure taxonomy and its recovery actions.

Ported from the reference platform's 22-reason failover taxonomy, which is
genuinely hard-won incident knowledge. NOT ported: its _classify_by_message path
(English substring matching) and its vendor-named members. Those are against
standing rules here, and every reason they encode is reachable from a status code
or a provider-declared quirk instead.

The tests that matter most are the LAST two: they pin the rules, because the
value of this port is that it kept them.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ProviderError
from stackowl.providers._resilient_round import (
    RECOVERY_FOR_CAUSE,
    FailureCause,
    RecoveryAction,
    classify_failure_cause,
    is_provider_fault,
    recovery_for,
)


class _Status(Exception):
    """An SDK-ish error carrying a status code."""

    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status_code = status


@pytest.mark.parametrize(
    ("status", "cause"),
    [
        (429, FailureCause.RATE_LIMIT),
        (529, FailureCause.OVERLOADED),
        (500, FailureCause.SERVER_5XX),
        (503, FailureCause.SERVER_5XX),
        (402, FailureCause.BILLING),
        (404, FailureCause.MODEL_NOT_FOUND),
        (408, FailureCause.TIMEOUT),
        (413, FailureCause.PAYLOAD_TOO_LARGE),
        (401, FailureCause.AUTH),
        (403, FailureCause.AUTH),
        (400, FailureCause.BAD_REQUEST),
    ],
)
def test_status_codes_map_to_causes(status, cause):
    assert classify_failure_cause(ProviderError("x", cause=_Status(status))) is cause


def test_529_is_checked_before_the_5xx_range():
    """Otherwise it collapses to SERVER_5XX and backs off like a plain 500,
    when saturation deserves harder backoff."""
    assert RECOVERY_FOR_CAUSE[FailureCause.OVERLOADED] is RecoveryAction.BACKOFF
    assert RECOVERY_FOR_CAUSE[FailureCause.SERVER_5XX] is RecoveryAction.RETRY


def test_413_no_longer_blind_retries():
    """THE DEFECT THIS PORT FIXES. All of 400-499 used to collapse to AUTH, so a
    payload-too-large was retried unchanged — a retry that cannot self-heal a
    request that is simply too big."""
    assert recovery_for(ProviderError("x", cause=_Status(413))) is RecoveryAction.COMPRESS


def test_404_falls_back_to_another_model():
    assert recovery_for(ProviderError("x", cause=_Status(404))) is RecoveryAction.FALLBACK_MODEL


def test_a_400_is_named_a_bad_request_not_a_credential_failure():
    """MEASURED LIVE: a background miner has been taking 400s from one provider
    for weeks, logged as "credential failure". That line sends an operator to the
    API key for a request-construction bug. Both still ABORT — the difference is
    that the log now points at the right thing."""
    assert classify_failure_cause(ProviderError("x", cause=_Status(400))) is (
        FailureCause.BAD_REQUEST
    )
    assert recovery_for(ProviderError("x", cause=_Status(400))) is RecoveryAction.ABORT


def test_a_400_does_not_cascade_because_another_model_would_reject_it_too():
    from stackowl.providers.llm_gateway import is_cascadable_fault

    assert not is_cascadable_fault(ProviderError("x", cause=_Status(400)))


def test_auth_aborts_rather_than_retrying():
    """We already LOGGED 'blind retry will not self-heal this' for these. Now the
    taxonomy says it instead of a log line."""
    assert recovery_for(ProviderError("x", cause=_Status(401))) is RecoveryAction.ABORT


def test_an_unmapped_cause_aborts_fail_safe(monkeypatch):
    """If someone adds a cause and forgets its action, the loop must stop rather
    than retry something nobody has reasoned about."""
    monkeypatch.delitem(RECOVERY_FOR_CAUSE, FailureCause.RATE_LIMIT)
    assert recovery_for(ProviderError("x", cause=_Status(429))) is RecoveryAction.ABORT


def test_an_unrecognised_exception_is_still_not_a_fault():
    """Unchanged pre-existing fail-safe: an internal bug must never look like an
    upstream outage, or it trips the circuit breaker against a healthy provider."""

    class _Weird(Exception):
        pass

    assert classify_failure_cause(ProviderError("x", cause=_Weird())) is FailureCause.NOT_A_FAULT


def test_a_status_less_error_never_becomes_a_breaker_fault():
    """The widened status gate is floored at 400 on purpose: `_cause_for_status`
    falls through to TRANSPORT, which IS a fault. An exception carrying a stray
    non-error status must not trip the breaker."""

    class _Odd(Exception):
        status_code = 200

    assert not is_provider_fault(ProviderError("x", cause=_Odd()))


def test_every_cause_states_what_to_do_about_it():
    """A taxonomy that does not change behaviour is decoration. This fails the
    moment someone adds a cause without deciding its action."""
    missing = [c for c in FailureCause if c not in RECOVERY_FOR_CAUSE]
    assert not missing, f"causes with no recovery action: {missing}"


def test_breaker_faults_change_in_exactly_one_place():
    """is_provider_fault feeds the circuit breaker, so splitting the 4xx range
    apart must not start (or stop) tripping it BY ACCIDENT. 408 is the single
    intended change and is asserted on its own below."""
    for status in (402, 404, 413, 401, 403, 400):
        assert not is_provider_fault(ProviderError("x", cause=_Status(status))), status
    for status in (429, 500, 503):
        assert is_provider_fault(ProviderError("x", cause=_Status(status))), status


# --------------------------------------------------------------------------- #
# The rules this port had to keep. These are the point.
# --------------------------------------------------------------------------- #


def test_the_classifier_contains_NO_english_message_matching():
    import pathlib

    src = pathlib.Path("src/stackowl/providers/_resilient_round.py").read_text()
    body = src[src.index("def classify_failure_cause") :]
    for smell in ("in error_msg", "in str(exc).lower()", '.lower() in', "message.lower()"):
        assert smell not in body, f"English matching crept in: {smell!r}"


def test_NO_vendor_names_in_the_taxonomy():
    """The reference names vendors in the enum itself. Ours reaches the same
    reasons by status code, and anything status-less is a provider-declared
    quirk in config — so src/ stays vendor-neutral."""
    import pathlib

    src = pathlib.Path("src/stackowl/providers/_resilient_round.py").read_text()
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#") and '"""' not in line
    )
    for vendor in ("llama_cpp", "mimo", "xiaomi", "openrouter", "ollama"):
        assert vendor not in code.lower(), f"vendor name in code: {vendor}"


def test_provider_quirks_are_config_not_code():
    from stackowl.config.provider import ProviderConfig

    base = {"name": "p", "protocol": "openai", "default_model": "m", "tiers": ("fast",)}
    assert ProviderConfig(**base).quirks == ()
    assert ProviderConfig(**base, quirks=("strip_grammar_pattern",)).quirks == (
        "strip_grammar_pattern",
    )


# --------------------------------------------------------------------------- #
# Wiring. A taxonomy nobody consults is decoration — this is the D05.2 lesson
# (M1 survived because 19 module tests passed while the module was unwired).
# --------------------------------------------------------------------------- #


def test_a_bad_model_id_now_cascades_to_the_next_tier():
    """404 = this model is unusable. is_provider_fault says False (the provider is
    fine), so before D02.6 the turn dead-ended at the user with other tiers idle."""
    from stackowl.providers.llm_gateway import is_cascadable_fault

    assert is_cascadable_fault(ProviderError("x", cause=_Status(404)))


def test_an_out_of_credit_provider_now_cascades():
    from stackowl.providers.llm_gateway import is_cascadable_fault

    assert is_cascadable_fault(ProviderError("x", cause=_Status(402)))


def test_an_auth_failure_still_does_NOT_cascade():
    """ABORT means abort. Cascading a bad credential would try every tier and
    burn the turn's budget discovering the same thing three times."""
    from stackowl.providers.llm_gateway import is_cascadable_fault

    assert not is_cascadable_fault(ProviderError("x", cause=_Status(401)))


def test_a_413_does_not_cascade_because_nothing_can_compress_yet():
    """COMPRESS has no actuator. Honest: the taxonomy names it, the ladder does
    not pretend to perform it. Climbing a tier does not make the payload smaller."""
    from stackowl.providers.llm_gateway import is_cascadable_fault

    assert not is_cascadable_fault(ProviderError("x", cause=_Status(413)))


def test_a_408_now_counts_against_the_breaker():
    """The one deliberate breaker change in D02.6: 408 used to reach AUTH via the
    4xx catch-all. A status-less TimeoutError always counted; this now matches."""
    assert is_provider_fault(ProviderError("x", cause=_Status(408)))


def test_529_still_counts_against_the_breaker():
    """Splitting OVERLOADED out of SERVER_5XX must not quietly stop tripping it —
    saturation is exactly when the breaker earns its keep."""
    assert is_provider_fault(ProviderError("x", cause=_Status(529)))
