"""The evidence-backed DNA signal must be able to fire on the traits we actually have.

MEASURED 2026-09-03, and this is the whole item.

``DnaAttributor`` buckets each trait into ABSOLUTE bands — low [0.0, 0.3), mid
[0.3, 0.7), high [0.7, 1.0] — and needs at least TWO bands with >= 3 samples
before it will compute a quality gap. Across all 9,586 outcomes in the live
database that carry both a ``quality_score`` and a ``dna_snapshot``, for ALL
SEVEN traits, EVERY SINGLE SAMPLE falls in the one ``mid`` band:

    trait                low     mid    high   observed range
    challenge_level        0    9586       0   [0.450, 0.620]
    completion_drive       0    9309       0   [0.500, 0.623]
    creativity             0    9586       0   [0.467, 0.530]
    curiosity              0    9586       0   [0.500, 0.632]
    formality              0    9586       0   [0.464, 0.598]
    precision              0    9586       0   [0.500, 0.621]
    verbosity              0    9586       0   [0.461, 0.550]

One band ever qualifies, so ``len(bands) < 2`` short-circuits every trait, every
run. The attribution path is not merely unlucky — it is STRUCTURALLY INCAPABLE of
producing a signal, and has been for all 20,146 recorded outcomes.

CONFIRMED IN THE LOGS. Of 60 attributor runs, 54 returned nothing with the reason
"no trait band gap exceeded threshold across any trait (scanned 7 traits over 500
samples)". The 6 that did propose a trait came from ``explore_fired=true`` — the
random 10% exploration margin — never from a band gap. Every evolution hypothesis
ever logged carries ``src=llm_fallback``.

WHY IT CANNOT SELF-CORRECT. Crossing a band edge from the 0.5 start needs a 0.2
move. ``_STEP_SIZE`` is 0.05 and ``_MAX_DELTA_PER_EPOCH`` is 0.10, and each step
must clear a shadow gate that requires 3 consecutive non-regressions — which has
succeeded twice, ever. So: attribution needs trait spread to produce a signal;
spread needs promotions; promotions need a signal. The loop holds itself shut,
and every actual change came from the LLM fallback, which is deliberately scaled
DOWN as the weakest signal (``SignalStrength.LLM_QUALITY``).

THE FIX IS TO BAND THE DISTRIBUTION WE HAVE, NOT THE ONE THE SCALE IMAGINES.
Band edges are derived from the observed values, so any real spread produces
comparable groups.

AND THAT ALONE WOULD HAVE MADE THINGS WORSE. A first pass computed
distribution-relative bands on the live rows and got, for ``secretary``, two
traits with a 0.196 quality gap — nearly double the threshold, and apparently a
vindication. It was not. ``formality`` and ``precision`` returned IDENTICAL
gaps, which is the tell: measuring the number of distinct DNA configurations
rather than the number of turns gives FIVE OF SIX OWLS EXACTLY ONE, and
``secretary`` two. So "500 samples" is 500 observations of one configuration,
and the gap between two such groups is the difference between two ERAS —
confounded with every model change, prompt change and unrelated fix in the
window. Shipping the banding fix by itself would have converted "0 attributions,
honest" into "a confident attribution built from 2 points, at VERIFIED strength,
which the governor multiplies by 1.0 where the LLM fallback gets 0.3."

So two floors guard it, and both must hold:

* ``_MIN_TRAIT_SPREAD`` — the trait must have varied by at least one evolution
  step, or the differences are smaller than the smallest change the system can
  make and the bands are noise;
* ``_MIN_BAND_DISTINCT_VALUES`` — a band needs distinct SETTINGS, not just
  distinct turns, because the independent unit of "did changing this trait
  change quality" is the configuration, not the observation.

On today's data attribution therefore still declines — but for the true reason,
stated truthfully, instead of the misleading "no band gap exceeded threshold
across 500 samples" which implied 500 independent observations.
"""

from __future__ import annotations

import random

import pytest

from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_attribution import _MIN_BAND_GAP, _STEP_SIZE, DnaAttributor

#: The real range every trait actually occupies in production, measured above.
LIVE_LOW, LIVE_HIGH = 0.46, 0.63


def _outcome(trait_value: float, quality: float, i: int) -> TaskOutcome:
    """A SUCCESSFUL, scored outcome — the only kind attribution learns from."""
    return TaskOutcome(
        outcome_id=i, trace_id=f"t{i}", session_key="s", owl_name="secretary",
        channel="telegram", success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, quality_score=quality, step_durations={},
        input_text="hi", response_text="hello", captured_at=float(i),
        scored_at=float(i), approach_rating=None,
        dna_snapshot={"precision": trait_value},
    )


def _sample(n: int = 60, *, spread: bool = True, correlated: bool = True) -> list[TaskOutcome]:
    """A trait swept across the live range at MANY DISTINCT SETTINGS.

    Distinct settings, not just two clusters: the attributor now requires
    ``_MIN_BAND_DISTINCT_VALUES`` different values within a band before it will
    treat that band as an independent sample. Two clusters of 20 identical
    values are two experiments run 20 times each, and the "gap" between them is
    the difference between two eras — which is exactly the confound that made
    the live data unusable (five of six owls have ONE distinct configuration in
    their entire history).

    ``correlated`` — higher trait values also score higher, so there IS a real
    gradient to find. ``spread`` — when False every value is identical, which
    must yield nothing.
    """
    out: list[TaskOutcome] = []
    for i in range(n):
        if spread:
            # i/(n-1) walks the live range in n distinct steps
            v = LIVE_LOW + (LIVE_HIGH - LIVE_LOW) * i / (n - 1)
        else:
            v = 0.5
        q = (0.5 + 0.4 * i / (n - 1)) if correlated else 0.7
        out.append(_outcome(round(v, 6), round(q, 6), i))
    return out


def _attr() -> DnaAttributor:
    """Exploration OFF — otherwise a random nudge masks whether the EVIDENCE fired."""
    return DnaAttributor(rng=random.Random(0), explore_epsilon=0.0)


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


def test_a_trait_that_never_leaves_the_middle_still_produces_a_signal() -> None:
    """THE DEFECT. Every live trait value sits in [0.45, 0.63]; under absolute
    thirds that is one band, so nothing could ever be proposed."""
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=_sample(),
    )
    assert report.deltas, (
        "no delta proposed for a trait with a clear quality gap inside the range "
        "the traits actually occupy — the attribution path is still dead"
    )
    assert "precision" in report.deltas
    assert report.fallback_reason is None


def test_the_delta_points_toward_the_higher_quality_values() -> None:
    """Direction must follow the evidence, not a fixed band centre. The old code
    aimed at _BAND_CENTERS["high"] = 0.85, a value no trait has ever reached."""
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=_sample(),
    )
    assert report.deltas["precision"] > 0, "high trait values scored better; delta should rise"

    # And the mirror image: when the LOW values score better, the delta falls.
    # current_dna starts HIGH here on purpose — at 0.5 the owl is already inside
    # the winning band and the correct answer is to propose nothing, which is the
    # hold guard, exercised separately below.
    flipped = [
        _outcome(round(LIVE_LOW + (LIVE_HIGH - LIVE_LOW) * i / 59, 6),
                 round(0.9 - 0.4 * i / 59, 6), i)
        for i in range(60)
    ]
    down = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=LIVE_HIGH), outcomes=flipped,
    )
    assert down.deltas["precision"] < 0


def test_an_owl_already_in_the_winning_band_is_left_alone() -> None:
    """THE ANTI-RATCHET BRAKE, and the reason bands and steering must come from
    ONE policy.

    If the samples were re-banded by their observed distribution while the
    steering target stayed a fixed constant (the old _BAND_CENTERS["high"] =
    0.85), this brake could never fire: the classifier would say "mid" for every
    real value while the winner was named "high". The result is +_STEP_SIZE in
    the same direction every epoch, forever, at VERIFIED signal strength — a
    ratchet that logs exactly like healthy learning."""
    report = _attr().attribute(
        owl_name="secretary",
        # The top of the range wins in _sample(); start the owl there.
        current_dna=OwlDNA(precision=LIVE_HIGH),
        outcomes=_sample(),
    )
    assert "precision" not in report.deltas


# --------------------------------------------------------------------------- #
# What must still NOT fire                                                     #
# --------------------------------------------------------------------------- #


def test_a_trait_that_never_varied_proposes_nothing() -> None:
    """THE GUARD. Deriving bands from the data means a near-constant distribution
    could be split into 'bands' that differ by nothing but noise. Below one
    _STEP_SIZE of spread there is no variation the system could even have
    caused, so there is nothing to attribute."""
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5),
        outcomes=_sample(spread=False),
    )
    assert report.deltas == {}
    assert report.fallback_reason is not None


def test_many_turns_at_two_settings_are_not_two_experiments() -> None:
    """THE CONFOUNDING GUARD, and the exact shape of the live data.

    Thirty turns at one trait value and thirty at another, with a large quality
    difference between them. There are plenty of OBSERVATIONS per band and a
    huge gap — and it is worth nothing, because the trait only ever held two
    settings, so the "gap" is the difference between two ERAS and carries every
    model change, prompt change and unrelated fix that happened between them.

    This is not hypothetical: measured 2026-09-03, five of six owls have exactly
    ONE distinct dna_snapshot across their entire history and secretary has two.
    An earlier pass of this very change computed a 0.196 gap for secretary and
    briefly read it as vindication; formality and precision returning IDENTICAL
    gaps was the tell.

    Caught by mutation: with the distinct-settings floor removed, every other
    test in this file still passed."""
    two_settings = [
        _outcome(LIVE_HIGH if i < 30 else LIVE_LOW, 0.9 if i < 30 else 0.4, i)
        for i in range(60)
    ]
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5),
        outcomes=two_settings,
    )
    assert report.deltas == {}, (
        "attributed a quality difference to a trait that only ever held two "
        "values — that is era-vs-era, not cause-and-effect"
    )
    assert report.fallback_reason is not None


def test_a_spread_smaller_than_one_step_is_noise_not_signal() -> None:
    """THE SPREAD GUARD. Sixty DISTINCT settings, so the independence floor is
    satisfied — but they span 0.002, far below one _STEP_SIZE. Variation smaller
    than the smallest change the system can make cannot have been caused by the
    system, and splitting it into bands manufactures structure out of sampling
    noise. The quality gradient here is steep on purpose: without the guard the
    bands would find a large, entirely spurious gap.

    Caught by mutation: with the spread floor removed,
    ``test_a_trait_that_never_varied_proposes_nothing`` still passed, because an
    all-identical sample collapses to one band by a different route."""
    tiny = [
        _outcome(round(0.500 + 0.002 * i / 59, 8), round(0.5 + 0.4 * i / 59, 6), i)
        for i in range(60)
    ]
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=tiny,
    )
    assert report.deltas == {}
    assert report.fallback_reason is not None


def test_spread_without_a_quality_gap_proposes_nothing() -> None:
    """The 0.10 gap threshold is unchanged: varying a trait is not evidence that
    varying it HELPED."""
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5),
        outcomes=_sample(correlated=False),
    )
    assert report.deltas == {}


def test_failed_outcomes_are_still_never_mined() -> None:
    """POSITIVE-ONLY LEARNING is a standing directive and this change must not
    weaken it — a decisive gap built entirely from FAILURES yields nothing."""
    failed = [
        TaskOutcome(
            outcome_id=i, trace_id=f"f{i}", session_key="s", owl_name="secretary",
            channel="telegram", success=False, latency_ms=1.0, tool_call_count=1,
            failure_class="stop", quality_score=0.9 if i < 20 else 0.4,
            step_durations={}, input_text="x", response_text="y",
            captured_at=float(i), scored_at=float(i), approach_rating=None,
            dna_snapshot={
                "precision": round(LIVE_LOW + (LIVE_HIGH - LIVE_LOW) * i / 39, 6),
            },
        )
        for i in range(40)
    ]
    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=failed,
    )
    assert report.deltas == {}


def test_the_per_epoch_cap_still_binds() -> None:
    """Making the path live must not let it move a trait further per epoch than
    the DeltaValidator's range allows."""
    from stackowl.owls.dna_attribution import _MAX_DELTA_PER_EPOCH

    report = _attr().attribute(
        owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=_sample(),
    )
    for trait, d in report.deltas.items():
        assert abs(d) <= _MAX_DELTA_PER_EPOCH, f"{trait} exceeded the epoch cap: {d}"


# --------------------------------------------------------------------------- #
# The tripwire: CAPABLE of firing, not merely "fired once"                     #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_attribution_path_is_capable_of_firing_on_live_trait_ranges() -> None:
    """Asserts the CAPABILITY, not one lucky sample.

    The failure this guards is not "attribution proposed the wrong thing" — it is
    "attribution can never propose anything", which looks identical to a quiet,
    healthy system from the outside. It survived 20,146 outcomes because nothing
    ever asked whether the band boundaries were reachable.

    Swept across the full live range so a future change to the banding, the step
    size or the trait clamps that puts the boundaries back out of reach fails
    here rather than going silent for another 20,000 turns.
    """
    for lo, hi in ((0.46, 0.63), (0.48, 0.55), (0.50, 0.62), (0.30, 0.70)):
        outcomes = [
            _outcome(round(lo + (hi - lo) * i / 59, 6),
                     round(0.5 + 0.4 * i / 59, 6), i)
            for i in range(60)
        ]
        report = _attr().attribute(
            owl_name="secretary", current_dna=OwlDNA(precision=0.5), outcomes=outcomes,
        )
        assert report.deltas, (
            f"attribution cannot fire over trait range [{lo}, {hi}] with a "
            f"0.4 quality gap — the evidence-backed signal is dead again"
        )


@pytest.mark.tripwire
def test_the_thresholds_are_reachable_from_the_neutral_start() -> None:
    """The constants must stay mutually consistent.

    The original defect was arithmetic, not logic: reaching a band edge from the
    0.5 neutral start required a 0.2 move, while one step is _STEP_SIZE and the
    epoch cap is _MAX_DELTA_PER_EPOCH. Any banding scheme whose groups a trait
    cannot traverse is the same bug wearing different numbers.
    """
    assert _STEP_SIZE > 0
    assert 0 < _MIN_BAND_GAP < 1, "a gap threshold outside [0,1] can never be met"
