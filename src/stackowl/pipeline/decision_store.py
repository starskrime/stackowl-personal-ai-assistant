"""TurnDecisionStore — durable per-session snapshot of the ADR-7 DecisionLedger.

The :mod:`stackowl.infra.decision_ledger` ledger is a per-turn ContextVar — it
vanishes the instant the turn's ``finally`` runs, and the gateway/core split
means the process that ran the turn may not be the one that later serves
``/explain``. This store persists the latest turn's decisions to the
``turn_decisions`` table (migration 0071) so "why did you do that?" becomes a
durable, cross-process read.

One row per session (UPSERT on ``session_id``) — only the latest turn is kept,
so the table never grows unbounded. Both methods are best-effort: persistence
must never break a turn (B5) and an explanation read must never raise, so every
failure is logged and swallowed (``save`` returns, ``latest`` returns ``None``).
"""

from __future__ import annotations

import json
import time

from stackowl.db.pool import DbPool
from stackowl.infra.decision_ledger import Decision
from stackowl.infra.observability import log

_UPSERT = (
    "INSERT INTO turn_decisions (session_id, trace_id, created_at, decisions_json) "
    "VALUES (?, ?, ?, ?) "
    "ON CONFLICT(session_id) DO UPDATE SET "
    "trace_id = excluded.trace_id, "
    "created_at = excluded.created_at, "
    "decisions_json = excluded.decisions_json"
)


class TurnDecisionStore:
    """Persist / read the latest turn's :class:`Decision` snapshot per session."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def save(
        self,
        *,
        session_id: str,
        trace_id: str | None,
        decisions: tuple[Decision, ...],
    ) -> None:
        """UPSERT this turn's decisions for ``session_id``. Best-effort, never raises."""
        # 1. ENTRY
        log.engine.debug(
            "[decision_store] save: entry",
            extra={"_fields": {"session_id": session_id, "count": len(decisions)}},
        )
        try:
            # 2. DECISION — serialize with default=str so non-str inputs/evidence
            # values (paths, enums, numbers) never break json.dumps.
            payload = json.dumps(
                [
                    {
                        "point": d.point,
                        "verdict": d.verdict,
                        "reason": d.reason,
                        "inputs": d.inputs,
                        "alternatives_considered": list(d.alternatives_considered),
                        "evidence": d.evidence,
                    }
                    for d in decisions
                ],
                default=str,
            )
            # 3. STEP — single UPSERT keeps only the latest turn per session.
            await self._db.execute(
                _UPSERT, (session_id, trace_id, time.time(), payload)
            )
            # 4. EXIT
            log.engine.debug(
                "[decision_store] save: exit — persisted",
                extra={"_fields": {"session_id": session_id, "json_len": len(payload)}},
            )
        except Exception as exc:
            log.engine.error(
                "[decision_store] save: failed (swallowed — must not break turn)",
                exc_info=exc,
                extra={"_fields": {"session_id": session_id}},
            )

    async def latest(self, session_id: str) -> tuple[Decision, ...] | None:
        """Read the latest persisted decisions for ``session_id``. Never raises.

        Returns ``None`` when there is no row or on any read/decode failure."""
        # 1. ENTRY
        log.engine.debug(
            "[decision_store] latest: entry",
            extra={"_fields": {"session_id": session_id}},
        )
        try:
            rows = await self._db.fetch_all(
                "SELECT decisions_json FROM turn_decisions WHERE session_id = ?",
                (session_id,),
            )
            if not rows:
                # 4. EXIT — no snapshot
                log.engine.debug(
                    "[decision_store] latest: exit — no row",
                    extra={"_fields": {"session_id": session_id}},
                )
                return None
            raw = json.loads(rows[0]["decisions_json"])
            decisions = tuple(
                Decision(
                    point=str(item.get("point", "")),
                    verdict=str(item.get("verdict", "")),
                    reason=str(item.get("reason", "")),
                    inputs=item.get("inputs") or {},
                    alternatives_considered=tuple(item.get("alternatives_considered") or ()),
                    evidence=item.get("evidence") or {},
                )
                for item in raw
            )
            # 4. EXIT
            log.engine.debug(
                "[decision_store] latest: exit — loaded",
                extra={"_fields": {"session_id": session_id, "count": len(decisions)}},
            )
            return decisions
        except Exception as exc:
            log.engine.error(
                "[decision_store] latest: failed (swallowed — returning None)",
                exc_info=exc,
                extra={"_fields": {"session_id": session_id}},
            )
            return None
