"""ToolHeuristicStore — SQLite CRUD for the ``tool_heuristics`` table (Commit 5).

Canonical source of truth for mined patterns linking (tool_name, condition,
predicted_outcome) → evidence_count. Embeddings of the human-readable
heuristic content land in the LanceDB lessons table via
:class:`stackowl.learning.lessons_index.LessonsIndex`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository


@dataclass(frozen=True)
class ToolHeuristic:
    """Read-side projection of one tool_heuristics row."""

    heuristic_id: int
    tool_name: str
    condition_kind: str
    condition_value: str
    predicted_outcome: str
    evidence_count: int
    mean_quality: float | None
    failure_class: str | None
    last_seen_at: float
    created_at: float
    updated_at: float


_INSERT_SQL = """
INSERT INTO tool_heuristics
    (tool_name, condition_kind, condition_value, predicted_outcome,
     evidence_count, mean_quality, failure_class,
     last_seen_at, created_at, updated_at, owner_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(owner_id, tool_name, condition_kind, condition_value, predicted_outcome) DO UPDATE SET
    evidence_count = excluded.evidence_count,
    mean_quality = excluded.mean_quality,
    failure_class = excluded.failure_class,
    last_seen_at = excluded.last_seen_at,
    updated_at = excluded.updated_at
"""

_SELECT_FIELDS = """
    heuristic_id, tool_name, condition_kind, condition_value, predicted_outcome,
    evidence_count, mean_quality, failure_class,
    last_seen_at, created_at, updated_at
"""


class ToolHeuristicStore(OwnedRepository):
    """Async SQLite wrapper for ``tool_heuristics`` (migration 0035).

    Owner-scoped: reads/writes are constrained to ``owner_id`` (defaults to the
    single-user :data:`DEFAULT_PRINCIPAL_ID`, so existing behavior is unchanged).
    """

    _table = "tool_heuristics"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.memory.debug("[heuristic] store.init: ready")

    async def upsert(
        self,
        *,
        tool_name: str,
        condition_kind: str,
        condition_value: str,
        predicted_outcome: str,
        evidence_count: int,
        mean_quality: float | None = None,
        failure_class: str | None = None,
    ) -> int:
        """Insert-or-update a heuristic. Returns its ``heuristic_id``."""
        # 1. ENTRY
        log.memory.debug(
            "[heuristic] store.upsert: entry",
            extra={"_fields": {
                "tool_name": tool_name, "kind": condition_kind,
                "outcome": predicted_outcome, "evidence_count": evidence_count,
            }},
        )
        now = time.time()
        await self._db.execute(
            _INSERT_SQL,
            (
                tool_name, condition_kind, condition_value, predicted_outcome,
                evidence_count, mean_quality, failure_class,
                now, now, now, self._owner_id,
            ),
        )
        rows = await self._db.fetch_all(
            """SELECT heuristic_id FROM tool_heuristics
               WHERE owner_id = ? AND tool_name = ? AND condition_kind = ?
                 AND condition_value = ? AND predicted_outcome = ?""",
            (self._owner_id, tool_name, condition_kind, condition_value, predicted_outcome),
        )
        hid = int(str(rows[0]["heuristic_id"])) if rows else -1
        # 4. EXIT
        log.memory.info(
            "[heuristic] store.upsert: stored",
            extra={"_fields": {"heuristic_id": hid, "tool_name": tool_name}},
        )
        return hid

    async def find_for_tool(
        self, tool_name: str, *, min_evidence: int = 3,
    ) -> list[ToolHeuristic]:
        """Active heuristics for ``tool_name`` with ``evidence_count >= min_evidence``."""
        log.memory.debug(
            "[heuristic] store.find_for_tool: entry",
            extra={"_fields": {"tool_name": tool_name, "min_evidence": min_evidence}},
        )
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM tool_heuristics "
            "WHERE owner_id = ? AND tool_name = ? AND evidence_count >= ? "
            "ORDER BY evidence_count DESC LIMIT 50",
            (self._owner_id, tool_name, min_evidence),
        )
        results = [_row_to_heuristic(r) for r in rows]
        log.memory.debug(
            "[heuristic] store.find_for_tool: exit",
            extra={"_fields": {"tool_name": tool_name, "n": len(results)}},
        )
        return results

    async def list_all(self, *, min_evidence: int = 1) -> list[ToolHeuristic]:
        """Every heuristic with at least ``min_evidence`` occurrences."""
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM tool_heuristics "
            "WHERE owner_id = ? AND evidence_count >= ? "
            "ORDER BY tool_name, evidence_count DESC",
            (self._owner_id, min_evidence),
        )
        return [_row_to_heuristic(r) for r in rows]


def _row_to_heuristic(row: dict[str, object]) -> ToolHeuristic:
    mq_raw = row.get("mean_quality")
    return ToolHeuristic(
        heuristic_id=int(str(row["heuristic_id"])),
        tool_name=str(row["tool_name"]),
        condition_kind=str(row["condition_kind"]),
        condition_value=str(row["condition_value"]),
        predicted_outcome=str(row["predicted_outcome"]),
        evidence_count=int(str(row["evidence_count"])),
        mean_quality=float(str(mq_raw)) if mq_raw is not None else None,
        failure_class=str(row["failure_class"]) if row.get("failure_class") else None,
        last_seen_at=float(str(row["last_seen_at"])),
        created_at=float(str(row["created_at"])),
        updated_at=float(str(row["updated_at"])),
    )


def heuristic_summary(h: ToolHeuristic) -> str:
    """Human-readable line for prompt-time consumption + lessons content."""
    fc = f" ({h.failure_class})" if h.failure_class else ""
    mq = f" mean_quality={h.mean_quality:.2f}" if h.mean_quality is not None else ""
    return (
        f"{h.tool_name}: when {h.condition_kind}={h.condition_value} → "
        f"{h.predicted_outcome}{fc} [evidence={h.evidence_count}{mq}]"
    )
