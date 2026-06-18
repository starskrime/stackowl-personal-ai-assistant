"""Tests for lean behavioral charter, LEAN_WINDOW_THRESHOLD, and the lean kwarg.

Covers:
  - LEAN_WINDOW_THRESHOLD constant value.
  - behavioral_charter_lean() produces a shorter but principle-preserving text.
  - build_base_prompt(now, lean=True) uses the lean charter.
  - build_base_prompt(now) / build_base_prompt(now, lean=False) are byte-identical
    and contain the full charter.
  - DNAPromptInjector.inject(..., lean=True) suppresses backfiring HIGH directives.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.owls.base_prompt import (
    LEAN_WINDOW_THRESHOLD,
    behavioral_charter,
    behavioral_charter_lean,
    build_base_prompt,
)
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.manifest import OwlAgentManifest

_NOW = datetime(2026, 6, 14, 12, 0, 0)


def test_threshold_value() -> None:
    assert LEAN_WINDOW_THRESHOLD == 8192


def test_lean_charter_shorter_but_keeps_principles() -> None:
    full = behavioral_charter()
    lean = behavioral_charter_lean()
    assert 0 < len(lean) < len(full)
    low = lean.lower()
    assert "own" in low
    assert "deliver" in low and ("hand back" in low or "manual" in low or "link" in low)
    assert "persist" in low
    assert "memory" in low


def test_build_base_prompt_lean_uses_lean_charter() -> None:
    assert behavioral_charter_lean() in build_base_prompt(_NOW, lean=True)
    assert behavioral_charter_lean() not in build_base_prompt(_NOW, lean=False)


def test_build_base_prompt_full_byte_identical() -> None:
    full = build_base_prompt(_NOW)
    assert build_base_prompt(_NOW, lean=False) == full
    assert behavioral_charter() in full


# ---------------------------------------------------------------------------
# DNAPromptInjector lean kwarg tests
# ---------------------------------------------------------------------------


def _mf(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(name=name, role="r", system_prompt="BASE", model_tier="fast")


def test_lean_suppresses_precision_directive() -> None:
    """lean=True must strip the precision (citation) directive."""
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    # Use distinct owl names to avoid latch cross-talk between lean/non-lean calls.
    out_lean = inj.inject(_mf("owl_prec_lean"), OwlDNA(precision=0.75), lean=True)
    assert "line numbers" not in out_lean


def test_non_lean_retains_precision_directive() -> None:
    """lean=False (explicit) must still emit the precision directive."""
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    out_full = inj.inject(_mf("owl_prec_full"), OwlDNA(precision=0.75), lean=False)
    assert "line numbers" in out_full


def test_lean_keeps_verbosity_low_directive() -> None:
    """lean=True must NOT suppress the cheap verbosity LOW directive."""
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    # verbosity LOW directive fires when verbosity <= 0.30
    out_lean = inj.inject(_mf("owl_verb_lean"), OwlDNA(verbosity=0.20), lean=True)
    assert "concise" in out_lean


def test_default_lean_false_byte_identical() -> None:
    """inject(manifest, dna) == inject(manifest, dna, lean=False) — backward compat."""
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    # Use different owl names to avoid latch carry-over between the two calls.
    # Both start cold (clear_all above), so seeding is deterministic.
    out_default = inj.inject(_mf("owl_compat_def"), OwlDNA(precision=0.75))
    DIRECTIVE_LATCH.clear_all()
    out_explicit = inj.inject(_mf("owl_compat_exp"), OwlDNA(precision=0.75), lean=False)
    # Both produce identical directive text; the owl name is NOT in the
    # injected output, so we can compare directly.
    assert out_default == out_explicit
