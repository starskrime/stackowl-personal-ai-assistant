"""SideEffectLedger — the exactly-once intent->commit contract (Pass 3a).

A side-effecting tool call (one that mutates the world: send an email, write a
file, hit a non-idempotent API) must run AT MOST ONCE even though the durable
executor may replay a step after a crash. This ledger gives that guarantee:

    decision = await ledger.begin(task, step, tool, args)
    if decision.outcome == "already_committed":
        result = decision.result          # replay — DO NOT re-execute
    else:                                  # "proceed" (or "uncertain")
        result = await tool.execute(args)  # execute exactly once
        await ledger.commit(task, step, tool, args, result)

The row is keyed by a DETERMINISTIC :func:`idempotency_key` (sha256 of the
task id, step index, tool name and a canonical, sorted-key JSON of the args),
so the same logical call computes the same key across replays. On re-``begin``
after a ``commit`` the recorded result is returned with no re-execution.

Pure/read tool calls are NOT ledgered — :func:`is_side_effecting` gates that,
reusing the existing ``ToolManifest.action_severity`` taxonomy
(``read`` / ``write`` / ``consequential``). Only ``write`` and
``consequential`` are guarded.

The ledger is owner-scoped via :class:`~stackowl.tenancy.OwnedRepository`: one
principal's ledger rows are invisible to another's store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

#: A row that has only been intended (begin called) but not yet committed.
_STATUS_INTENT = "intent"
#: A row whose side effect has completed and whose result is recorded.
_STATUS_COMMITTED = "committed"

LedgerOutcome = Literal["proceed", "already_committed", "uncertain"]


@dataclass(frozen=True)
class LedgerDecision:
    """The verdict returned by :meth:`SideEffectLedger.begin`.

    ``proceed``           no prior record — caller should execute then commit.
    ``already_committed`` the step already ran; ``result`` holds the recorded
                          output and the caller must NOT re-execute.
    ``uncertain``         an ``intent`` row exists without a commit (a prior
                          attempt may have died mid-execution); the caller
                          should re-attempt with care. ``result`` is ``None``.
    """

    outcome: LedgerOutcome
    result: str | None = None


def idempotency_key(
    task_id: str,
    step_index: int,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    """Deterministic sha256 key for a (task, step, tool, args) tuple.

    ``args`` is serialized as canonical sorted-key JSON so logically-equal
    argument dicts produce an identical key across process restarts and replays.
    Any change to task, step, tool, or any arg value yields a different key.
    """
    canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    payload = "\x1f".join((task_id, str(step_index), tool_name, canonical_args))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_side_effecting(action_severity: str) -> bool:
    """Return True if a tool of this severity must be ledger-guarded.

    Reuses the existing ``ToolManifest.action_severity`` taxonomy: ``write``
    and ``consequential`` mutate the world (guarded); ``read`` is pure (not
    guarded). An unknown severity is treated as side-effecting (fail-safe: when
    in doubt, guard).
    """
    return action_severity != "read"


class SideEffectLedger(OwnedRepository):
    """Owner-scoped exactly-once ledger over ``side_effect_ledger``."""

    _table = "side_effect_ledger"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    @staticmethod
    def idempotency_key(
        task_id: str,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        """Static alias of the module-level :func:`idempotency_key`.

        This is the OWNER-AGNOSTIC logical identity of a call — deterministic
        across replays for the same (task, step, tool, args). The *stored*
        primary key additionally folds in the bound owner (see
        :meth:`_owned_key`) so two principals can never collide on the shared
        ``idempotency_key`` PK.
        """
        return idempotency_key(task_id, step_index, tool_name, args)

    def _owned_key(
        self,
        task_id: str,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        """Owner-scoped storage key: the logical key folded with the owner id.

        Keeps each principal's ledger rows on disjoint primary keys so the
        owner-blind PK (``side_effect_ledger.idempotency_key``) can never
        cross-collide between owners while staying deterministic per owner.
        """
        logical = idempotency_key(task_id, step_index, tool_name, args)
        payload = "\x1f".join((self._owner_id, logical))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def begin(
        self,
        task_id: str,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
    ) -> LedgerDecision:
        """Open the intent for a side-effecting call and decide what to do.

        * If a ``committed`` row exists -> ``already_committed`` carrying the
          recorded result (caller must NOT re-execute).
        * Else if an ``intent`` row already exists -> ``uncertain`` (a prior
          attempt may have died after intent but before commit).
        * Else write a fresh ``intent`` row and return ``proceed``.
        """
        # 1. ENTRY
        key = self._owned_key(task_id, step_index, tool_name, args)
        log.tasks.debug(
            "[tasks] ledger.begin: entry",
            extra={"_fields": {
                "task_id": task_id, "step_index": step_index,
                "tool_name": tool_name, "owner_id": self._owner_id, "key": key,
            }},
        )
        rows = await self._fetch_owned(
            self._table, "idempotency_key = ?", (key,)
        )
        # 2. DECISION — branch on any existing row's status
        if rows:
            existing = rows[0]
            status = str(existing["status"])
            if status == _STATUS_COMMITTED:
                raw = existing.get("result_blob")
                result = None if raw is None else str(raw)
                log.tasks.info(
                    "[tasks] ledger.begin: already committed — skip execute",
                    extra={"_fields": {"task_id": task_id, "key": key}},
                )
                return LedgerDecision(outcome="already_committed", result=result)
            # An intent without a commit: outcome is uncertain.
            log.tasks.warning(
                "[tasks] ledger.begin: existing intent — uncertain",
                extra={"_fields": {"task_id": task_id, "key": key, "status": status}},
            )
            return LedgerDecision(outcome="uncertain", result=None)
        # 3. STEP — no prior row: record the intent and proceed
        await self._insert_owned(self._table, {
            "idempotency_key": key,
            "task_id": task_id,
            "owner_id": self._owner_id,
            "step_index": step_index,
            "tool_name": tool_name,
            "status": _STATUS_INTENT,
            "result_blob": None,
            "created_at": datetime.now(tz=UTC).isoformat(),
        })
        # 4. EXIT
        log.tasks.debug(
            "[tasks] ledger.begin: exit — intent written, proceed",
            extra={"_fields": {"task_id": task_id, "key": key}},
        )
        return LedgerDecision(outcome="proceed", result=None)

    async def commit(
        self,
        task_id: str,
        step_index: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        """Mark the step's ledger row ``committed`` and record its result.

        Owner-scoped UPDATE keyed by the deterministic idempotency key. After
        this, a re-``begin`` of the same logical call returns
        ``already_committed`` with ``result``.
        """
        # 1. ENTRY
        key = self._owned_key(task_id, step_index, tool_name, args)
        log.tasks.debug(
            "[tasks] ledger.commit: entry",
            extra={"_fields": {
                "task_id": task_id, "step_index": step_index,
                "tool_name": tool_name, "owner_id": self._owner_id,
                "key": key, "result_len": len(result),
            }},
        )
        # 2. DECISION — mark this idempotency key as committed so future
        #    re-begins return already_committed without re-executing the tool
        log.tasks.debug(
            "[tasks] ledger.commit: marking key committed",
            extra={"_fields": {
                "task_id": task_id, "step_index": step_index,
                "tool_name": tool_name, "key": key,
            }},
        )
        # 3. STEP — owner-scoped UPDATE (helper rejects SQL without owner_id)
        await self._execute_owned(
            f"UPDATE {self._table} SET status = ?, result_blob = ? "  # noqa: S608 — table from class, columns literal
            "WHERE owner_id = ? AND idempotency_key = ?",
            (_STATUS_COMMITTED, result, self._owner_id, key),
        )
        # 4. EXIT
        log.tasks.info(
            "[tasks] ledger.commit: committed",
            extra={"_fields": {"task_id": task_id, "key": key}},
        )
