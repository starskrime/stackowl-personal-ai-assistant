"""Unit tests for DNAPromptInjector — latch-backed directive emission."""

from __future__ import annotations

from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.manifest import OwlAgentManifest


def _m(name: str = "scout") -> OwlAgentManifest:
    return OwlAgentManifest(name=name, role="r", system_prompt="BASE", model_tier="fast")


def test_high_directive_emitted_above_enter() -> None:
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m(), OwlDNA(challenge_level=0.72))
    assert "BASE" in out and out != "BASE"  # a directive was appended


def test_directive_latches_through_deadband() -> None:
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    m = _m("o1")
    on = inj.inject(m, OwlDNA(challenge_level=0.72))    # enter HIGH
    hold = inj.inject(m, OwlDNA(challenge_level=0.66))  # deadband → STILL on
    off = inj.inject(m, OwlDNA(challenge_level=0.58))   # exit
    assert on != "BASE" and hold != "BASE" and off == "BASE"


def test_no_directive_in_neutral() -> None:
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m("o2"), OwlDNA())  # all 0.5
    assert out == "BASE"
