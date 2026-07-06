"""Unit tests for DNAPromptInjector — latch-backed directive emission."""

from __future__ import annotations

from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
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
    off = inj.inject(m, OwlDNA(challenge_level=0.50))   # below HIGH_EXIT (0.55) → exit
    assert on != "BASE" and hold != "BASE" and off == "BASE"


def test_no_directive_in_neutral() -> None:
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m("o2"), OwlDNA())  # all 0.5
    assert out == "BASE"


def test_high_curiosity_drives_exploration_breadth_not_clarify() -> None:
    # F-53: act-first / anti-over-clarify is no longer gated behind high curiosity.
    # It is now an UNCONDITIONAL charter principle (see test_base_prompt). The
    # curiosity HIGH directive governs EXPLORATION BREADTH only — it must NOT
    # re-introduce the always-ask language, and it no longer OWNS the act-first
    # nudge (so a low-curiosity owl still gets act-first from the charter).
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m("curio"), OwlDNA(curiosity=0.72))
    low = out.lower()
    assert out != "BASE"  # curiosity still modulates behaviour
    assert "ask clarifying questions whenever" not in low  # always-ask is gone
    assert "explore" in low or "broadly" in low  # now drives exploration breadth
