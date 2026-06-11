"""Confidence-aware ranking of tool-heuristic lesson hits (UCB-style).

Pillar ③: a noisy, low-evidence heuristic should not outrank a well-proven one
purely on semantic similarity. We reorder ONLY ``tool_heuristic`` hits by

    score(h) = similarity(h) - c * sqrt( ln(N) / evidence_count(h) )

with c = sqrt(2) and N = sum of evidence over the heuristic candidates (>= e, so
the log is non-negative). High similarity dominates; the penalty term demotes
under-evidenced (noisy) heuristics — low evidence inflates the term and lowers rank. Hits with no ``evidence_count`` in
metadata (legacy rows / non-heuristic) score as similarity-only — fail-safe.
Non-heuristic hits keep their original relative order, appended after heuristics.
"""

from __future__ import annotations

import math

from stackowl.infra.observability import log
from stackowl.learning.lesson import LessonHit

_HEURISTIC_SOURCE = "tool_heuristic"
_C = math.sqrt(2.0)


def _evidence(hit: LessonHit) -> int | None:
    raw = hit.metadata.get("evidence_count")
    if isinstance(raw, bool):
        return None
    return raw if isinstance(raw, int) and raw > 0 else None


def rank_lessons(hits: list[LessonHit]) -> list[LessonHit]:
    """Return hits with heuristic hits UCB-ranked first, others appended in order."""
    heuristics = [h for h in hits if h.source_type == _HEURISTIC_SOURCE]
    others = [h for h in hits if h.source_type != _HEURISTIC_SOURCE]
    if len(heuristics) <= 1:
        return [*heuristics, *others]
    total_n = max(math.e, float(sum(_evidence(h) or 0 for h in heuristics)))
    ln_n = math.log(total_n)

    def score(h: LessonHit) -> float:
        ev = _evidence(h)
        if ev is None:
            return h.similarity
        return h.similarity - _C * math.sqrt(ln_n / ev)

    ranked = sorted(heuristics, key=score, reverse=True)
    log.engine.debug(
        "[learning] rank_lessons: ranked heuristics",
        extra={"_fields": {"n_heuristic": len(ranked), "n_other": len(others),
                            "top_ref": ranked[0].source_ref if ranked else None}},
    )
    return [*ranked, *others]
