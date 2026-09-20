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
    conn: Any, command_id: str, command_type: str, undo_payload: str | None = None,
) -> bool:
    """Record that *command_id* is about to execute, on the caller's OWN
    already-open transaction connection.

    Returns ``True`` the FIRST time a given ``command_id`` is recorded (the
    caller should proceed with its mutation) and ``False`` on every
    subsequent call for the SAME ``command_id`` (a lease-reclaim re-run, or
    any other replay -- the caller must short-circuit: no-op, never re-apply
    the mutation or re-record the domain event).

    ``undo_payload`` (Story 4.7, migration 0154) -- an OPTIONAL JSON string a
    mutator captures alongside its receipt, holding whatever "restore-to"
    state its own undo needs (e.g. a job's prior ``{schedule, goal}`` before
    an edit). ``None`` (every 4.3/4.5/4.6 caller) writes ``NULL``, byte-
    identical to before this column existed.
    """
    # 1. ENTRY
    log.tasks.debug(
        "[commands] idempotency.record_command_execution: entry",
        extra={"_fields": {
            "command_id": command_id, "command_type": command_type,
            "has_undo_payload": undo_payload is not None,
        }},
    )
    cursor = await conn.execute(
        "INSERT OR IGNORE INTO command_receipts "
        "(command_id, command_type, executed_at, undo_payload) VALUES (?, ?, ?, ?)",
        (command_id, command_type, datetime.now(UTC).isoformat(), undo_payload),
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
