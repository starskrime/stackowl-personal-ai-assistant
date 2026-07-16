"""DnaAttributor — outcome-driven DNA tuning (Learning Commit 4).

Replaces the G1 gap: instead of an LLM reading raw conversation messages and
GUESSING which trait knob to turn, we bucket the owl's scored outcomes by
trait band, compute mean quality per band, and propose a delta toward the
band that historically wins.

Algorithm (per trait, per owl):

1. For each scored outcome with a captured ``dna_snapshot``, classify its
   trait value into one of three bands: low [0.0, 0.3), mid [0.3, 0.7),
   high [0.7, 1.0].
2. For each band with ≥ :data:`_MIN_BAND_SAMPLES` outcomes, compute the
   mean ``quality_score``.
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
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES

_MUTABLE_TRAITS: tuple[str, ...] = TRAIT_NAMES

_BAND_EDGES: tuple[float, float] = (0.3, 0.7)  # < .3 = low, [.3,.7) = mid, ≥.7 = high
_BAND_NAMES: tuple[str, str, str] = ("low", "mid", "high")
_BAND_CENTERS: dict[str, float] = {"low": 0.15, "mid": NEUTRAL, "high": 0.85}

_MIN_BAND_SAMPLES = 3        # need ≥3 outcomes in a band for that mean to count
_MIN_BAND_GAP = 0.10         # winning-band quality must beat losing-band by 0.10
_STEP_SIZE = 0.05            # one evolution step nudges this much
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


def _band_for(value: float) -> str:
    if value < _BAND_EDGES[0]:
        return "low"
    if value < _BAND_EDGES[1]:
        return "mid"
    return "high"


def _filter_scored_outcomes(outcomes: list[TaskOutcome]) -> list[TaskOutcome]:
    """POSITIVE-ONLY LEARNING filter — outcomes eligible for DNA attribution.

    F-54 (ACCEPTED-BY-DIRECTIVE): tune traits from SUCCESSFUL outcomes only
    (see feedback_positive_only_learning). Failed/penalized outcomes are
    dropped even when they carry a quality_score + dna_snapshot. A user
    Dislike vote (``approach_rating == "negative"``) is excluded too — the
    turn may have technically succeeded, but the user rejected the approach,
    so it must not reinforce the trait band that produced it.
    """
    return [
        o for o in outcomes
        if o.quality_score is not None and o.dna_snapshot
        and o.success and not o.failure_class
        and o.approach_rating != "negative"
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
        # 3. STEP — bucket by band
        band_qualities: dict[str, list[float]] = defaultdict(list)
        for o in scored:
            v = o.dna_snapshot.get(trait)
            if v is None:
                continue
            band_qualities[_band_for(float(v))].append(float(o.quality_score or 0.0))
        # Compute per-band stats only where we have enough samples
        bands: list[BandStats] = []
        for band, qualities in band_qualities.items():
            if len(qualities) < _MIN_BAND_SAMPLES:
                continue
            bands.append(BandStats(
                band=band, n_samples=len(qualities),
                mean_quality=sum(qualities) / len(qualities),
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
        # 3. STEP — direction from current value toward best-band center
        current_value = float(getattr(current_dna, trait))
        target = _BAND_CENTERS[best.band]
        direction = 1.0 if target > current_value else -1.0
        delta = direction * _STEP_SIZE
        # If current is already in the winning band, propose 0 (don't drift)
        if _band_for(current_value) == best.band:
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
