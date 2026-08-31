"""record_deleted_rows — what a row contained, before it stops existing.

Bakir, 2026-08-31, deciding how an UNCAPPED reconciliation sweep stays safe:
"Snapshot the deleted rows before deleting." And on what the record must hold:
"Enough to reconstruct the row — table, primary key, the full row contents, and
WHY it was judged an orphan. A log saying 'deleted 148 rows' tells you the damage
happened and nothing about undoing it."

That sentence describes 2026-08-30 exactly. The purge audit row recorded
``count=151`` and named two backups that were never written; the skills were
recoverable only by luck, through a different facility
(``skill_audit.snapshot_json``) that happened to hold their bodies.

ONE TABLE, NOT A NEW ONE. ``audit_log`` is already the platform's general,
hash-chained audit — 11,053 rows spanning consent decisions, capability denials
and job failures, with ``integrity_hash``/``chain_version`` and a retention sweep
that audits its own pruning. Bakir: "a per-store audit table is that mistake in
miniature." So this is an EVENT TYPE on the log that exists, written through
``chain_append_via_pool``, the writer that exists — and it inherits the tamper
chain for free.

BEFORE THE DELETE, NOT AFTER, and the trade is stated rather than hidden. Writing
first means a delete that subsequently fails leaves a record of a deletion that
did not happen — misleading. Writing after means a crash between the two loses
the only copy of the data. His instruction was explicit ("then deletes"), and
losing the data is the worse of the two.

NEVER RAISES. Bookkeeping that can cost the operation it records is worse than no
bookkeeping: the same rule ``complete_turn_task`` and ``enqueue_turn_task``
already hold.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from stackowl.audit.logger import chain_append_via_pool
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

#: The event type. One value, so "what was deleted on this platform" is a single
#: query rather than a union over per-store tables.
DELETION_EVENT = "row.deleted"

#: Ceiling on the serialised payload. A skills row carries body_text and an
#: embedding; without a bound, one deletion could write half a megabyte into the
#: audit and the retention sweep would be cleaning up after the safety net.
#: Anything that only appends will poison its reader.
MAX_DETAILS_CHARS = 100_000


def _jsonable(value: Any) -> Any:
    """Coerce a column value to something JSON can hold.

    BLOBs (embeddings) and any other exotic type become a short marker rather
    than an exception — losing one column must not lose the whole record, which
    is the only reason the record exists.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


async def record_deleted_rows(
    db: DbPool,
    *,
    table: str,
    rows: list[dict[str, Any]],
    reason: str,
    actor: str,
) -> None:
    """Record the full contents of rows about to be deleted. Never raises.

    ``rows`` is what a ``SELECT *`` returned for the rows the caller is about to
    remove — the whole row, not its key, because a key alone cannot restore
    anything.
    """
    # 1. ENTRY
    log.tasks.debug(
        "[audit] record_deleted_rows: entry",
        extra={"_fields": {"table": table, "rows": len(rows)}},
    )
    # 2. DECISION — a sweep that found nothing must not fill the audit with
    # empty records; that is how a forensic table becomes unreadable.
    if not rows:
        return
    try:
        payload: dict[str, Any] = {
            "table": table,
            "reason": reason,
            "row_count": len(rows),
            "rows": [{k: _jsonable(v) for k, v in row.items()} for row in rows],
            "truncated": False,
        }
        details = json.dumps(payload, ensure_ascii=False)
        if len(details) > MAX_DETAILS_CHARS:
            # Keep the SHAPE and the count even when the contents will not fit —
            # a truncated record that says so beats a missing one that does not.
            payload["rows"] = [
                {k: (v if not isinstance(v, str) else v[:2000]) for k, v in row.items()}
                for row in payload["rows"]
            ]
            payload["truncated"] = True
            details = json.dumps(payload, ensure_ascii=False)[:MAX_DETAILS_CHARS]
        # 3. STEP — onto the shared chained log.
        await chain_append_via_pool(
            db,
            event_type=DELETION_EVENT,
            actor=actor,
            target=table,
            timestamp=time.time(),
            details_json=details,
        )
    except Exception as exc:
        # Every except logs, and this one must never propagate: the caller is
        # mid-delete and a failed record must not become a failed deletion.
        log.tasks.warning(
            "[audit] record_deleted_rows: could NOT record what was deleted — "
            "the deletion proceeds UNRECORDED",
            exc_info=exc,
            extra={"_fields": {"table": table, "rows": len(rows), "actor": actor}},
        )
        return
    # 4. EXIT
    log.tasks.info(
        "[audit] record_deleted_rows: exit — deletion recorded",
        extra={"_fields": {"table": table, "rows": len(rows), "actor": actor,
                           "reason": reason}},
    )
