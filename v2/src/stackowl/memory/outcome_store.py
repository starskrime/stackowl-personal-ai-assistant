"""TaskOutcomeStore — persistence layer for per-pipeline-run outcomes.

Telemetry, not knowledge: dedicated table separate from staged_facts and
audit_log per the Commit 1 pre-implementation audit. Outcomes are captured
synchronously by the backend at end-of-pipeline; the critic LLM fills in
``quality_score`` asynchronously later (CriticScorerHandler).

Failure_class is just the exception class name from ``state.errors`` — we
reuse the existing :mod:`stackowl.exceptions` hierarchy as the taxonomy
rather than inventing a parallel enum.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log

_EXC_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")


@dataclass(frozen=True)
class TaskOutcome:
    """Read-side projection of one task_outcomes row."""

    outcome_id: int
    trace_id: str
    session_id: str
    owl_name: str
    channel: str
    success: bool
    latency_ms: float
    tool_call_count: int
    failure_class: str | None
    quality_score: float | None
    step_durations: dict[str, float]
    input_text: str
    response_text: str
    captured_at: float
    scored_at: float | None
    tool_sequence: tuple[str, ...] = ()
    dna_snapshot: dict[str, float] = field(default_factory=dict)


def classify_failure(errors: tuple[str, ...]) -> str | None:
    """Derive a failure_class string from ``state.errors``.

    Reuses the existing exception hierarchy from :mod:`stackowl.exceptions`
    — we extract the first exception class name we find. Returns None when
    there are no errors (i.e. the run succeeded).
    """
    if not errors:
        return None
    for err in errors:
        # Error strings look like: "stepname: ExceptionClass: detail message"
        # The classify_failure logic strips off the step prefix and pulls the class name.
        stripped = err.split(":", 1)[1].strip() if ":" in err else err
        match = _EXC_NAME_RE.match(stripped)
        if match:
            return match.group(1)
    # Fallback — first error string truncated.
    return errors[0][:120]


class TaskOutcomeStore:
    """Async SQLite wrapper for the task_outcomes table (migration 0029)."""

    def __init__(self, db: DbPool) -> None:
        self._db = db
        log.memory.debug("[outcomes] store.init: ready")

    async def record(
        self,
        *,
        trace_id: str,
        session_id: str,
        owl_name: str,
        channel: str,
        success: bool,
        latency_ms: float,
        tool_call_count: int,
        failure_class: str | None,
        step_durations: dict[str, float],
        input_text: str,
        response_text: str,
        tool_sequence: tuple[str, ...] = (),
        dna_snapshot: dict[str, float] | None = None,
    ) -> None:
        """Insert a new outcome row. quality_score / scored_at start NULL.

        Idempotent on (trace_id) — second insert with the same trace_id is
        a no-op so backends that retry don't double-insert.
        """
        log.memory.debug(
            "[outcomes] record: entry",
            extra={"_fields": {
                "trace_id": trace_id,
                "success": success,
                "latency_ms": int(latency_ms),
                "tool_call_count": tool_call_count,
            }},
        )
        await self._db.execute(
            """INSERT INTO task_outcomes (
                   trace_id, session_id, owl_name, channel, success,
                   latency_ms, tool_call_count, failure_class,
                   step_durations, input_text, response_text, captured_at,
                   tool_sequence, dna_snapshot
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(trace_id) DO NOTHING""",
            (
                trace_id, session_id, owl_name, channel, int(success),
                latency_ms, tool_call_count, failure_class,
                json.dumps(step_durations, separators=(",", ":")),
                input_text[:8000], response_text[:8000], time.time(),
                json.dumps(list(tool_sequence), separators=(",", ":")),
                json.dumps(dna_snapshot or {}, separators=(",", ":")),
            ),
        )
        log.memory.info(
            "[outcomes] record: exit",
            extra={"_fields": {"trace_id": trace_id, "success": success}},
        )

    async def list_pending_critic(self, limit: int = 25) -> list[TaskOutcome]:
        """Return outcomes that haven't been scored yet (quality_score IS NULL).

        Ordered oldest-first so the critic scores in the order things happened
        (gives more sensible cross-references when reading reflections later).
        """
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] list_pending_critic: entry",
            extra={"_fields": {"limit": limit}},
        )
        # 3. STEP — single indexed SELECT against idx_task_outcomes_pending
        rows = await self._db.fetch_all(
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes
               WHERE quality_score IS NULL
               ORDER BY captured_at ASC
               LIMIT ?""",
            (limit,),
        )
        results = [_row_to_outcome(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "[outcomes] list_pending_critic: exit",
            extra={"_fields": {"limit": limit, "n_pending": len(results)}},
        )
        return results

    async def set_quality_score(self, outcome_id: int, score: float) -> None:
        """Write the critic's score back. Idempotent — overwrites previous score."""
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] set_quality_score: entry",
            extra={"_fields": {"outcome_id": outcome_id, "score": score}},
        )
        # 3. STEP
        await self._db.execute(
            "UPDATE task_outcomes SET quality_score = ?, scored_at = ? WHERE outcome_id = ?",
            (score, time.time(), outcome_id),
        )
        # 4. EXIT
        log.memory.info(
            "[outcomes] set_quality_score: stored",
            extra={"_fields": {"outcome_id": outcome_id, "score": score}},
        )

    async def list_scored_for_owl_global(
        self, *, since_epoch: float = 0.0, limit: int = 2000,
    ) -> list[TaskOutcome]:
        """Return all scored outcomes ACROSS all owls since ``since_epoch``.

        Used by :class:`ToolOutcomeMiner` (Learning Commit 5) to mine per-tool
        patterns regardless of which owl ran the tool. Capped at a wide limit
        so memory stays bounded even on heavy-use accounts.
        """
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] list_scored_for_owl_global: entry",
            extra={"_fields": {"since_epoch": since_epoch, "limit": limit}},
        )
        rows = await self._db.fetch_all(
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes
               WHERE quality_score IS NOT NULL
                 AND captured_at >= ?
               ORDER BY captured_at DESC
               LIMIT ?""",
            (since_epoch, limit),
        )
        results = [_row_to_outcome(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "[outcomes] list_scored_for_owl_global: exit",
            extra={"_fields": {"n": len(results)}},
        )
        return results

    async def list_scored_for_owl(
        self, owl_name: str, *, since_epoch: float = 0.0, limit: int = 500,
    ) -> list[TaskOutcome]:
        """Return all scored outcomes for an owl since ``since_epoch``.

        Used by :class:`DnaAttributor` (Learning Commit 4) to compute per-trait
        quality statistics. Filters out unscored rows (critic hasn't run yet).
        Ordered newest-first so the attributor weights recent outcomes when
        capping at ``limit``.
        """
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] list_scored_for_owl: entry",
            extra={"_fields": {
                "owl_name": owl_name, "since_epoch": since_epoch, "limit": limit,
            }},
        )
        rows = await self._db.fetch_all(
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes
               WHERE owl_name = ?
                 AND quality_score IS NOT NULL
                 AND captured_at >= ?
               ORDER BY captured_at DESC
               LIMIT ?""",
            (owl_name, since_epoch, limit),
        )
        results = [_row_to_outcome(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "[outcomes] list_scored_for_owl: exit",
            extra={"_fields": {"owl_name": owl_name, "n": len(results)}},
        )
        return results

    async def list_successful_with_sequence(
        self, *, min_quality: float = 0.75, since_epoch: float = 0.0,
        limit: int = 500,
    ) -> list[TaskOutcome]:
        """Return scored, high-quality outcomes that invoked at least one tool.

        Used by :class:`SkillSynthesizerHandler` to cluster tool sequences for
        new-skill proposals. Ordered oldest-first so the synthesizer sees the
        chronological development of a pattern (useful when summarising).
        """
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] list_successful_with_sequence: entry",
            extra={"_fields": {
                "min_quality": min_quality, "since_epoch": since_epoch,
                "limit": limit,
            }},
        )
        # 3. STEP — quality + recency + non-empty tool_sequence
        rows = await self._db.fetch_all(
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes
               WHERE quality_score IS NOT NULL
                 AND quality_score >= ?
                 AND captured_at >= ?
                 AND tool_sequence != '[]'
               ORDER BY captured_at ASC
               LIMIT ?""",
            (min_quality, since_epoch, limit),
        )
        results = [_row_to_outcome(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "[outcomes] list_successful_with_sequence: exit",
            extra={"_fields": {"n": len(results), "limit": limit}},
        )
        return results

    async def recent_for_session(
        self, session_id: str, *, limit: int = 3,
        exclude_trace_id: str | None = None,
    ) -> list[TaskOutcome]:
        """Return the most recent outcomes for ``session_id``, newest-first.

        Powers live action recall ("what did you just do?") — surfaced
        synchronously into the classify-built memory_context. ``exclude_trace_id``
        drops the in-flight turn (which the backend captures before classify of
        the NEXT turn runs, so without exclusion the agent could echo the very
        question being asked). Uses idx_task_outcomes_session (migration 0029).
        """
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] recent_for_session: entry",
            extra={"_fields": {
                "session_id": session_id, "limit": limit,
                "has_exclude": exclude_trace_id is not None,
            }},
        )
        # 2. DECISION — nonpositive limit yields nothing (avoid a needless query)
        if limit <= 0:
            log.memory.debug(
                "[outcomes] recent_for_session: exit — nonpositive limit",
                extra={"_fields": {"session_id": session_id, "limit": limit}},
            )
            return []
        # 3. STEP — single indexed SELECT against idx_task_outcomes_session
        sql = (
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes
               WHERE session_id = ?"""
        )
        params: tuple[object, ...] = (session_id,)
        if exclude_trace_id is not None:
            sql += " AND trace_id != ?"
            params += (exclude_trace_id,)
        sql += " ORDER BY captured_at DESC LIMIT ?"
        params += (limit,)
        rows = await self._db.fetch_all(sql, params)
        results = [_row_to_outcome(r) for r in rows]
        # 4. EXIT
        log.memory.debug(
            "[outcomes] recent_for_session: exit",
            extra={"_fields": {"session_id": session_id, "n": len(results)}},
        )
        return results

    async def get_by_trace_id(self, trace_id: str) -> TaskOutcome | None:
        # 1. ENTRY
        log.memory.debug(
            "[outcomes] get_by_trace_id: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        rows = await self._db.fetch_all(
            """SELECT outcome_id, trace_id, session_id, owl_name, channel,
                      success, latency_ms, tool_call_count, failure_class,
                      quality_score, step_durations, input_text, response_text,
                      captured_at, scored_at, tool_sequence, dna_snapshot
               FROM task_outcomes WHERE trace_id = ?""",
            (trace_id,),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.memory.debug(
                "[outcomes] get_by_trace_id: exit — miss",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        out = _row_to_outcome(rows[0])
        log.memory.debug(
            "[outcomes] get_by_trace_id: exit — hit",
            extra={"_fields": {
                "trace_id": trace_id, "outcome_id": out.outcome_id,
                "has_score": out.quality_score is not None,
            }},
        )
        return out


def _row_to_outcome(row: dict[str, object]) -> TaskOutcome:
    step_durations_raw = row.get("step_durations") or "{}"
    try:
        step_durations = json.loads(str(step_durations_raw))
    except json.JSONDecodeError:
        step_durations = {}
    seq_raw = row.get("tool_sequence") or "[]"
    try:
        seq_list = json.loads(str(seq_raw))
        if not isinstance(seq_list, list):
            seq_list = []
    except json.JSONDecodeError:
        seq_list = []
    dna_raw = row.get("dna_snapshot") or "{}"
    try:
        dna_dict = json.loads(str(dna_raw))
        if not isinstance(dna_dict, dict):
            dna_dict = {}
        dna_snapshot = {str(k): float(v) for k, v in dna_dict.items()
                        if isinstance(v, int | float)}
    except (json.JSONDecodeError, TypeError, ValueError):
        dna_snapshot = {}
    quality_raw = row.get("quality_score")
    scored_raw = row.get("scored_at")
    return TaskOutcome(
        outcome_id=int(str(row["outcome_id"])),
        trace_id=str(row["trace_id"]),
        session_id=str(row["session_id"]),
        owl_name=str(row["owl_name"]),
        channel=str(row["channel"]),
        success=bool(row["success"]),
        latency_ms=float(str(row["latency_ms"])),
        tool_call_count=int(str(row["tool_call_count"])),
        tool_sequence=tuple(str(t) for t in seq_list),
        failure_class=str(row["failure_class"]) if row.get("failure_class") else None,
        quality_score=float(str(quality_raw)) if quality_raw is not None else None,
        step_durations=step_durations,
        input_text=str(row.get("input_text", "")),
        response_text=str(row.get("response_text", "")),
        captured_at=float(str(row["captured_at"])),
        scored_at=float(str(scored_raw)) if scored_raw is not None else None,
        dna_snapshot=dna_snapshot,
    )
