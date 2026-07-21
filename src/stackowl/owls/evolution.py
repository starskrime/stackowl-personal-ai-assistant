"""DNA evolution — coordinator and delta validator (Story 4.3).

The ``EvolutionPromptBuilder`` lives in ``evolution_prompt.py`` to keep this
module within the 300-line cap. Checkpoint/restore for promotion goes through
``LearningArtifactStore`` (learning_artifact_store.py, Story 2.3) — the trio
below still forms the LLM-driven mutation pipeline, re-exported here for
caller convenience::

    EvolutionPromptBuilder → ModelProvider → DeltaValidator → mutate
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.exceptions import TransientError
from stackowl.infra.observability import log
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.concurrency import ConcurrencyGovernor
from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.dna_attribution import (
    AttributionReport,
    DnaAttributor,
    lookback_epoch,
)
from stackowl.owls.dna_authored import read_authored_dna
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.owls.dna_governor import SignalStrength, bound_dna
from stackowl.owls.dna_hydrator import apply_dna_overlay
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.evolution_prompt import EvolutionPromptBuilder
from stackowl.owls.learning_artifact_store import LearningArtifactStore
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.shadow_validator import ShadowValidator
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.skills.store import SkillIndexStore

__all__ = [
    "DeltaValidator",
    "EvolutionCoordinator",
    "EvolutionPromptBuilder",
]

_DELTA_LOWER = -0.25
_DELTA_UPPER = 0.25
# PARL-7 (F084) — bound for a single owl's evolution (attribution query + optional
# LLM fallback + DB writes). Generous, since a real LLM fallback can be slow on a
# weak host, but finite so one stuck owl can't wedge the nightly batch.
EVOLUTION_PER_OWL_TIMEOUT_SECONDS = 120.0
# F-55 — per-owl transient recovery. A timeout / network blip / rate-limit used
# to drop the owl outright, silently no-op'ing its evolution until the next
# nightly batch. Retry exactly once after a small backoff before giving up; an
# owl still failing after the retry is surfaced for follow-up. Batch-isolation
# is untouched — one owl's failure never propagates out of _evolve_one_bounded.
_EVOLUTION_MAX_ATTEMPTS = 2  # original attempt + one retry on transient failure
_EVOLUTION_RETRY_BACKOFF_SECONDS = 1.0

# Design decision 3 — per-owl evolution aggressiveness. Scales the FINALIZED
# per-trait deltas before they are applied: conservative halves drift,
# experimental doubles it, adaptive (the default) is unchanged. bound_dna still
# clamps the resulting DNA, so experimental can never breach the safe governor
# band — this only tunes how fast the owl moves within it.
_EVOLUTION_STRATEGY_FACTOR: dict[str, float] = {
    "conservative": 0.5,
    "adaptive": 1.0,
    "experimental": 2.0,
}


def _scale_deltas(deltas: dict[str, float], strategy: str) -> dict[str, float]:
    """Scale each trait delta by the owl's evolution strategy. Returns the input
    unchanged (same object) for the 1× / unknown-strategy case (no allocation)."""
    factor = _EVOLUTION_STRATEGY_FACTOR.get(strategy, 1.0)
    if factor == 1.0:
        return deltas
    return {trait: delta * factor for trait, delta in deltas.items()}


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.UNICODE)

_FETCH_EXCERPTS_SQL = """
SELECT m.content
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.owl_name = ?
ORDER BY m.created_at DESC
LIMIT ?
"""


class DeltaValidator:
    """Validate and parse LLM-suggested trait deltas."""

    _TRAITS: frozenset[str] = frozenset(TRAIT_NAMES)

    def validate(self, raw: str) -> dict[str, float]:
        """Parse ``raw`` (LLM response) and return ``{trait: delta}`` for valid entries."""
        log.engine.debug(
            "[dna] validator.validate: entry",
            extra={"_fields": {"raw_len": len(raw)}},
        )
        payload = self._extract_json(raw)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            log.engine.warning(
                "[dna] validator.validate: payload is not an object",
                extra={"_fields": {"type": type(payload).__name__}},
            )
            return {}
        result: dict[str, float] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or key not in self._TRAITS:
                log.engine.warning(
                    "[dna] validator.validate: unknown or non-string trait — skipping",
                    extra={"_fields": {"key": str(key)}},
                )
                continue
            try:
                delta = float(value)
            except (TypeError, ValueError):
                log.engine.warning(
                    "[dna] validator.validate: non-float value — skipping",
                    extra={"_fields": {"trait": key, "value_type": type(value).__name__}},
                )
                continue
            clamped = max(_DELTA_LOWER, min(_DELTA_UPPER, delta))
            result[key] = clamped
        log.engine.debug(
            "[dna] validator.validate: exit",
            extra={"_fields": {"valid_deltas": len(result)}},
        )
        return result

    def _extract_json(self, raw: str) -> Any:
        """Pull a JSON object out of ``raw`` — tolerant of markdown code fences."""
        text = raw.strip()
        match = _FENCE_RE.search(text)
        candidate = match.group(1).strip() if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            log.engine.warning(
                "[dna] validator.validate: JSON unparseable",
                extra={"_fields": {"snippet": candidate[:120], "error": str(exc)}},
            )
            return None


class EvolutionCoordinator(JobHandler):
    """Run DNA evolution batch as a scheduled job."""

    def __init__(
        self,
        db: DbPool,
        provider_registry: ProviderRegistry,
        owl_registry: OwlRegistry,
        evolution_batch_size: int = 10,
        attributor: DnaAttributor | None = None,
        per_owl_timeout_s: float = EVOLUTION_PER_OWL_TIMEOUT_SECONDS,
        delegation_governor: ConcurrencyGovernor | None = None,
        shadow_validator: ShadowValidator | None = None,
        kuzu: KuzuAdapter | None = None,
    ) -> None:
        self._db = db
        self._provider_registry = provider_registry
        self._owl_registry = owl_registry
        self._batch_size = max(1, evolution_batch_size)
        self._prompt_builder = EvolutionPromptBuilder()
        self._validator = DeltaValidator()
        self._learning_store = LearningArtifactStore(db)
        # Story 2.6 (FR-8, AD-1, AD-3) — the shadow-validation gate sits between
        # checkpoint and persist for every promotion. Injectable so tests can
        # substitute a deterministic stub; every PRODUCTION caller gets Story
        # 2.5's module-level defaults (AD-3 "single shared config, not
        # per-caller") — never a custom n_consecutive_required/sample_size here.
        self._shadow_validator = shadow_validator or ShadowValidator(db, provider_registry)
        # PARL-7 (F084) — bound each owl's evolution and run the batch CONCURRENTLY
        # under the shared in-flight governor, so one stuck owl (e.g. a hung LLM
        # fallback call) cannot stall the whole nightly batch.
        self._per_owl_timeout_s = per_owl_timeout_s
        self._governor = delegation_governor
        # Learning Commit 4 — attribution-based evolution. Injectable so tests
        # can supply a deterministic RNG; production gets the default
        # (10% explore margin, 20-sample threshold per operator vote).
        self._attributor = attributor or DnaAttributor()
        self._outcome_store = TaskOutcomeStore(db)
        # Story 3.4 (FR-16/FR-17) — owned-skill success_rate lookup for the
        # attribution nudge. DnaAttributor itself stays DB-free (pure logic);
        # this store lives here, next to its one caller (_try_attribution).
        self._skill_store = SkillIndexStore(db)
        # Dynamic-injection arc, sub-project 1 — best-effort graph mirror of DNA
        # trait state. None (no graph wired) degrades to exactly today's
        # behavior; a Kuzu failure at sync time never affects the durable persist.
        self._kuzu = kuzu
        # Race guard — the nightly batch (_evolve_one) and the inline evolve_now
        # tool both read-modify-write the SAME owl's DNA through
        # _checkpoint_validate_and_promote with no lock and no version column
        # (upsert_owl_dna does a plain ON CONFLICT overwrite). Without
        # serialization, a batch cycle and an inline evolve_now landing on the
        # same owl at once is a lost-update: whichever promotes last silently
        # discards the other's mutation, and a rejected-gate restore can even
        # revert the OTHER call's already-promoted change back to a stale
        # snapshot. One lock per owl name, created lazily (safe under asyncio's
        # single-threaded cooperative scheduling — no await between the
        # dict lookup and insert).
        self._owl_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, owl_name: str) -> asyncio.Lock:
        return self._owl_locks.setdefault(owl_name, asyncio.Lock())

    @property
    def handler_name(self) -> str:
        return "evolution_batch"

    async def execute(self, job: Job) -> JobResult:
        """Run evolution for every owl with enough conversation turns."""
        log.engine.info(
            "[dna] coordinator.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "batch_size": self._batch_size}},
        )
        t0 = time.monotonic()
        mutated_owls: list[str] = []
        skipped_owls: list[str] = []
        stuck_owls: list[str] = []
        try:
            manifests = list(self._owl_registry.list())
            # PARL-7 (F084) — evolve owls CONCURRENTLY, each bounded by a per-owl
            # timeout under the shared governor. A hung owl times out (recorded as
            # stuck) without blocking the rest; return_exceptions keeps one owl's
            # failure from cancelling its siblings.
            results = await asyncio.gather(
                *(self._evolve_one_bounded(m) for m in manifests),
                return_exceptions=True,
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.engine.error(
                "[dna] coordinator.execute: batch failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )
        for manifest, outcome in zip(manifests, results, strict=True):
            if isinstance(outcome, BaseException):
                # A per-owl crash (not a timeout) — logged in the helper; counted
                # as stuck so the batch result stays honest without failing.
                stuck_owls.append(manifest.name)
            elif outcome is None:
                stuck_owls.append(manifest.name)  # timed out
            elif outcome:
                mutated_owls.append(manifest.name)
            else:
                skipped_owls.append(manifest.name)
        duration_ms = (time.monotonic() - t0) * 1000
        output = (
            f"mutated={len(mutated_owls)} skipped={len(skipped_owls)} "
            f"stuck={len(stuck_owls)}"
        )
        log.engine.info(
            "[dna] coordinator.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "mutated": mutated_owls,
                    "skipped": skipped_owls,
                    "stuck": stuck_owls,
                    "duration_ms": duration_ms,
                }
            },
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=output,
            error=None,
            duration_ms=duration_ms,
        )

    async def _evolve_one(self, manifest: OwlAgentManifest) -> bool:
        """Serialize this owl's evolution against any concurrent evolve_now call.

        Re-fetches the manifest AFTER acquiring the lock — if a concurrent
        mutation landed while this call was waiting, the batch's checkpoint/
        clamp/persist must start from the live post-mutation state, not the
        stale snapshot ``execute()`` captured before the wait.
        """
        async with self._lock_for(manifest.name):
            manifest = self._owl_registry.get(manifest.name)
            return await self._evolve_one_locked(manifest)

    async def _evolve_one_locked(self, manifest: OwlAgentManifest) -> bool:
        """Evolve a single owl. Returns ``True`` if any mutation was applied.

        Two-stage decision per Learning Commit 4 (operator vote):
        1. Try the attribution path first — query scored outcomes for this owl,
           bucket by trait band, propose deltas toward winning bands.
        2. If attribution returns no deltas (cold-start or no signal gap), fall
           through to the LLM path with a stats summary embedded in the prompt.
        """
        # 1. ENTRY
        log.engine.debug(
            "[dna] coordinator.evolve_one: entry",
            extra={"_fields": {"owl": manifest.name}},
        )
        # 2. DECISION — attribution path
        attribution = await self._try_attribution(manifest)
        deltas: dict[str, float]
        evolution_source: str
        if attribution.deltas:
            deltas = attribution.deltas
            evolution_source = (
                "attribution+explore" if attribution.explore_fired else "attribution"
            )
            log.engine.info(
                "[dna] coordinator.evolve_one: using attribution deltas",
                extra={"_fields": {
                    "owl": manifest.name, "source": evolution_source,
                    "n_scored": attribution.n_scored_outcomes,
                    "deltas": deltas,
                }},
            )
        else:
            # 2. DECISION — fallback to LLM path with stats summary
            log.engine.debug(
                "[dna] coordinator.evolve_one: attribution silent — LLM fallback",
                extra={"_fields": {
                    "owl": manifest.name,
                    "fallback_reason": attribution.fallback_reason,
                }},
            )
            deltas = await self._llm_fallback(manifest, attribution)
            evolution_source = "llm_fallback"
        promoted = False
        if not deltas:
            log.engine.warning(
                "[dna] coordinator.evolve_one: no deltas from any path — skip",
                extra={"_fields": {"owl": manifest.name}},
            )
        else:
            # Apply the owl's evolution strategy to the finalized deltas (single
            # chokepoint — uniform whether the deltas came from attribution or LLM).
            deltas = _scale_deltas(deltas, manifest.evolution_strategy)
            log.engine.debug(
                "[dna] coordinator.evolve_one: deltas scaled by evolution strategy",
                extra={"_fields": {
                    "owl": manifest.name, "strategy": manifest.evolution_strategy,
                    "n_deltas": len(deltas),
                }},
            )
            # 3. STEP — apply mutations (checkpoint/clamp/gate/persist all live in
            # _checkpoint_validate_and_promote — Story 2.6, AD-3's ONE promotion fn)
            new_dna = manifest.dna
            for trait, delta in deltas.items():
                previous = float(getattr(new_dna, trait))
                try:
                    new_dna = new_dna.mutate(trait, delta)
                except Exception as exc:  # B5
                    log.engine.warning(
                        "[dna] coordinator.evolve_one: mutate rejected — skipping trait",
                        exc_info=exc,
                        extra={"_fields": {
                            "owl": manifest.name, "trait": trait, "delta": delta,
                        }},
                    )
                    continue
                current = float(getattr(new_dna, trait))
                log.engine.info(
                    "[dna] %s: %s %.3f → %.3f (delta %+.3f, src=%s)",
                    manifest.name, trait, previous, current, delta, evolution_source,
                )
            # Story 2.4 (FR-6/AD-4) — tag the batch's effective delta with how
            # strong the signal behind it is. "attribution"/"attribution+explore"
            # both come from DnaAttributor's verified-outcome path (VERIFIED);
            # "llm_fallback" has no TaskOutcome backing at all (LLM_QUALITY,
            # scaled down).
            signal = (
                SignalStrength.VERIFIED
                if evolution_source.startswith("attribution")
                else SignalStrength.LLM_QUALITY
            )
            promoted = await self._checkpoint_validate_and_promote(
                manifest, new_dna, evolution_source=evolution_source, signal=signal,
            )

        # Story 3.3 (FR-15/AD-6) — decay every trait NOT touched by this
        # cycle's deltas back toward its authored baseline. Re-fetch from the
        # registry first: the promotion attempt above may have overlaid a new
        # DNA onto the live registry (or restored the original on a gate
        # rejection) via apply_dna_overlay — decay must operate on the
        # registry's TRUE current state, not the stale local `manifest`.
        # Decay's own True/False is intentionally NOT folded into `promoted`
        # (execute()'s mutated=/skipped= bookkeeping tracks only the main
        # deltas-based result) — decay's outcome is visible via its own
        # evolution_source="decay" checkpoint + the [owls] evolution.delta log.
        current_manifest = self._owl_registry.get(manifest.name)
        await self._apply_decay(current_manifest, reinforced_traits=frozenset(deltas.keys()))

        # 4. EXIT
        log.engine.info(
            "[dna] coordinator.evolve_one: exit",
            extra={"_fields": {
                "owl": manifest.name, "source": evolution_source,
                "mutated_traits": list(deltas.keys()),
                "explore_fired": attribution.explore_fired,
                "promoted": promoted,
            }},
        )
        return promoted

    async def _apply_decay(
        self, manifest: OwlAgentManifest, reinforced_traits: frozenset[str],
    ) -> bool:
        """Drift every unreinforced trait toward its authored anchor (Story 3.3,
        FR-15/AD-6). "Unreinforced" == not a key in ``reinforced_traits`` (this
        cycle's proposed ``deltas``) — no new persisted "last touched" state is
        needed since ``deltas`` is already recomputed fresh every batch cycle.

        ``decay_rate_per_week`` is a WEEKLY rate, but this job runs DAILY
        (``evolution_batch``, seeded 02:00 UTC) — applying
        ``decay_rate_per_week / 7`` per run is the simplest correct reading of
        a weekly rate on a daily cadence (exponential decay toward the anchor,
        compounding day over day). This is intentional, not a bug.

        Returns ``True`` if a decay mutation was promoted to live, ``False``
        if there was nothing to decay this cycle (every trait reinforced, or
        every unreinforced trait already sits at its anchor).
        """
        # 1. ENTRY
        log.owls.debug(
            "[dna] coordinator._apply_decay: entry",
            extra={"_fields": {
                "owl": manifest.name, "n_reinforced": len(reinforced_traits),
            }},
        )
        anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()
        daily_fraction = manifest.dna.decay_rate_per_week / 7
        # 2. DECISION — which traits actually need to move.
        updates: dict[str, float] = {}
        for trait in _MUTABLE_TRAITS:
            if trait in reinforced_traits:
                continue  # AC #2 — genuinely reinforced traits are never decayed
            current = float(getattr(manifest.dna, trait))
            anchor_value = float(getattr(anchor, trait))
            if current == anchor_value:
                continue  # already at baseline — nothing to decay
            updates[trait] = current + (anchor_value - current) * daily_fraction
        decayed_dna = manifest.dna.model_copy(update=updates)
        if decayed_dna == manifest.dna:
            # 4. EXIT — nothing changed (everything reinforced, everything
            # already at baseline, or decay_rate_per_week == 0). No pointless
            # checkpoint/gate cycle for a no-op.
            log.owls.debug(
                "[dna] coordinator._apply_decay: exit — nothing to decay",
                extra={"_fields": {"owl": manifest.name}},
            )
            return False
        # 3. STEP — same gated promotion path as any other mutation (AD-1/AD-6).
        # SignalStrength.VERIFIED here is NOT a claim that decay is a
        # "verified outcome" in the usual sense — it's chosen because its
        # multiplier is 1.0 (no extra scaling). decay_rate_per_week / 7 is
        # already the fully-configured step size; layering a SECOND scaling
        # factor on top (as OUTCOME_BINARY/LLM_QUALITY would) would silently
        # under-decay relative to the operator's configured rate.
        promoted = await self._checkpoint_validate_and_promote(
            manifest, decayed_dna, evolution_source="decay", signal=SignalStrength.VERIFIED,
        )
        # 4. EXIT
        log.owls.info(
            "[dna] coordinator._apply_decay: exit",
            extra={"_fields": {
                "owl": manifest.name,
                "decayed_traits": list(updates.keys()),
                "promoted": promoted,
            }},
        )
        return promoted

    async def evolve_one_owl_now(self, owl_name: str) -> bool:
        """On-demand, single-task evolution trigger (Story 3.1, FR-12/FR-13/AD-5).

        Serialized against a concurrent nightly-batch cycle on the SAME owl
        via ``self._lock_for`` — see :meth:`_evolve_one` for why (lost-update
        on the shared DNA row otherwise).

        A GENUINELY SEPARATE code path from :meth:`_evolve_one`'s attribution-
        first branch — it never calls ``self._try_attribution``/``self._attributor``,
        not even via a flag that happens to skip it today. DnaAttributor's
        statistical path requires >=20 scored outcomes, a bar a single just-
        finished task can never meet, so this path forces the LLM-fallback
        (``_llm_fallback``, reused UNCHANGED) unconditionally — AD-5's "never
        branches on DnaAttributor's sample count" is true by construction.

        Reuses ``_checkpoint_validate_and_promote`` — Story 2.6's ONE promotion
        function (AD-1/AD-3) — so this path is gated by the shadow-validation
        gate from the moment it exists, exactly like the nightly batch.

        Returns ``True`` if the mutation was promoted to live, ``False`` if no
        deltas were proposed (not enough conversation material yet) or the
        shadow gate rejected the mutation (both normal, non-error outcomes).
        """
        async with self._lock_for(owl_name):
            return await self._evolve_one_owl_now_locked(owl_name)

    async def _evolve_one_owl_now_locked(self, owl_name: str) -> bool:
        # 1. ENTRY
        log.owls.debug(
            "[dna] coordinator.evolve_one_owl_now: entry",
            extra={"_fields": {"owl": owl_name}},
        )
        # manifest lookup raises OwlNotFoundError on an unknown owl — let it
        # propagate (matches this codebase's existing convention, e.g.
        # commands/owls_command.py's `_dna_restore`). Fetched AFTER acquiring
        # the lock so a concurrent batch mutation that just landed is visible.
        manifest = self._owl_registry.get(owl_name)

        # 2. DECISION — force the LLM-fallback path unconditionally. This
        # AttributionReport is honest metadata (zero attribution samples WERE
        # consulted, by design), not a fake/misleading report.
        attribution = AttributionReport(
            owl_name=owl_name, n_scored_outcomes=0, deltas={}, per_trait=(),
            explore_fired=False, explore_trait=None,
            fallback_reason="evolve_now: single-task trigger, forced LLM-fallback path (FR-13/AD-5)",
        )
        deltas = await self._llm_fallback(manifest, attribution)
        if not deltas:
            log.owls.info(
                "[dna] coordinator.evolve_one_owl_now: no deltas proposed — skip",
                extra={"_fields": {"owl": owl_name}},
            )
            return False

        # 3. STEP — scale by strategy, mutate, promote through the ONE shared
        # gate (same shape as _evolve_one's mutation loop).
        deltas = _scale_deltas(deltas, manifest.evolution_strategy)
        new_dna = manifest.dna
        for trait, delta in deltas.items():
            try:
                new_dna = new_dna.mutate(trait, delta)
            except Exception as exc:  # B5
                log.owls.warning(
                    "[dna] coordinator.evolve_one_owl_now: mutate rejected — skipping trait",
                    exc_info=exc,
                    extra={"_fields": {"owl": owl_name, "trait": trait, "delta": delta}},
                )
                continue
        promoted = await self._checkpoint_validate_and_promote(
            manifest, new_dna, evolution_source="evolve_now", signal=SignalStrength.LLM_QUALITY,
        )
        # 4. EXIT
        log.owls.info(
            "[dna] coordinator.evolve_one_owl_now: exit",
            extra={"_fields": {
                "owl": owl_name, "mutated_traits": list(deltas.keys()), "promoted": promoted,
            }},
        )
        return promoted

    async def _checkpoint_validate_and_promote(
        self,
        manifest: OwlAgentManifest,
        new_dna: OwlDNA,
        *,
        evolution_source: str,
        signal: SignalStrength,
    ) -> bool:
        """THE single promotion path (AD-3): checkpoint -> clamp -> shadow-validate
        -> commit-or-restore -> observe. ``new_dna`` is the RAW mutated DNA
        (pre-governor-clamp) — clamping happens here, before the gate, so the
        gate validates what would ACTUALLY ship.

        Returns ``True`` if the mutation was promoted to live, ``False`` if the
        gate rejected it (no-op — the pre-mutation checkpoint is restored).

        This is the ONE promotion function in the codebase (AD-3) — both the
        nightly batch (``_evolve_one``, this story) and Story 3.1's
        ``evolve_one_owl_now`` call this same method. Do not duplicate
        checkpoint/gate/persist/restore logic anywhere else.
        """
        # 1. ENTRY
        log.engine.debug(
            "[dna] coordinator.promote: entry",
            extra={"_fields": {"owl": manifest.name, "source": evolution_source}},
        )
        # 3. STEP — checkpoint + clamp (AD-2: unified LearningArtifactStore
        # primitive supersedes DNACheckpointer — Story 2.3)
        checkpoint_id = await self._learning_store.checkpoint(
            "dna", manifest.name, manifest.dna.model_dump(), reason=evolution_source,
        )
        anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()
        safe_dna = bound_dna(manifest.dna, new_dna, anchor, signal=signal)  # governor: clamp once

        # 2. DECISION — the shadow gate (FR-8/AD-1/AD-3): persist only after N
        # consecutive non-regressions on a held-out replay of real interactions.
        result = await self._shadow_validator.validate(manifest.name, manifest, safe_dna)
        if not result.passed:
            # Story 2.7 (AC #1) — ERROR, not WARNING: a gate rejection must be
            # visible without a human specifically going looking for it. The
            # message text is held stable/greppable (never varied between
            # calls) so `jq 'select(.msg == "...")'` reliably counts every
            # rejection; `failures` carries Story 2.5's per-replay detail
            # (truncated per this repo's sensitive-data convention) so "the
            # specific non-regression that failed" is IN the record, not just
            # a count.
            log.owls.error(
                "[dna] coordinator.promote: shadow gate REJECTED",
                extra={"_fields": {
                    "owl": manifest.name,
                    "checkpoint_id": checkpoint_id,
                    "n_replayed": result.n_replayed,
                    "consecutive_non_regressions": result.consecutive_non_regressions,
                    "n_consecutive_required": self._shadow_validator.n_consecutive_required,
                    "failures": [
                        {**f, "input_text": str(f.get("input_text", ""))[:200]}
                        for f in result.failures
                    ],
                }},
            )
            # FR-10 — restore-and-reaffirm is a STRUCTURAL guarantee, not a no-op
            # skipped just because today's call ordering happens to make it
            # redundant (persist never ran pre-gate). See Story 2.6 Dev Notes:
            # don't "optimize" this away — it's the safety net regardless of
            # what a future refactor changes about the ordering above.
            restored_payload = await self._learning_store.restore(
                "dna", manifest.name, checkpoint_id,
            )
            restored_dna = OwlDNA.model_validate(restored_payload)
            await self._persist_dna(manifest.name, restored_dna)
            apply_dna_overlay(self._owl_registry, manifest.name, restored_dna)
            # 4. EXIT
            log.engine.info(
                "[dna] coordinator.promote: exit — rejected",
                extra={"_fields": {"owl": manifest.name, "checkpoint_id": checkpoint_id}},
            )
            return False

        await self._persist_dna(manifest.name, safe_dna)                # DB = source of truth (persist FIRST)
        apply_dna_overlay(self._owl_registry, manifest.name, safe_dna)  # live refresh (next turn sees it)
        for trait in _MUTABLE_TRAITS:                                    # audit (drift detectable + reversible)
            old_val = float(getattr(manifest.dna, trait))
            new_val = float(getattr(safe_dna, trait))
            if old_val != new_val:
                log.engine.info(
                    "[owls] evolution.delta",
                    extra={"_fields": {
                        "owl": manifest.name,
                        "trait": trait,
                        "old": old_val,
                        "new": new_val,
                        "delta": round(new_val - old_val, 4),
                        "source": evolution_source,
                    }},
                )
        # 4. EXIT
        log.engine.info(
            "[dna] coordinator.promote: exit — promoted",
            extra={"_fields": {
                "owl": manifest.name, "checkpoint_id": checkpoint_id, "source": evolution_source,
            }},
        )
        return True

    async def _evolve_one_bounded(self, manifest: OwlAgentManifest) -> bool | None:
        """Evolve one owl under the governor + a per-owl timeout (PARL-7 / F084).

        Returns ``True``/``False`` from :meth:`_evolve_one`, or ``None`` if the
        owl could not be evolved — never propagated, so a single hung/crashed
        owl cannot stall or fail the whole nightly batch (batch-isolation).

        F-55 — TRANSIENT failures (timeout, network blip, rate-limit) get one
        bounded retry after a small backoff before the owl is dropped; an owl
        still failing after the retry is surfaced at WARNING with a clear
        ``evolution.stuck_owl`` marker for follow-up. Non-transient crashes are
        dropped on the first failure (no auto-retry).
        """
        for attempt in range(_EVOLUTION_MAX_ATTEMPTS):
            is_last = attempt == _EVOLUTION_MAX_ATTEMPTS - 1
            try:
                return await self._evolve_one_attempt(manifest)
            except (TimeoutError, TransientError) as exc:  # transient → recoverable
                kind = "timeout" if isinstance(exc, TimeoutError) else "transient"
                if not is_last:
                    # DECISION — transient and budget left: back off, retry once.
                    log.engine.info(
                        "[dna] coordinator._evolve_one_bounded: transient failure — retrying once",
                        extra={"_fields": {
                            "owl": manifest.name, "kind": kind,
                            "attempt": attempt + 1,
                            "backoff_s": _EVOLUTION_RETRY_BACKOFF_SECONDS,
                        }},
                    )
                    await asyncio.sleep(_EVOLUTION_RETRY_BACKOFF_SECONDS)
                    continue
                # EXIT — still failing after the retry: surface for follow-up.
                log.engine.warning(
                    "[dna] coordinator._evolve_one_bounded: evolution.stuck_owl — "
                    "still failing after retry, needs follow-up",
                    exc_info=exc, extra={"_fields": {
                        "owl": manifest.name, "kind": kind,
                        "attempts": attempt + 1,
                        "timeout_s": self._per_owl_timeout_s,
                    }},
                )
                return None
            except Exception as exc:  # B5 — one owl's crash never sinks the batch
                log.engine.warning(
                    "[dna] coordinator._evolve_one_bounded: owl evolution failed — skipping",
                    exc_info=exc, extra={"_fields": {"owl": manifest.name}},
                )
                return None
        return None  # unreachable — loop always returns; satisfies the type-checker

    async def _evolve_one_attempt(self, manifest: OwlAgentManifest) -> bool | None:
        """One bounded evolution attempt: governor slot + per-owl timeout.

        Raises :class:`TimeoutError` on timeout (handled as transient by the
        caller); all other exceptions propagate unchanged.
        """
        if self._governor is None:
            return await asyncio.wait_for(
                self._evolve_one(manifest), timeout=self._per_owl_timeout_s
            )
        async with self._governor.slot():
            return await asyncio.wait_for(
                self._evolve_one(manifest), timeout=self._per_owl_timeout_s
            )

    async def _try_attribution(self, manifest: OwlAgentManifest) -> AttributionReport:
        """Pull scored outcomes for this owl and run the attributor.

        Returns the report unchanged so callers can read ``fallback_reason``
        and ``per_trait`` for downstream prompt construction.
        """
        log.engine.debug(
            "[dna] coordinator._try_attribution: entry",
            extra={"_fields": {"owl": manifest.name}},
        )
        try:
            outcomes = await self._outcome_store.list_scored_for_owl(
                manifest.name, since_epoch=lookback_epoch(),
            )
        except Exception as exc:  # B5
            log.engine.warning(
                "[dna] coordinator._try_attribution: list_scored_for_owl failed",
                exc_info=exc, extra={"_fields": {"owl": manifest.name}},
            )
            return AttributionReport(
                owl_name=manifest.name, n_scored_outcomes=0,
                deltas={}, per_trait=(),
                explore_fired=False, explore_trait=None,
                fallback_reason=f"outcome query failed: {exc}",
            )
        skill_success_rate = await self._owl_skill_success_rate(manifest)
        report = self._attributor.attribute(
            owl_name=manifest.name, current_dna=manifest.dna, outcomes=outcomes,
            skill_success_rate=skill_success_rate,
        )
        log.engine.debug(
            "[dna] coordinator._try_attribution: exit",
            extra={"_fields": {
                "owl": manifest.name,
                "n_outcomes": len(outcomes),
                "n_deltas": len(report.deltas),
                "skill_success_rate": skill_success_rate,
            }},
        )
        return report

    async def _owl_skill_success_rate(self, manifest: OwlAgentManifest) -> float | None:
        """Aggregate ``manifest``'s owned skills' ``success_rate`` into one
        advisory signal (Story 3.4, FR-16/FR-17). Unexecuted skills carry
        ``success_rate=None`` and are excluded from the average — never
        treated as 0. Returns ``None`` (no signal, not a fabricated default)
        when the owl owns no skills, or none have executed enough to have a
        rate yet — matches this repo's existing "None == no opinion"
        convention (``dna_attribution.py``/``classify.py``).
        """
        # 1. ENTRY
        log.engine.debug(
            "[dna] coordinator._owl_skill_success_rate: entry",
            extra={"_fields": {"owl": manifest.name, "n_owned_skills": len(manifest.skills)}},
        )
        if not manifest.skills:
            return None
        # 3. STEP — batch lookup (reuses get_many_by_name; no N+1 .get() loop)
        try:
            skills = await self._skill_store.get_many_by_name(manifest.skills)
        except Exception as exc:  # B5 — advisory-only signal, never blocks attribution
            log.engine.warning(
                "[dna] coordinator._owl_skill_success_rate: get_many_by_name failed "
                "— treating as no signal",
                exc_info=exc, extra={"_fields": {"owl": manifest.name}},
            )
            return None
        rates = [s.success_rate for s in skills if s.success_rate is not None]
        # 2. DECISION — no executed skills yet → no signal
        if not rates:
            log.engine.debug(
                "[dna] coordinator._owl_skill_success_rate: exit — no executed skills",
                extra={"_fields": {"owl": manifest.name, "n_skills_resolved": len(skills)}},
            )
            return None
        avg = sum(rates) / len(rates)
        # 4. EXIT
        log.engine.debug(
            "[dna] coordinator._owl_skill_success_rate: exit",
            extra={"_fields": {"owl": manifest.name, "n_rated": len(rates), "avg": round(avg, 4)}},
        )
        return avg

    async def _llm_fallback(
        self, manifest: OwlAgentManifest, attribution: AttributionReport,
    ) -> dict[str, float]:
        """LLM-driven evolution path with stats summary (post-Commit 4).

        Always called when attribution is silent. Now the prompt embeds the
        per-trait band rationale so the LLM has evidence to reason from rather
        than guessing on raw messages alone.
        """
        log.engine.debug(
            "[dna] coordinator._llm_fallback: entry",
            extra={"_fields": {"owl": manifest.name}},
        )
        excerpts = await self._fetch_excerpts(manifest.name)
        # 2. DECISION — also need enough conversation material; honor the old
        # batch_size gate so we don't burn LLM calls on brand-new owls.
        if len(excerpts) < self._batch_size and attribution.n_scored_outcomes == 0:
            log.engine.debug(
                "[dna] coordinator._llm_fallback: exit — no excerpts AND no outcomes",
                extra={"_fields": {"owl": manifest.name}},
            )
            return {}
        stats_summary = _attribution_to_stats_summary(attribution)
        messages = self._prompt_builder.build(
            manifest.name, manifest, excerpts, stats_summary=stats_summary,
        )
        TestModeGuard.assert_not_test_mode(f"evolution.complete[{manifest.name}]")
        try:
            provider, model = self._provider_registry.get_by_tier("fast")
            # disable_thinking: a JSON-deltas verdict needs no chain-of-thought;
            # a reasoning-capable provider otherwise burns the whole max_tokens
            # budget on <think> and never emits the deltas (same empty-reply
            # failure mode fixed in owls/router.py and every interaction/
            # *_classifier.py caller via this same flag).
            result = await provider.complete(
                messages, model=model, max_tokens=512, disable_thinking=True,
            )
        except Exception as exc:  # B5
            log.engine.warning(
                "[dna] coordinator._llm_fallback: provider call failed — skipping",
                exc_info=exc, extra={"_fields": {"owl": manifest.name}},
            )
            return {}
        deltas = self._validator.validate(result.content)
        log.engine.debug(
            "[dna] coordinator._llm_fallback: exit",
            extra={"_fields": {
                "owl": manifest.name, "n_deltas": len(deltas),
            }},
        )
        return deltas

    async def _fetch_excerpts(self, owl_name: str) -> list[str]:
        try:
            rows = await self._db.fetch_all(_FETCH_EXCERPTS_SQL, (owl_name, self._batch_size))
        except Exception as exc:  # B5 — messages table may be absent in some deployments
            log.engine.debug(
                "[dna] coordinator._fetch_excerpts: query failed — returning []",
                exc_info=exc, extra={"_fields": {"owl_name": owl_name}},
            )
            return []
        return [str(row["content"]) for row in rows if row.get("content")]


    async def _persist_dna(self, owl_name: str, dna: OwlDNA) -> None:
        await upsert_owl_dna(self._db, owl_name, dna, table="owl_dna")
        await self._sync_dna_to_graph(owl_name, dna)

    async def _sync_dna_to_graph(self, owl_name: str, dna: OwlDNA) -> None:
        """Best-effort mirror of the just-persisted DNA values into the graph.

        Never raises, never blocks the caller — a Kuzu failure here must not
        affect the already-committed durable (SQLite) persist. No-op when no
        graph is wired (``self._kuzu is None``, the default)."""
        if self._kuzu is None:
            return
        try:
            await self._kuzu.upsert_owl_node(owl_name)
            for trait_name in _MUTABLE_TRAITS:
                trait_id = f"{owl_name}::{trait_name}"
                value = float(getattr(dna, trait_name))
                await self._kuzu.upsert_trait_node(trait_id, owl_name, trait_name, value)
                await self._kuzu.link_owl_has_trait(owl_name, trait_id)
        except Exception as exc:  # noqa: BLE001 — a graph-sync failure must never break evolution
            log.engine.warning(
                "[dna] coordinator._sync_dna_to_graph: failed — graph left stale",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name}},
            )


def _attribution_to_stats_summary(report: AttributionReport) -> dict[str, object]:
    """Serialize an :class:`AttributionReport` into the prompt-stats shape.

    Kept module-level so :class:`EvolutionPromptBuilder` stays decoupled from
    the AttributionReport dataclass.
    """
    return {
        "n_scored_outcomes": report.n_scored_outcomes,
        "per_trait": [
            {
                "trait": tr.trait,
                "rationale": tr.rationale,
                "bands": [
                    {"band": b.band, "n": b.n_samples,
                     "mean_quality": round(b.mean_quality, 3)}
                    for b in tr.bands
                ],
            }
            for tr in report.per_trait
        ],
        "fallback_reason": report.fallback_reason,
    }
