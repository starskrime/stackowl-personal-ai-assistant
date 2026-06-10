"""ContradictionDetector — pairwise cosine-similarity contradiction & near-duplicate scan."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.memory.models import MemoryRecord, StagedFact


_NEAR_DUPLICATE_THRESHOLD = 0.95
_CONTRADICTION_THRESHOLD = 0.85
_NEAR_DUPLICATE_LABEL = "near-duplicate"
_CONTRADICTION_LABEL = "potential-contradiction"


class ContradictionReport(BaseModel):
    """A single pairwise finding produced by :class:`ContradictionDetector`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id_a: str
    fact_id_b: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. Returns 0.0 for zero-magnitude vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (mag_a * mag_b)))


class ContradictionDetector:
    """Detects near-duplicate and potential-contradiction pairs by cosine similarity.

    Strategy (per the Story 6.6 spec):

    * Pairwise scan of every fact that exposes an ``embedding``.
    * ``similarity >= 0.95`` *and same source_type* → ``near-duplicate``.
    * ``similarity >= 0.85`` *and different source_type* → ``potential-contradiction``.
    * Facts without embeddings are skipped silently (logged at debug).
    * Any unhandled exception returns ``[]`` so the DreamWorker never crashes.
    """

    def __init__(
        self,
        near_duplicate_threshold: float = _NEAR_DUPLICATE_THRESHOLD,
        contradiction_threshold: float = _CONTRADICTION_THRESHOLD,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] contradiction_detector.init: entry",
            extra={
                "_fields": {
                    "near_duplicate_threshold": near_duplicate_threshold,
                    "contradiction_threshold": contradiction_threshold,
                }
            },
        )
        self._near_dup = near_duplicate_threshold
        self._contradiction = contradiction_threshold
        # 4. EXIT
        log.memory.debug("[memory] contradiction_detector.init: exit")

    async def detect(
        self, facts: list[StagedFact | MemoryRecord]
    ) -> list[ContradictionReport]:
        """Run pairwise similarity scan; return list of reports."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] contradiction_detector.detect: entry",
            extra={"_fields": {"fact_count": len(facts)}},
        )
        try:
            with_embeddings = self._filter_embedded(facts)
            # 2. DECISION
            if len(with_embeddings) < 2:
                log.memory.debug(
                    "[memory] contradiction_detector.detect: insufficient embedded facts",
                    extra={"_fields": {"embedded_count": len(with_embeddings)}},
                )
                return []
            # 3. STEP — pairwise scan
            reports = self._scan_pairs(with_embeddings)
        except Exception as exc:
            # B5 — never crash the caller; return empty list and log
            log.memory.warning(
                "[memory] contradiction_detector.detect: scan failed — returning []",
                exc_info=exc,
                extra={"_fields": {"fact_count": len(facts)}},
            )
            return []
        # 4. EXIT
        log.memory.info(
            "[memory] contradiction_detector.detect: exit",
            extra={
                "_fields": {
                    "fact_count": len(facts),
                    "report_count": len(reports),
                }
            },
        )
        return reports

    # ------------------------------------------------------------------ helpers

    def _filter_embedded(
        self, facts: list[StagedFact | MemoryRecord]
    ) -> list[StagedFact | MemoryRecord]:
        """Drop facts that don't expose a non-empty embedding list."""
        kept: list[StagedFact | MemoryRecord] = []
        for fact in facts:
            embedding = getattr(fact, "embedding", None)
            if embedding is None or len(embedding) == 0:
                log.memory.debug(
                    "[memory] contradiction_detector: skipping fact without embedding",
                    extra={"_fields": {"fact_id": getattr(fact, "fact_id", "?")}},
                )
                continue
            kept.append(fact)
        return kept

    def _scan_pairs(
        self, facts: list[StagedFact | MemoryRecord]
    ) -> list[ContradictionReport]:
        """O(n^2) scan returning all reports that cross the configured thresholds."""
        reports: list[ContradictionReport] = []
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                report = self._classify_pair(facts[i], facts[j])
                if report is not None:
                    reports.append(report)
                    self._log_finding(report)
        return reports

    def _classify_pair(
        self,
        fact_a: StagedFact | MemoryRecord,
        fact_b: StagedFact | MemoryRecord,
    ) -> ContradictionReport | None:
        """Return a report when the pair crosses a threshold, else ``None``."""
        emb_a = getattr(fact_a, "embedding", None) or []
        emb_b = getattr(fact_b, "embedding", None) or []
        sim = _cosine(emb_a, emb_b)
        src_a = getattr(fact_a, "source_type", "")
        src_b = getattr(fact_b, "source_type", "")
        # Near-duplicate has higher threshold and same source_type
        if sim >= self._near_dup and src_a == src_b:
            return ContradictionReport(
                fact_id_a=fact_a.fact_id,
                fact_id_b=fact_b.fact_id,
                explanation=_NEAR_DUPLICATE_LABEL,
                confidence=sim,
            )
        if sim >= self._contradiction and src_a != src_b:
            return ContradictionReport(
                fact_id_a=fact_a.fact_id,
                fact_id_b=fact_b.fact_id,
                explanation=_CONTRADICTION_LABEL,
                confidence=sim,
            )
        return None

    def _log_finding(self, report: ContradictionReport) -> None:
        """Always log contradictions/duplicates at WARNING so they surface in logs."""
        log.memory.warning(
            "[memory] contradiction_detector: finding",
            extra={
                "_fields": {
                    "fact_id_a": report.fact_id_a,
                    "fact_id_b": report.fact_id_b,
                    "explanation": report.explanation,
                    "confidence": report.confidence,
                }
            },
        )


__all__: list[str] = ["ContradictionDetector", "ContradictionReport"]
