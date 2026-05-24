"""DNA evolution — coordinator and delta validator (Story 4.3).

The ``EvolutionPromptBuilder`` lives in ``evolution_prompt.py`` and
``DNACheckpointer`` in ``dna_storage.py`` to keep this module within the
300-line cap. The trio of classes still forms the LLM-driven mutation
pipeline, re-exported here for caller convenience::

    EvolutionPromptBuilder → ModelProvider → DeltaValidator → mutate
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_storage import DNACheckpointer
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
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.UNICODE)

_UPSERT_DNA_SQL = """
INSERT INTO owl_dna (
    owl_name, challenge_level, verbosity, curiosity,
    formality, creativity, precision, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(owl_name) DO UPDATE SET
    challenge_level = excluded.challenge_level,
    verbosity = excluded.verbosity,
    curiosity = excluded.curiosity,
    formality = excluded.formality,
    creativity = excluded.creativity,
    precision = excluded.precision,
    updated_at = excluded.updated_at
"""

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

    _TRAITS: frozenset[str] = frozenset(
        {
            "challenge_level",
            "verbosity",
            "curiosity",
            "formality",
            "creativity",
            "precision",
        }
    )

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
    ) -> None:
        self._db = db
        self._provider_registry = provider_registry
        self._owl_registry = owl_registry
        self._batch_size = max(1, evolution_batch_size)
        self._prompt_builder = EvolutionPromptBuilder()
        self._validator = DeltaValidator()
        self._checkpointer = DNACheckpointer(db)

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
        try:
            for manifest in self._owl_registry.list():
                if await self._evolve_one(manifest):
                    mutated_owls.append(manifest.name)
                else:
                    skipped_owls.append(manifest.name)
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
        duration_ms = (time.monotonic() - t0) * 1000
        output = f"mutated={len(mutated_owls)} skipped={len(skipped_owls)}"
        log.engine.info(
            "[dna] coordinator.execute: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "mutated": mutated_owls,
                    "skipped": skipped_owls,
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
        """Evolve a single owl. Returns ``True`` if any mutation was applied."""
        excerpts = await self._fetch_excerpts(manifest.name)
        if len(excerpts) < self._batch_size:
            log.engine.debug(
                "[dna] coordinator.evolve: not enough excerpts — skip",
                extra={"_fields": {"owl": manifest.name, "available": len(excerpts)}},
            )
            return False
        messages = self._prompt_builder.build(manifest.name, manifest, excerpts)
        TestModeGuard.assert_not_test_mode(f"evolution.complete[{manifest.name}]")
        provider = self._provider_registry.get_by_tier("fast")
        result = await provider.complete(messages, model="", max_tokens=512)
        deltas = self._validator.validate(result.content)
        if not deltas:
            log.engine.warning(
                "[dna] coordinator.evolve: no valid deltas — skip",
                extra={"_fields": {"owl": manifest.name}},
            )
            return False
        checkpoint_id = await self._checkpointer.checkpoint(manifest.name, manifest.dna)
        new_dna = manifest.dna
        for trait, delta in deltas.items():
            previous = float(getattr(new_dna, trait))
            new_dna = new_dna.mutate(trait, delta)
            current = float(getattr(new_dna, trait))
            log.engine.info(
                "[dna] %s: %s %.3f → %.3f (delta %+.3f)",
                manifest.name,
                trait,
                previous,
                current,
                delta,
            )
        await self._persist_dna(manifest.name, new_dna)
        log.engine.info(
            "[dna] coordinator.evolve: mutations applied",
            extra={
                "_fields": {
                    "owl": manifest.name,
                    "checkpoint_id": checkpoint_id,
                    "mutated_traits": list(deltas.keys()),
                }
            },
        )
        return True

    async def _fetch_excerpts(self, owl_name: str) -> list[str]:
        rows = await self._db.fetch_all(_FETCH_EXCERPTS_SQL, (owl_name, self._batch_size))
        return [str(row["content"]) for row in rows if row.get("content")]

    async def _persist_dna(self, owl_name: str, dna: OwlDNA) -> None:
        await self._db.execute(
            _UPSERT_DNA_SQL,
            (
                owl_name,
                dna.challenge_level,
                dna.verbosity,
                dna.curiosity,
                dna.formality,
                dna.creativity,
                dna.precision,
                datetime.now(UTC).isoformat(),
            ),
        )
