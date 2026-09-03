"""DnaAttributor — outcome-driven DNA tuning (Learning Commit 4).

Replaces the G1 gap: instead of an LLM reading raw conversation messages and
GUESSING which trait knob to turn, we bucket the owl's scored outcomes by
trait band, compute mean quality per band, and propose a delta toward the
band that historically wins.

Algorithm (per trait, per owl):

1. For each scored outcome with a captured ``dna_snapshot``, classify its
   trait value into one of three bands whose edges are the TERCILES OF THE
   OBSERVED VALUES (:func:`_band_edges`) — not absolute thirds of [0,1], which
   no trait has ever spanned (see the note on the constants below).
2. For each band with ≥ :data:`_MIN_BAND_SAMPLES` outcomes AND ≥
   :data:`_MIN_BAND_DISTINCT_VALUES` distinct trait settings, compute the mean
   ``quality_score``. The second condition is what keeps 500 turns at ONE
   setting from being mistaken for 500 experiments.
3. If at least two bands qualify AND the gap between best and worst band
   exceeds :data:`_MIN_BAND_GAP`, propose a delta of :data:`_STEP_SIZE`
   toward the best band (sign points from current band toward winner).
4. Cap deltas at :data:`_MAX_DELTA_PER_EPOCH`; DeltaValidator clamps further
   downstream.

Explore margin (10% per operator vote): with that probability, the trait
choice is replaced by a random ±0.05 nudge on a random trait, to keep
gathering variance.

Returns ``{}`` when there's no statistical signal — caller falls back to the
LLM evolution path (which itself now consumes a stats summary, not raw msgs).
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcome, is_positive_signal
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES

_MUTABLE_TRAITS: tuple[str, ...] = TRAIT_NAMES

# BANDS ARE DERIVED FROM THE OBSERVED VALUES, NOT FROM THE THEORETICAL SCALE.
# They used to be absolute thirds of [0,1] — low < 0.3, high >= 0.7 — with fixed
# centres at 0.15/0.5/0.85. MEASURED 2026-09-03 across all 9,586 outcomes in the
# live database carrying both a quality_score and a dna_snapshot: for ALL SEVEN
# traits, EVERY sample fell in the single "mid" band. Widest observed range of
# any trait: [0.450, 0.632]. Since a gap needs two qualifying bands, the
# evidence-backed path could never propose anything, and never did — 54 of 60
# runs returned nothing, and every hypothesis ever logged carries
# src=llm_fallback.
#
# It could not self-correct either: reaching an edge from the 0.5 start needs a
# 0.2 move, one step is _STEP_SIZE (0.05) behind a shadow gate demanding three
# consecutive non-regressions, which has passed twice ever. Attribution needed
# spread to make a signal; spread needed promotions; promotions needed a signal.
_MIN_BAND_SAMPLES = 3        # need ≥3 outcomes in a band for that mean to count
_MIN_BAND_GAP = 0.10         # winning-band quality must beat losing-band by 0.10
_STEP_SIZE = 0.05            # one evolution step nudges this much
#: A trait must vary by at least one evolution step before its spread counts as
#: signal. Below that the differences are smaller than the smallest change the
#: system can make, so splitting them into bands is manufacturing structure from
#: noise — the failure mode that deriving bands from data would otherwise invite.
_MIN_TRAIT_SPREAD = _STEP_SIZE
#: DISTINCT SETTINGS, not observations. The independent unit for "did changing
#: this trait change quality" is the number of distinct VALUES the trait has
#: held, not the number of turns run at them. MEASURED 2026-09-03: five of six
#: owls have exactly ONE distinct dna_snapshot across their whole history and
#: secretary has two — so "scanned 7 traits over 500 samples" was 500
#: observations of one configuration, and any band gap computed from it is the
#: difference between two ERAS, confounded with every model change, prompt change
#: and unrelated fix in the window. Attributing that to the trait would be
#: confident nonsense at VERIFIED signal strength, which the governor multiplies
#: by 1.0 where the LLM fallback gets 0.3.
_MIN_BAND_DISTINCT_VALUES = 2
_MAX_DELTA_PER_EPOCH = 0.10  # outer cap — matches DeltaValidator's range
_EXPLORE_EPSILON = 0.10      # 10% chance of a random nudge (per operator vote)
_EXPLORE_DELTA_BOUND = 0.05  # exploration nudges are ±0.05
_DEFAULT_LOOKBACK_DAYS = 14
_SECONDS_PER_DAY = 86_400

MIN_SAMPLES_FOR_ATTRIBUTION = 20  # below this, fall back to LLM path


@dataclass(frozen=True)
class BandStats:
    """Per-band quality statistics for one trait."""

    band: str            # low / mid / high
    n_samples: int
    mean_quality: float
    # The observed mean TRAIT value in this band. Direction used to point at a
    # fixed _BAND_CENTERS constant (0.15/0.5/0.85) — values no trait has ever
    # held — so it aimed at a target the scale could not reach. Steering toward
    # where the winning samples actually sit keeps the target inside the
    # distribution that produced the evidence.
    mean_value: float = NEUTRAL


@dataclass(frozen=True)
class TraitAttribution:
    """Per-trait attribution decision."""

    trait: str
    bands: tuple[BandStats, ...]  # only bands with ≥ _MIN_BAND_SAMPLES
    proposed_delta: float         # signed; 0.0 means "no signal"
    rationale: str                # human-readable for audit/log


@dataclass(frozen=True)
class AttributionReport:
    """Top-level outcome — emitted by the attributor."""

    owl_name: str
    n_scored_outcomes: int
    deltas: dict[str, float]                 # {trait: signed delta}, post-explore
    per_trait: tuple[TraitAttribution, ...]  # detail per trait (best-effort)
    explore_fired: bool                      # was the 10% explore margin used
    explore_trait: str | None
    fallback_reason: str | None = None       # populated when caller should LLM-fallback


def _band_edges(values: list[float]) -> tuple[float, float] | None:
    """Tercile edges of the OBSERVED trait values, or None when there is no spread.

    Returns ``None`` — meaning "this trait has not varied enough to attribute
    anything to it" — when the observed range is under :data:`_MIN_TRAIT_SPREAD`.
    That guard is the price of deriving bands from data: without it, a trait
    pinned at a single value would still be split into groups whose quality
    difference is pure sampling noise.
    """
    if len(values) < 2:
        return None
    if max(values) - min(values) < _MIN_TRAIT_SPREAD:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def _band_for(value: float, edges: tuple[float, float]) -> str:
    lo, hi = edges
    if value < lo:
        return "low"
    if value < hi:
        return "mid"
    return "high"


def _filter_scored_outcomes(outcomes: list[TaskOutcome]) -> list[TaskOutcome]:
    """POSITIVE-ONLY LEARNING filter — outcomes eligible for DNA attribution.

    F-54 (ACCEPTED-BY-DIRECTIVE): tune traits from SUCCESSFUL outcomes only
    (see feedback_positive_only_learning). Failed/penalized outcomes are
    dropped even when they carry a quality_score + dna_snapshot — the shared
    :func:`is_positive_signal` gate (success + no failure_class + not
    Disliked) — AND (DNA-attribution-specific) a trait delta can't be
    computed without a scored quality + a captured DNA snapshot.
    """
    return [
        o for o in outcomes
        if is_positive_signal(o) and o.quality_score is not None and o.dna_snapshot
    ]


class DnaAttributor:
    """Compute trait deltas from owl's scored outcomes.

    Pure logic — caller injects outcomes + current DNA. Self-contained;
    no DB / provider dependencies so unit tests are trivial.
    """

    def __init__(
        self,
        *,
        min_samples_for_attribution: int = MIN_SAMPLES_FOR_ATTRIBUTION,
        explore_epsilon: float = _EXPLORE_EPSILON,
        rng: random.Random | None = None,
    ) -> None:
        # 1. ENTRY
        log.engine.debug(
            "[dna] attributor.init: ready",
            extra={"_fields": {
                "min_samples_for_attribution": min_samples_for_attribution,
                "explore_epsilon": explore_epsilon,
            }},
        )
        self._min_samples = min_samples_for_attribution
        self._epsilon = explore_epsilon
        # Injectable RNG so tests are deterministic.
        self._rng = rng or random.Random()

    def attribute(
        self,
        owl_name: str,
        current_dna: OwlDNA,
        outcomes: list[TaskOutcome],
        *,
        skill_success_rate: float | None = None,
    ) -> AttributionReport:
        """Compute trait deltas from ``outcomes``. Returns empty deltas when
        signal is insufficient — caller falls back to the LLM path.

        ``skill_success_rate`` (Story 3.4, FR-16/FR-17/AD-7) is the owl's
        average success_rate across its owned, execution-tested skills —
        an OPTIONAL advisory nudge applied to each trait's already-computed
        ``proposed_delta`` inside :meth:`_attribute_one_trait`. Default
        ``None`` preserves byte-identical pre-story behavior for any caller
        that doesn't pass it.
        """
        # 1. ENTRY
        log.engine.debug(
            "[dna] attributor.attribute: entry",
            extra={"_fields": {
                "owl_name": owl_name, "n_outcomes": len(outcomes),
                "skill_success_rate": skill_success_rate,
            }},
        )
        # 2. DECISION — too few scored outcomes. POSITIVE-ONLY LEARNING (operator
        # directive): tune traits from SUCCESSFUL outcomes only, so the bands
        # reflect what worked — never which configuration failed.
        #
        # F-54 (ACCEPTED-BY-DIRECTIVE): an audit suggested PENALIZING trait bands
        # with high failure rates. That is *negative* learning and is rejected on
        # purpose — see feedback_positive_only_learning ("remember wins, never
        # failures"). Failed / penalized outcomes (success=False or any
        # failure_class, incl. "unachieved_effect") are dropped here and never
        # mined, even when they carry a quality_score + dna_snapshot. Deliver-time
        # honesty still admits a live failure; this filter only governs what the
        # evolver LEARNS FROM. Do NOT add failure-based attribution.
        scored = _filter_scored_outcomes(outcomes)
        if len(scored) < self._min_samples:
            log.engine.debug(
                "[dna] attributor.attribute: exit — below sample threshold",
                extra={"_fields": {
                    "owl_name": owl_name, "scored": len(scored),
                    "min_required": self._min_samples,
                }},
            )
            return AttributionReport(
                owl_name=owl_name, n_scored_outcomes=len(scored),
                deltas={}, per_trait=(),
                explore_fired=False, explore_trait=None,
                fallback_reason=(
                    f"only {len(scored)} scored outcomes with dna_snapshot "
                    f"(need ≥{self._min_samples})"
                ),
            )
        # 3. STEP — per-trait band analysis
        per_trait_reports: list[TraitAttribution] = []
        deltas: dict[str, float] = {}
        for trait in _MUTABLE_TRAITS:
            attr = self._attribute_one_trait(
                trait, current_dna, scored, skill_success_rate=skill_success_rate,
            )
            per_trait_reports.append(attr)
            if attr.proposed_delta != 0.0:
                deltas[trait] = attr.proposed_delta
        # 2. DECISION — explore margin overrides one slot per epoch
        explore_fired = False
        explore_trait: str | None = None
        if self._rng.random() < self._epsilon:
            explore_trait = self._rng.choice(_MUTABLE_TRAITS)
            explore_delta = self._rng.uniform(-_EXPLORE_DELTA_BOUND, _EXPLORE_DELTA_BOUND)
            deltas[explore_trait] = explore_delta
            explore_fired = True
            log.engine.info(
                "[dna] attributor.attribute: explore margin fired",
                extra={"_fields": {
                    "owl_name": owl_name, "trait": explore_trait,
                    "delta": round(explore_delta, 4),
                }},
            )
        # 2. DECISION — nothing to propose AND no explore → tell caller to fallback
        fallback_reason: str | None = None
        if not deltas:
            fallback_reason = (
                "no trait band gap exceeded threshold across any trait "
                f"(scanned {len(_MUTABLE_TRAITS)} traits over {len(scored)} samples)"
            )
        # 4. EXIT
        report = AttributionReport(
            owl_name=owl_name, n_scored_outcomes=len(scored),
            deltas={k: max(-_MAX_DELTA_PER_EPOCH,
                            min(_MAX_DELTA_PER_EPOCH, v))
                    for k, v in deltas.items()},
            per_trait=tuple(per_trait_reports),
            explore_fired=explore_fired, explore_trait=explore_trait,
            fallback_reason=fallback_reason,
        )
        log.engine.info(
            "[dna] attributor.attribute: exit",
            extra={"_fields": {
                "owl_name": owl_name,
                "n_scored": len(scored),
                "proposed_traits": list(report.deltas.keys()),
                "explore_fired": explore_fired,
                "fallback_reason": fallback_reason,
            }},
        )
        return report

    def _attribute_one_trait(
        self, trait: str, current_dna: OwlDNA, scored: list[TaskOutcome],
        *, skill_success_rate: float | None = None,
    ) -> TraitAttribution:
        """Bucket samples for one trait, propose a delta toward the winning band.

        Returns ``proposed_delta == 0.0`` when no qualifying gap exists.

        Story 3.4 (FR-16/FR-17/AD-7): when a non-zero delta IS proposed and
        ``skill_success_rate`` is given, apply a bounded advisory multiplier
        ``0.85 + 0.3 * skill_success_rate`` ∈ [0.85, 1.15] — a ±15% nudge on
        the magnitude of a decision the band analysis already made. Never
        applied to a ``0.0`` delta (``0.0 * anything == 0.0`` — a nudge can't
        manufacture signal from nothing), never sign-flipping, never a new
        veto/gate of its own.
        """
        # 1. ENTRY
        log.engine.debug(
            "[dna] attributor._attribute_one_trait: entry",
            extra={"_fields": {"trait": trait, "n_scored": len(scored)}},
        )
        # 3. STEP — bucket by band. ONE POLICY: the edges computed here also
        # supply the steering target and the hold-check below. Splitting them
        # (re-banding the samples while steering at a fixed constant) would
        # disable the "already in the winning band" brake and aim every proposal
        # at a value the envelope cannot reach — a monotone ratchet that logs
        # like healthy learning, one +_STEP_SIZE per epoch, forever.
        pairs = [
            (float(v), float(o.quality_score or 0.0))
            for o in scored
            if (v := o.dna_snapshot.get(trait)) is not None
        ]
        edges = _band_edges([v for v, _ in pairs])
        if edges is None:
            observed = (
                f"{max(v for v, _ in pairs) - min(v for v, _ in pairs):.3f}"
                if pairs else "0.000"
            )
            log.engine.debug(
                "[dna] attributor._attribute_one_trait: exit — trait never varied",
                extra={"_fields": {"trait": trait, "observed_spread": observed}},
            )
            return TraitAttribution(
                trait=trait, bands=(), proposed_delta=0.0,
                rationale=(
                    f"trait varied by only {observed} across {len(pairs)} samples "
                    f"(need ≥{_MIN_TRAIT_SPREAD}) — nothing to attribute"
                ),
            )
        band_qualities: dict[str, list[float]] = defaultdict(list)
        band_values: dict[str, list[float]] = defaultdict(list)
        for v, q in pairs:
            b = _band_for(v, edges)
            band_qualities[b].append(q)
            band_values[b].append(v)
        # Compute per-band stats only where we have enough samples AND enough
        # DISTINCT SETTINGS. The second condition is the one that matters: 500
        # turns at a single trait value are 500 observations of ONE experiment,
        # and a "gap" between two such groups is the difference between eras.
        bands: list[BandStats] = []
        for band, qualities in band_qualities.items():
            values = band_values[band]
            if len(qualities) < _MIN_BAND_SAMPLES:
                continue
            if len(set(values)) < _MIN_BAND_DISTINCT_VALUES:
                log.engine.debug(
                    "[dna] attributor._attribute_one_trait: band has too few "
                    "distinct settings — not an independent sample",
                    extra={"_fields": {
                        "trait": trait, "band": band, "n_samples": len(qualities),
                        "n_distinct_values": len(set(values)),
                    }},
                )
                continue
            bands.append(BandStats(
                band=band, n_samples=len(qualities),
                mean_quality=sum(qualities) / len(qualities),
                mean_value=sum(values) / len(values),
            ))
        bands.sort(key=lambda b: b.mean_quality, reverse=True)
        # 2. DECISION — need at least two qualifying bands
        if len(bands) < 2:
            log.engine.debug(
                "[dna] attributor._attribute_one_trait: exit — <2 bands qualify",
                extra={"_fields": {"trait": trait, "n_bands": len(bands)}},
            )
            return TraitAttribution(
                trait=trait, bands=tuple(bands), proposed_delta=0.0,
                rationale=f"<2 bands met sample threshold (have {len(bands)})",
            )
        best = bands[0]
        worst = bands[-1]
        gap = best.mean_quality - worst.mean_quality
        # 2. DECISION — gap too small
        if gap < _MIN_BAND_GAP:
            log.engine.debug(
                "[dna] attributor._attribute_one_trait: exit — gap too small",
                extra={"_fields": {"trait": trait, "gap": round(gap, 3)}},
            )
            return TraitAttribution(
                trait=trait, bands=tuple(bands), proposed_delta=0.0,
                rationale=(
                    f"best({best.band})={best.mean_quality:.2f} vs "
                    f"worst({worst.band})={worst.mean_quality:.2f}, "
                    f"gap {gap:.2f} < {_MIN_BAND_GAP}"
                ),
            )
        # 3. STEP — direction from current value toward where the winning samples
        # ACTUALLY SIT. This used to aim at _BAND_CENTERS[best.band] — a constant
        # 0.15/0.5/0.85 — and 0.85 is outside the governor's anchor ± 0.30
        # envelope, so "steer toward the winner" meant "steer at an unreachable
        # point", which is a direction that never stops pointing the same way.
        current_value = float(getattr(current_dna, trait))
        target = best.mean_value
        direction = 1.0 if target > current_value else -1.0
        delta = direction * _STEP_SIZE
        # If current is already in the winning band, propose 0 (don't drift).
        # Uses the SAME edges the bands were built from — the brake and the
        # bucketing must never disagree about what "the winning band" means.
        if _band_for(current_value, edges) == best.band:
            log.engine.debug(
                "[dna] attributor._attribute_one_trait: exit — already in best band",
                extra={"_fields": {
                    "trait": trait, "current": current_value, "band": best.band,
                }},
            )
            return TraitAttribution(
                trait=trait, bands=tuple(bands), proposed_delta=0.0,
                rationale=(
                    f"already in best band ({best.band}); "
                    f"holding at {current_value:.2f}"
                ),
            )
        # 3. STEP — Story 3.4 advisory nudge, applied only to this non-zero
        # proposed_delta (see method docstring for the bound + rationale).
        effective_delta = delta
        nudge_applied = False
        if skill_success_rate is not None:
            multiplier = 0.85 + 0.3 * skill_success_rate
            effective_delta = delta * multiplier
            nudge_applied = True
        # 4. EXIT
        log.engine.info(
            "[dna] attributor._attribute_one_trait: exit — proposing delta",
            extra={"_fields": {
                "trait": trait, "current": round(current_value, 3),
                "best_band": best.band, "best_mean": round(best.mean_quality, 3),
                "worst_band": worst.band, "worst_mean": round(worst.mean_quality, 3),
                "gap": round(gap, 3), "delta": round(effective_delta, 3),
                "skill_success_rate": skill_success_rate, "nudge_applied": nudge_applied,
            }},
        )
        return TraitAttribution(
            trait=trait, bands=tuple(bands), proposed_delta=effective_delta,
            rationale=(
                f"best band {best.band} (mean={best.mean_quality:.2f}, "
                f"n={best.n_samples}) beats {worst.band} "
                f"(mean={worst.mean_quality:.2f}, n={worst.n_samples}) "
                f"by {gap:.2f}; nudge {effective_delta:+.2f}"
            ),
        )


def lookback_epoch(lookback_days: int = _DEFAULT_LOOKBACK_DAYS) -> float:
    """Convenience: ``since_epoch`` value for ``list_scored_for_owl`` queries."""
    return time.time() - lookback_days * _SECONDS_PER_DAY
