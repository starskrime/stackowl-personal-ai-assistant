"""D04.5 — the request is logged, the refusal is not.

D04.5 is `keep` — "confirm it survives" — and it does. Verified three ways on
2026-09-05:

* `LLMGateway._can_escalate_meaningfully` exists and asks the real question ("is
  there a HIGHER tier that actually resolves somewhere else?");
* on the live configuration it answers **False**, because the ladder is degenerate —
  `describe_tier_ladder()` returns `NeraAiRaw/neraai-v1-raw` on all three rungs;
* both provider loops honour it: `if can_escalate and escalation_requested():`.

That gate was earned. Its docstring records the cost of not having it: **25
escalations, all landing on the same (provider, model)**, each making the provider
DISCARD a finished attempt — 14, 15, 16 and 19 tool calls in four of those turns —
and hand the gateway a turn it had to re-run, with the tool-outcome ledger reset so
the re-run was blind to what the first attempt had learned.

**WHAT DOES NOT SURVIVE IS THE EXPLANATION.** The pipeline logs
`"[pipeline] execute: circuit open — requested tier escalation"` at INFO when a tool's
breaker opens — 12 times in the kept logs — and **nothing anywhere records that the
request was declined, or why**. Measured: 12 requests, 0 outcomes. Someone asking
"why didn't that turn escalate?" finds a request and silence, and the honest answer —
"because every rung is the same model" — is exactly the fact D04.4 had to make
visible for the ladder itself.

WHY IT IS ONE FUNCTION AND NOT TWO IF-STATEMENTS. Both providers read the flag, so
putting the decline in each would be two copies of one rule — the shape this
programme keeps finding, and the shape that made the ollama sniff wrong in D04.2 one
item earlier. `escalation_allowed()` is the single place that answers "should this
turn escalate", and the single place that says why not.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.providers import escalation_signal as sig


@pytest.fixture(autouse=True)
def _clean_signal():
    sig.clear_escalation()
    sig.reset_decline_notice()
    yield
    sig.clear_escalation()
    sig.reset_decline_notice()


def test_no_request_means_no_escalation_and_no_noise(caplog) -> None:
    """The overwhelmingly common case must stay silent.

    A line emitted on every turn that did not ask to escalate would drown the one
    that did — the failure D18.7 recorded, where a guard that fires on the healthy
    case is one its reader learns to ignore.
    """
    with caplog.at_level(logging.INFO):
        assert sig.escalation_allowed(can_escalate=True) is False
    assert not caplog.records


def test_a_request_with_somewhere_to_go_escalates(caplog) -> None:
    sig.request_escalation("breaker opened")
    with caplog.at_level(logging.INFO):
        assert sig.escalation_allowed(can_escalate=True) is True
    assert not [r for r in caplog.records if "declined" in r.getMessage()]


def test_a_request_with_nowhere_to_go_is_declined_OUT_LOUD(caplog) -> None:
    """The gap this file exists for: 12 requests, 0 recorded outcomes."""
    sig.request_escalation("breaker opened")
    with caplog.at_level(logging.INFO):
        assert sig.escalation_allowed(can_escalate=False) is False

    declined = [r.getMessage() for r in caplog.records if "declined" in r.getMessage()]
    assert declined, "a declined escalation left no trace at all"
    assert "no stronger tier" in declined[0], (
        "the line must say WHY — a decline with no reason is the same silence"
    )


def test_the_decline_is_said_once_not_every_step(caplog) -> None:
    """A tool loop can ask many times in one turn; the answer does not change.

    Repeating it per step would turn one fact into a burst, which is how a true line
    becomes noise.
    """
    sig.request_escalation("breaker opened")
    with caplog.at_level(logging.INFO):
        for _ in range(5):
            sig.escalation_allowed(can_escalate=False)

    declined = [r for r in caplog.records if "declined" in r.getMessage()]
    assert len(declined) == 1, f"said it {len(declined)} times, expected once per turn"


def test_both_provider_loops_ask_the_same_function() -> None:
    """One source. Two `if can_escalate and escalation_requested()` statements would
    be two copies of one rule, which is how the ollama sniff in D04.2 diverged."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl" / "providers"
    for name in ("openai_provider.py", "anthropic_provider.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert "escalation_allowed(" in text, f"{name} does not use the shared decision"
        assert "can_escalate and escalation_requested()" not in text, (
            f"{name} still open-codes the decision — that is the second copy"
        )
