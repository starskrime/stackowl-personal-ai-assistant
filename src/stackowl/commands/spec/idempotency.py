"""``record_command_execution`` -- the AD-26 receipt guard a subsystem mutator
writes into its OWN transaction, before applying its mutation (Story 4.3).

``command_receipts`` (migration 0151) is a plain INSERT-OR-IGNORE table keyed
by ``command_id`` -- the same "cheap, boring, provably-correct" shape
``idx_tasks_idempotency_live`` (migration 0124) already uses for the durable
task loop's own idempotency guard. Recording the receipt and applying the
mutation MUST share one ``DbPool.transaction()`` connection (this function
takes the live ``conn``, never a ``DbPool``, mirroring ``journal.record``'s own
contract) -- that is what makes "the mutator writes command_id in its own
transaction so a lease-reclaim re-run is a no-op" true rather than aspirational.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# AD-7 — see registry.py's identical import for why `log` is allowed here.
from stackowl.infra.observability import log

# `conn` is typed `Any` rather than `aiosqlite.Connection`: this package's
# import boundary is `authz/` + `pipeline/durable` + stdlib/pydantic +
# `stackowl.infra`, and `aiosqlite` is a third-party driver, none of those.


async def record_command_execution(
    conn: Any, command_id: str, command_type: str,
) -> bool:
    """Record that *command_id* is about to execute, on the caller's OWN
    already-open transaction connection.

    Returns ``True`` the FIRST time a given ``command_id`` is recorded (the
    caller should proceed with its mutation) and ``False`` on every
    subsequent call for the SAME ``command_id`` (a lease-reclaim re-run, or
    any other replay -- the caller must short-circuit: no-op, never re-apply
    the mutation or re-record the domain event).
    """
    # 1. ENTRY
    log.tasks.debug(
        "[commands] idempotency.record_command_execution: entry",
        extra={"_fields": {"command_id": command_id, "command_type": command_type}},
    )
    cursor = await conn.execute(
        "INSERT OR IGNORE INTO command_receipts (command_id, command_type, executed_at) "
        "VALUES (?, ?, ?)",
        (command_id, command_type, datetime.now(UTC).isoformat()),
    )
    newly_recorded = bool(cursor.rowcount == 1)
    # 4. EXIT
    log.tasks.info(
        "[commands] idempotency.record_command_execution: exit",
        extra={"_fields": {
            "command_id": command_id, "command_type": command_type,
            "newly_recorded": newly_recorded,
        }},
    )
    return newly_recorded
