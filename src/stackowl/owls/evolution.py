"""DNA evolution — coordinator and delta validator (Story 4.3).

The ``EvolutionPromptBuilder`` lives in ``evolution_prompt.py`` and
``DNACheckpointer`` in ``dna_storage.py`` to keep this module within the
300-line cap. The trio of classes still forms the LLM-driven mutation
pipeline, re-exported here for caller convenience::

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
from stackowl.infra.observability import log
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
from stackowl.owls.dna_governor import bound_dna
from stackowl.owls.dna_hydrator import apply_dna_overlay
from stackowl.owls.dna_storage import DNACheckpointer, upsert_owl_dna
from stackowl.owls.evolution_prompt import EvolutionPromptBuilder
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

__all__ = [
    "DeltaValidator",
    "EvolutionCoordinator",
    "EvolutionPromptBuilder",
]

_DELTA_LOWER = -0.1
_DELTA_UPPER = 0.1
# PARL-7 (F084) — bound for a single owl's evolution (attribution query + optional
# LLM fallback + DB writes). Generous, since a real LLM fallback can be slow on a
# weak host, but finite so one stuck owl can't wedge the nightly batch.
EVOLUTION_PER_OWL_TIMEOUT_SECONDS = 120.0
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
    ) -> None:
        self._db = db
        self._provider_registry = provider_registry
        self._owl_registry = owl_registry
        self._batch_size = max(1, evolution_batch_size)
        self._prompt_builder = EvolutionPromptBuilder()
        self._validator = DeltaValidator()
        self._checkpointer = DNACheckpointer(db)
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
            success=True,
            output=output,
            error=None,
            duration_ms=duration_ms,
        )

    async def _evolve_one(self, manifest: OwlAgentManifest) -> bool:
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
        if not deltas:
            log.engine.warning(
                "[dna] coordinator.evolve_one: no deltas from any path — skip",
                extra={"_fields": {"owl": manifest.name}},
            )
            return False
        # 3. STEP — checkpoint + apply mutations
        checkpoint_id = await self._checkpointer.checkpoint(manifest.name, manifest.dna)
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
        anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()
        safe_dna = bound_dna(manifest.dna, new_dna, anchor)       # governor: clamp once
        await self._persist_dna(manifest.name, safe_dna)          # DB = source of truth (persist FIRST)
        apply_dna_overlay(self._owl_registry, manifest.name, safe_dna)  # live refresh (next turn sees it)
        for trait in _MUTABLE_TRAITS:                              # audit (drift detectable + reversible)
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
            "[dna] coordinator.evolve_one: mutations applied",
            extra={"_fields": {
                "owl": manifest.name, "source": evolution_source,
                "checkpoint_id": checkpoint_id,
                "mutated_traits": list(deltas.keys()),
                "explore_fired": attribution.explore_fired,
            }},
        )
        return True

    async def _evolve_one_bounded(self, manifest: OwlAgentManifest) -> bool | None:
        """Evolve one owl under the governor + a per-owl timeout (PARL-7 / F084).

        Returns ``True``/``False`` from :meth:`_evolve_one`, or ``None`` if the
        owl timed out — a timeout is recorded (stuck), never propagated, so a
        single hung owl cannot stall or fail the whole nightly batch.
        """
        try:
            if self._governor is None:
                return await asyncio.wait_for(
                    self._evolve_one(manifest), timeout=self._per_owl_timeout_s
                )
            async with self._governor.slot():
                return await asyncio.wait_for(
                    self._evolve_one(manifest), timeout=self._per_owl_timeout_s
                )
        except TimeoutError:
            log.engine.warning(
                "[dna] coordinator._evolve_one_bounded: owl timed out — skipping",
                extra={"_fields": {
                    "owl": manifest.name, "timeout_s": self._per_owl_timeout_s,
                }},
            )
            return None
        except Exception as exc:  # B5 — one owl's crash never sinks the batch
            log.engine.warning(
                "[dna] coordinator._evolve_one_bounded: owl evolution failed — skipping",
                exc_info=exc, extra={"_fields": {"owl": manifest.name}},
            )
            return None

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
        report = self._attributor.attribute(
            owl_name=manifest.name, current_dna=manifest.dna, outcomes=outcomes,
        )
        log.engine.debug(
            "[dna] coordinator._try_attribution: exit",
            extra={"_fields": {
                "owl": manifest.name,
                "n_outcomes": len(outcomes),
                "n_deltas": len(report.deltas),
            }},
        )
        return report

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
            provider = self._provider_registry.get_by_tier("fast")
            result = await provider.complete(messages, model="", max_tokens=512)
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
