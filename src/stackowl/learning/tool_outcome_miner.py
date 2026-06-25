"""ToolOutcomeMiner — discover (tool, condition → outcome) patterns from
``task_outcomes`` data.

Statistical mining only in this commit (per operator vote — "both: statistical
first, LLM only on weak-signal clusters"). The LLM-fallback path is a Phase 2
backlog item — left as a clean extension point in :meth:`mine`.

Patterns surfaced today:
* ``failure_class`` per ``tool_name`` — "when web_fetch is invoked AND the run
  failed with ToolTimeoutError, it's a 'timeout_likely' pattern". Requires
  ≥ :data:`_MIN_EVIDENCE` occurrences in the lookback window.

Future patterns (Phase 2): URL-host extraction, path-prefix extraction,
keyword extraction from input_text — wired into the same upsert pipeline.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.learning.lesson import LessonSource
from stackowl.learning.lessons_index import LessonDraft, LessonsIndex
from stackowl.learning.tool_heuristic_store import (
    ToolHeuristic,
    ToolHeuristicStore,
    heuristic_summary,
)
from stackowl.memory.outcome_store import TaskOutcomeStore

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.memory.outcome_store import TaskOutcome


_LOOKBACK_DAYS_DEFAULT = 30
_SECONDS_PER_DAY = 86_400
_MIN_EVIDENCE = 3
_HEURISTIC_LESSON_SOURCE: LessonSource = "tool_heuristic"


@dataclass(frozen=True)
class MiningReport:
    """Aggregate counts from one mining run."""

    n_outcomes_scanned: int
    n_heuristics_written: int
    n_lessons_published: int


class ToolOutcomeMiner:
    """Scan task_outcomes → write tool_heuristics → publish to lessons index."""

    def __init__(
        self,
        outcome_store: TaskOutcomeStore,
        heuristic_store: ToolHeuristicStore,
        lessons_index: LessonsIndex | None = None,
        *,
        lookback_days: int = _LOOKBACK_DAYS_DEFAULT,
        min_evidence: int = _MIN_EVIDENCE,
    ) -> None:
        self._outcomes = outcome_store
        self._heuristics = heuristic_store
        self._lessons = lessons_index
        self._lookback_days = lookback_days
        self._min_evidence = min_evidence
        log.memory.debug(
            "[heuristic] miner.init: ready",
            extra={"_fields": {
                "lookback_days": lookback_days,
                "min_evidence": min_evidence,
                "has_lessons_index": lessons_index is not None,
            }},
        )

    async def mine(self) -> MiningReport:
        """One mining pass: scan outcomes, group by (tool, failure_class),
        upsert heuristics that meet the evidence threshold, publish summaries
        to the lessons index."""
        # 1. ENTRY
        log.memory.info("[heuristic] miner.mine: entry")
        since = time.time() - self._lookback_days * _SECONDS_PER_DAY
        # We re-use list_successful_with_sequence's lookback semantics — same
        # bucketing surface, just don't require quality >= threshold here.
        try:
            outcomes = await self._outcomes.list_scored_for_owl_global(
                since_epoch=since,
            )
        except AttributeError:
            # Older schema where no global helper exists — derive by scanning
            # all owls via the per-owl method one-by-one would need an owl list
            # we don't carry here. Skip mining gracefully.
            log.memory.warning(
                "[heuristic] miner.mine: outcome_store has no global helper — skip",
            )
            return MiningReport(0, 0, 0)
        log.memory.debug(
            "[heuristic] miner.mine: scanned outcomes",
            extra={"_fields": {"n_outcomes": len(outcomes)}},
        )
        # 3. STEP — bucket by (tool_name, failure_class)
        buckets: dict[tuple[str, str], list[TaskOutcome]] = defaultdict(list)
        for o in outcomes:
            if not o.tool_sequence:
                continue
            # POSITIVE-ONLY LEARNING (operator directive): mine ONLY what WORKED.
            # A failed run is skipped entirely — the platform never learns a
            # "tool X fails under Y" heuristic, only "tool X succeeds for these".
            if o.failure_class:
                continue
            failure_label = "succeeded"
            for tool in o.tool_sequence:
                buckets[(tool, failure_label)].append(o)
        # 3. STEP — emit heuristics for buckets above threshold
        written = 0
        new_lessons: list[LessonDraft] = []
        for (tool_name, failure_label), members in buckets.items():
            if len(members) < self._min_evidence:
                continue
            qualities = [
                float(o.quality_score) for o in members if o.quality_score is not None
            ]
            mean_q = sum(qualities) / len(qualities) if qualities else None
            predicted_outcome = (
                "succeeds" if failure_label == "succeeded" else "fails"
            )
            heuristic_id = await self._heuristics.upsert(
                tool_name=tool_name,
                condition_kind="failure_class",
                condition_value=failure_label,
                predicted_outcome=predicted_outcome,
                evidence_count=len(members),
                mean_quality=mean_q,
                failure_class=None if failure_label == "succeeded" else failure_label,
            )
            written += 1
            # Build the human-readable lesson content (also what's embedded).
            mocked = ToolHeuristic(
                heuristic_id=heuristic_id, tool_name=tool_name,
                condition_kind="failure_class",
                condition_value=failure_label,
                predicted_outcome=predicted_outcome,
                evidence_count=len(members), mean_quality=mean_q,
                failure_class=None if failure_label == "succeeded" else failure_label,
                last_seen_at=time.time(),
                created_at=time.time(), updated_at=time.time(),
            )
            new_lessons.append(LessonDraft(
                source_type=_HEURISTIC_LESSON_SOURCE,
                source_ref=str(heuristic_id),
                content=heuristic_summary(mocked),
                metadata={
                    "tool_name": tool_name,
                    "predicted_outcome": predicted_outcome,
                    "failure_class": failure_label,
                    "evidence_count": len(members),
                    "mean_quality": mean_q,
                },
            ))
        # 3. STEP — push lesson drafts in one batch
        n_lessons = 0
        if new_lessons and self._lessons is not None:
            try:
                n_lessons = await self._lessons.publish_many(new_lessons)
            except Exception as exc:  # B5 — lessons are best-effort
                log.memory.warning(
                    "[heuristic] miner.mine: lessons publish failed",
                    exc_info=exc,
                )
        # 4. EXIT
        report = MiningReport(
            n_outcomes_scanned=len(outcomes),
            n_heuristics_written=written,
            n_lessons_published=n_lessons,
        )
        log.memory.info(
            "[heuristic] miner.mine: exit",
            extra={"_fields": {
                "n_outcomes": report.n_outcomes_scanned,
                "n_heuristics": report.n_heuristics_written,
                "n_lessons": report.n_lessons_published,
            }},
        )
        return report
