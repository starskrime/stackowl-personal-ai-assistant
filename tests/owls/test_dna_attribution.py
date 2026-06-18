"""Tests for Learning Commit 4 — DnaAttributor + EvolutionCoordinator rewiring."""

from __future__ import annotations

import random
import time

from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_attribution import (
    MIN_SAMPLES_FOR_ATTRIBUTION,
    DnaAttributor,
    _band_for,
)


def _o(
    *, quality: float, dna_snapshot: dict[str, float],
    trace_id: str | None = None,
) -> TaskOutcome:
    """Helper: TaskOutcome with the fields attribution actually cares about."""
    return TaskOutcome(
        outcome_id=0, trace_id=trace_id or f"t-{quality}-{dna_snapshot}",
        session_id="s", owl_name="scout", channel="cli",
        success=True, latency_ms=100.0, tool_call_count=0,
        failure_class=None, quality_score=quality, step_durations={},
        input_text="", response_text="",
        captured_at=time.time(), scored_at=time.time(),
        tool_sequence=(), dna_snapshot=dna_snapshot,
    )


# ---------- band classifier -------------------------------------------------


def test_band_for_low_mid_high() -> None:
    assert _band_for(0.0) == "low"
    assert _band_for(0.29999) == "low"
    assert _band_for(0.3) == "mid"
    assert _band_for(0.5) == "mid"
    assert _band_for(0.69999) == "mid"
    assert _band_for(0.7) == "high"
    assert _band_for(1.0) == "high"


# ---------- AttributionReport sample-size gating ----------------------------


def test_attributor_returns_empty_when_below_threshold() -> None:
    attr = DnaAttributor(rng=random.Random(42), explore_epsilon=0.0)
    # Only 5 outcomes — well below default threshold of 20.
    outcomes = [
        _o(quality=0.8, dna_snapshot={"challenge_level": 0.5,
                                       "verbosity": 0.5, "curiosity": 0.5,
                                       "formality": 0.5, "creativity": 0.5,
                                       "precision": 0.5})
        for _ in range(5)
    ]
    report = attr.attribute(
        owl_name="scout", current_dna=OwlDNA(), outcomes=outcomes,
    )
    assert report.deltas == {}
    assert report.fallback_reason is not None
    assert "20" in report.fallback_reason


def test_attributor_ignores_outcomes_without_dna_snapshot() -> None:
    attr = DnaAttributor(rng=random.Random(0), explore_epsilon=0.0)
    outcomes = [_o(quality=0.9, dna_snapshot={}) for _ in range(50)]
    report = attr.attribute(
        owl_name="scout", current_dna=OwlDNA(), outcomes=outcomes,
    )
    assert report.deltas == {}


def test_attributor_proposes_delta_toward_winning_band() -> None:
    """challenge_level=high outcomes outperform low → propose +delta to push up."""
    attr = DnaAttributor(rng=random.Random(99), explore_epsilon=0.0)
    # Owl currently sits at low band (0.2); attributor should push toward high.
    current = OwlDNA(challenge_level=0.2)
    outcomes: list[TaskOutcome] = []
    # 10 low-band outcomes with mean quality 0.5
    for i in range(10):
        outcomes.append(_o(
            quality=0.45 + (i % 2) * 0.1,
            dna_snapshot={"challenge_level": 0.15,
                          "verbosity": 0.5, "curiosity": 0.5,
                          "formality": 0.5, "creativity": 0.5,
                          "precision": 0.5},
            trace_id=f"low-{i}",
        ))
    # 10 high-band outcomes with mean quality 0.9
    for i in range(10):
        outcomes.append(_o(
            quality=0.85 + (i % 2) * 0.1,
            dna_snapshot={"challenge_level": 0.85,
                          "verbosity": 0.5, "curiosity": 0.5,
                          "formality": 0.5, "creativity": 0.5,
                          "precision": 0.5},
            trace_id=f"high-{i}",
        ))
    report = attr.attribute(
        owl_name="scout", current_dna=current, outcomes=outcomes,
    )
    assert "challenge_level" in report.deltas
    assert report.deltas["challenge_level"] > 0.0  # nudge upward


def test_attributor_proposes_negative_delta_when_low_band_wins() -> None:
    attr = DnaAttributor(rng=random.Random(0), explore_epsilon=0.0)
    current = OwlDNA(verbosity=0.9)
    outcomes: list[TaskOutcome] = []
    for i in range(10):
        outcomes.append(_o(
            quality=0.9,
            dna_snapshot={"challenge_level": 0.5, "verbosity": 0.15,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"vlow-{i}",
        ))
    for i in range(10):
        outcomes.append(_o(
            quality=0.5,
            dna_snapshot={"challenge_level": 0.5, "verbosity": 0.85,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"vhigh-{i}",
        ))
    report = attr.attribute(
        owl_name="scout", current_dna=current, outcomes=outcomes,
    )
    assert "verbosity" in report.deltas
    assert report.deltas["verbosity"] < 0.0  # nudge downward toward winning low band


def test_attributor_holds_when_already_in_winning_band() -> None:
    attr = DnaAttributor(rng=random.Random(0), explore_epsilon=0.0)
    current = OwlDNA(challenge_level=0.85)  # already in high band
    outcomes: list[TaskOutcome] = []
    for i in range(10):
        outcomes.append(_o(
            quality=0.4,
            dna_snapshot={"challenge_level": 0.15, "verbosity": 0.5,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"l-{i}",
        ))
    for i in range(10):
        outcomes.append(_o(
            quality=0.9,
            dna_snapshot={"challenge_level": 0.85, "verbosity": 0.5,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"h-{i}",
        ))
    report = attr.attribute(
        owl_name="scout", current_dna=current, outcomes=outcomes,
    )
    # challenge_level proposed_delta should be 0 (already in winning band).
    cl_attr = next(t for t in report.per_trait if t.trait == "challenge_level")
    assert cl_attr.proposed_delta == 0.0
    assert "already in best band" in cl_attr.rationale


def test_attributor_holds_when_band_gap_below_threshold() -> None:
    attr = DnaAttributor(rng=random.Random(0), explore_epsilon=0.0)
    current = OwlDNA()
    outcomes: list[TaskOutcome] = []
    # Both bands score the same → no signal.
    for i in range(10):
        outcomes.append(_o(
            quality=0.75,
            dna_snapshot={"challenge_level": 0.15, "verbosity": 0.5,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"l-{i}",
        ))
    for i in range(10):
        outcomes.append(_o(
            quality=0.78,
            dna_snapshot={"challenge_level": 0.85, "verbosity": 0.5,
                          "curiosity": 0.5, "formality": 0.5,
                          "creativity": 0.5, "precision": 0.5},
            trace_id=f"h-{i}",
        ))
    report = attr.attribute(
        owl_name="scout", current_dna=current, outcomes=outcomes,
    )
    cl_attr = next(t for t in report.per_trait if t.trait == "challenge_level")
    assert cl_attr.proposed_delta == 0.0
    assert "gap" in cl_attr.rationale


# ---------- explore margin -------------------------------------------------


def test_explore_margin_fires_with_high_epsilon() -> None:
    # epsilon=1.0 forces explore to fire every time.
    attr = DnaAttributor(rng=random.Random(42), explore_epsilon=1.0)
    # Sample size below threshold so attribution path returns empty.
    outcomes = [_o(quality=0.8, dna_snapshot={"challenge_level": 0.5,
                                               "verbosity": 0.5,
                                               "curiosity": 0.5,
                                               "formality": 0.5,
                                               "creativity": 0.5,
                                               "precision": 0.5})
                for _ in range(30)]
    report = attr.attribute(
        owl_name="scout", current_dna=OwlDNA(), outcomes=outcomes,
    )
    assert report.explore_fired is True
    assert report.explore_trait is not None
    assert len(report.deltas) >= 1
    assert -0.10 <= next(iter(report.deltas.values())) <= 0.10


def test_explore_margin_never_fires_with_zero_epsilon() -> None:
    attr = DnaAttributor(rng=random.Random(42), explore_epsilon=0.0)
    outcomes = [_o(quality=0.8, dna_snapshot={"challenge_level": 0.5,
                                               "verbosity": 0.5,
                                               "curiosity": 0.5,
                                               "formality": 0.5,
                                               "creativity": 0.5,
                                               "precision": 0.5})
                for _ in range(30)]
    report = attr.attribute(
        owl_name="scout", current_dna=OwlDNA(), outcomes=outcomes,
    )
    assert report.explore_fired is False


# ---------- delta clamping -------------------------------------------------


def test_attributor_clamps_deltas_to_max_per_epoch() -> None:
    """Even if explore RNG produces a wild value, it's clamped to ±0.10."""
    class _MaxRNG:
        def random(self) -> float: return 0.0  # always fire
        def choice(self, seq): return seq[0]
        def uniform(self, a, b): return 99.0  # huge value

    attr = DnaAttributor(rng=_MaxRNG(), explore_epsilon=1.0)  # type: ignore[arg-type]
    outcomes = [_o(quality=0.8, dna_snapshot={"challenge_level": 0.5,
                                               "verbosity": 0.5,
                                               "curiosity": 0.5,
                                               "formality": 0.5,
                                               "creativity": 0.5,
                                               "precision": 0.5})
                for _ in range(25)]
    report = attr.attribute(
        owl_name="scout", current_dna=OwlDNA(), outcomes=outcomes,
    )
    # Whatever the explorer proposed must be clamped to ±0.10.
    for delta in report.deltas.values():
        assert -0.10 <= delta <= 0.10


# ---------- AttributionReport constant -------------------------------------


def test_min_samples_constant_is_20() -> None:
    """Per operator vote, must be 20."""
    assert MIN_SAMPLES_FOR_ATTRIBUTION == 20
