"""Per-owl tool-usage scoring — the stable ordering signal for D05.2.

Answers one question: *which tools does THIS owl actually reach for?* The answer
orders the discretionary half of the presented set
(:meth:`ToolPresentation.rank_candidates`), replacing a lexical match against the
turn's request text — a signal that changed with every question and so defeated
the position-0 prompt-cache marker.

NO NEW STORAGE. The signal has been recorded on every turn since 2026-05-28:
``task_outcomes`` carries ``owl_name`` alongside ``tool_sequence`` (a JSON array
of the tool names the turn dispatched), written from
``pipeline/backends/shared.py``. This module groups what is already there. A
fourth per-tool aggregate table — beside ``task_outcomes``, ``tool_heuristics``
and the turn-scoped ledger — is exactly the duplication dedup target X2 exists to
remove, so there is deliberately no store here and nothing to migrate.

Read once per session, never per turn: see ``infra/presented_tools.py``.

NOT :class:`ToolOutcomeMiner`. That miner scans the same rows daily but buckets
by ``(tool, failure_label)`` and discards ``owl_name``. Its question is "does this
tool tend to succeed?" — global and predictive, feeding ``tool_heuristics``. Ours
is "does this owl use this tool?" — per-owl and descriptive. Same rows, different
grain; merging them would change the meaning of a table four other call sites
read as a global prediction.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.outcome_store import is_positive_signal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore

__all__ = [
    "DEMOTION_HALF_LIFE_DAYS", "LOOKBACK_DAYS",
    "score_tools_for_owl", "score_tools_globally",
]

#: How far back usage is read. Bounds the query and gives demotion its horizon:
#: a tool untouched for the whole window scores 0 and falls to the by-name tail.
LOOKBACK_DAYS = 30

#: Recency weighting. A dispatch from `n` days ago counts 0.5**(n/half_life), so
#: an owl that changed jobs last week is re-ordered within a couple of weeks
#: instead of being held to what it used a month ago. Chosen so the oldest usable
#: evidence (30 days) still carries ~1/8 the weight of today's, not ~0 — a rarely
#: run owl must not be scored as if it had no history at all.
DEMOTION_HALF_LIFE_DAYS = 10.0

#: Quality floor. DELIBERATELY LOWER than list_successful_with_sequence's 0.75
#: default, which is a skill-synthesis bar: "good enough to base a new skill on"
#: is a much stronger claim than "this owl uses this tool". Ordering only needs
#: the latter, and the stricter bar would starve the signal for owls whose work
#: is routinely scored mid-range.
MIN_QUALITY = 0.4

#: Cap on rows scanned. The query orders NEWEST-FIRST — the store's own
#: list_successful_with_sequence is oldest-first (ASC) because its consumer wants
#: a pattern's chronological development, which is the wrong end for a
#: recency-weighted score. Reusing that helper's default would have silently
#: scored the oldest 500 rows in the window and called the result recent.
MAX_ROWS = 500

_SECONDS_PER_DAY = 86_400


def _score_rows(
    rows: Sequence[TaskOutcome], *, now: float, half_life_days: float,
) -> tuple[dict[str, float], int]:
    """Recency-weighted per-tool scoring. ONE copy of the rule.

    Extracted for ESC-36 so :func:`score_tools_for_owl` and
    :func:`score_tools_globally` cannot drift on the quality floor, the
    positive-only gate, or the half-life. Returns (scores, rows_used).
    """
    scores: dict[str, float] = {}
    used = 0
    for outcome in rows:
        # POSITIVE-ONLY LEARNING (standing operator directive). Inherited from
        # is_positive_signal rather than re-implemented — the same gate the DNA
        # attribution and tool-outcome miners use, so it cannot drift between
        # them. It also excludes an approach the user explicitly Disliked, which
        # a hand-rolled `success == 1` check here would have silently promoted.
        if not is_positive_signal(outcome):
            continue
        if outcome.quality_score is not None and outcome.quality_score < MIN_QUALITY:
            continue
        if not outcome.tool_sequence:
            continue
        age_days = max(now - (outcome.captured_at or now), 0.0) / _SECONDS_PER_DAY
        weight = math.pow(0.5, age_days / half_life_days) if half_life_days > 0 else 1.0
        used += 1
        for tool_name in outcome.tool_sequence:
            if tool_name:
                scores[tool_name] = scores.get(tool_name, 0.0) + weight
    return scores, used


async def score_tools_globally(
    outcomes: TaskOutcomeStore,
    *,
    now: float | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    half_life_days: float = DEMOTION_HALF_LIFE_DAYS,
) -> dict[str, float]:
    """The PLATFORM's tool usage, across every owl — ESC-36's tie-break.

    WHY THIS EXISTS. `rank_candidates` keyed on
    ``(-owl_usage, -declared_priority, name)``, and the last term decided far more
    than it looks. Measured 2026-08-23 over 9,349 outcomes: only 3 of 18 owls are
    cold-start, but even a busy owl has history for a small slice of the catalog —
    ``headhunter`` 14 distinct tools of 77, ``secretary`` 23. Everything else tied
    at 0 and was ordered by SPELLING, for owls with and without history alike.
    ``presentation_priority`` could not rescue them either: 8 of 77 tools declare
    it and all eight are browser tools.

    So when this owl has no evidence for a tool, use everyone's. Data-derived, not
    hand-ranked — a curated ordering of 77 tools is a constant that rots exactly as
    the eight lockstep cap bumps did.

    Empty dict on any failure, exactly like the per-owl scorer: ordering may
    degrade, it may never break a turn.
    """
    # 1. ENTRY
    log.tool.debug(
        "[tool_usage] score_tools_globally: entry",
        extra={"_fields": {"lookback_days": lookback_days}},
    )
    now = time.time() if now is None else now
    since = now - lookback_days * _SECONDS_PER_DAY
    try:
        rows = await outcomes.list_tool_usage_global(
            since_epoch=since, limit=MAX_ROWS,
        )
    except Exception as err:  # noqa: BLE001 — ordering must never break a turn
        log.tool.error(
            "[tool_usage] score_tools_globally: outcome read failed — "
            "unscored tools keep by-name order",
            exc_info=err,
        )
        return {}

    scores, used = _score_rows(rows, now=now, half_life_days=half_life_days)
    # 4. EXIT — INFO, not debug: this is the evidence that the tie-break is live.
    log.tool.info(
        "[tool_usage] score_tools_globally: exit",
        extra={"_fields": {
            "rows": len(rows), "positive_rows": used, "tools_scored": len(scores),
            "top": sorted(scores, key=lambda n: -scores[n])[:5],
        }},
    )
    return scores


async def score_tools_for_owl(
    outcomes: TaskOutcomeStore,
    owl_name: str,
    *,
    now: float | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    half_life_days: float = DEMOTION_HALF_LIFE_DAYS,
) -> dict[str, float]:
    """Return ``{tool_name: score}`` for one owl — higher means used more/recently.

    Empty dict on no history, no owl, or any read failure. That is the cold-start
    path, and it is a real fallback rather than a first-run-only branch: an empty
    mapping makes ``rank_candidates`` order by name, which is deterministic and
    stable. A degraded ordering is acceptable here; an unstable one is not.
    """
    # 1. ENTRY
    log.tool.debug(
        "[tool_usage] score_tools_for_owl: entry",
        extra={"_fields": {"owl": owl_name, "lookback_days": lookback_days}},
    )
    if not owl_name:
        # 2. DECISION — no identity to score against. The global path (e.g. a CLI
        # turn with no owl) gets by-name order, which is what it had before D05.2.
        log.tool.debug("[tool_usage] score_tools_for_owl: exit — no owl name")
        return {}

    now = time.time() if now is None else now
    since = now - lookback_days * _SECONDS_PER_DAY
    try:
        rows = await outcomes.list_tool_usage_for_owl(
            owl_name=owl_name, since_epoch=since, limit=MAX_ROWS,
        )
    except Exception as err:  # noqa: BLE001 — ordering must never break a turn
        log.tool.error(
            "[tool_usage] score_tools_for_owl: outcome read failed — "
            "falling back to by-name order",
            exc_info=err,
            extra={"_fields": {"owl": owl_name}},
        )
        return {}

    scores, used = _score_rows(rows, now=now, half_life_days=half_life_days)

    # 4. EXIT
    log.tool.debug(
        "[tool_usage] score_tools_for_owl: exit",
        extra={"_fields": {
            "owl": owl_name, "rows": len(rows), "positive_rows": used,
            "tools_scored": len(scores),
            "top": sorted(scores, key=lambda n: -scores[n])[:5],
        }},
    )
    return scores
