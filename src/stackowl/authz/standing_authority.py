"""Standing authority -- the one place FR32's "owner explicitly pre-approved"
can ever come from (Story 4.6, AD-27, NFR30/NFR31).

WRITABLE ONLY THROUGH authz/ (NFR30). :func:`grant`/:func:`revoke` are the
ONLY two functions that ever write the ``standing_authority`` table --
``tests/authz/test_standing_authority_has_one_writer.py``'s AST scan fails
any file outside ``authz/``, ``commands/spec/`` and a subsystem's own
``*/commands.py`` handler module that imports either name. In practice both
are called ONLY from ``authz/commands.py``'s ``authority.grant``/
``authority.revoke`` ``CommandSpec`` handlers -- and those two command types
are declared ``severity="consequential"``, so ``action_policy.decide()``'s
own FIRST-CHECKED rule (CONSEQUENTIAL always needs step-up, for every
requester kind) already refuses every path here without an explicit
Telegram-mediated approval, with zero new branch (FR37: "never
self-granted ... never through voice").

Every write also lands in the hash-chained ``audit_log`` (NFR31, via
``stackowl.audit.logger.chain_append_via_pool``) and records an
``authority.granted``/``authority.revoked`` journal event, in the SAME
transaction as the ``standing_authority`` row itself (AD-24) -- the same
"row + audit_log + journal event, one commit" shape
``scheduler/scheduler.py::JobScheduler.pause`` already uses via
``scheduler_helpers.py::write_audit``'s ``conn=`` chokepoint, just opened
here directly (via ``DbPool.transaction()``) rather than threaded in from a
caller, since ``grant``/``revoke`` are each the top-level DB entry point for
their own write -- no caller already holds an open transaction the way a
COMMAND-driven mutator invoked mid-handler does.

:func:`find_active` is read-only and NOT reached by any live dispatch path
this story (spec Boundaries: "do not wire a live DB lookup of
standing_authority into submit_command/execute_command_task's dispatch
path") -- it is proven entirely by this story's own direct I/O tests, for a
future story (4.7+) to call once an actually-irreversible, autonomously-
dispatched command type exists.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from stackowl.audit.logger import chain_append_via_pool
from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef
from stackowl.journal import record as journal_record
from stackowl.journal.authority_events import AuthorityGrantedAttrs, AuthorityRevokedAttrs

#: AD-27's standing-authority scope vocabulary -- only ``"job"`` today (a
#: scheduled job's own declared ``preauthorized_command_types``, this
#: story's other half). Additive-only for whatever 4.7+ scopes an
#: irreversible autonomous command by next.
ScopeKind = Literal["job"]

_TABLE = "standing_authority"

#: ``authz.requester.RequesterKind`` -> ``journal.ActorKind`` -- COPIED, not
#: imported, from ``scheduler/scheduler.py::_REQUESTER_KIND_TO_ACTOR_KIND``.
#: AD-7 forbids ``authz/`` depending on ``scheduler/`` (a subsystem);
#: ``journal/records.py``'s own docstring names this exact "reused
#: structure, not reused values" precedent for the identical reason. An
#: unrecognized value falls back to AUTONOMOUS -- fail-safe, never a KeyError.
_REQUESTER_KIND_TO_ACTOR_KIND: dict[str, ActorKind] = {
    "owner": ActorKind.OWNER,
    "owl": ActorKind.OWL,
    "autonomous": ActorKind.AUTONOMOUS,
    "voice-unverified": ActorKind.VOICE_WORKER,
}


def _actor_kind_for(requester_kind: str) -> ActorKind:
    return _REQUESTER_KIND_TO_ACTOR_KIND.get(requester_kind, ActorKind.AUTONOMOUS)


@dataclass(frozen=True)
class StandingAuthorityRecord:
    """One ``standing_authority`` row."""

    id: str
    scope_kind: ScopeKind
    scope_id: str
    command_type: str
    granted_by: str
    provenance: str
    granted_at: str
    revoked_at: str | None = None


def _target(scope_kind: str, scope_id: str, command_type: str) -> str:
    """The ``audit_log``/journal ``target`` string for one scope+command-type
    pair -- deterministic, so a grant and its later revoke share one target.

    JSON-encoded (never a hand-joined ``f"{a}:{b}:{c}"``) so a ``:`` inside
    ``scope_id``/``command_type`` can never make two DIFFERENT triples
    collide on the same target string -- ``json.dumps`` always escapes a
    literal ``:`` inside a string element, so ``["job", "a:b", "c"]`` and
    ``["job", "a", "b:c"]`` serialize to two distinct strings.
    """
    return json.dumps([scope_kind, scope_id, command_type], separators=(",", ":"))


async def grant(
    db: DbPool,
    *,
    scope_kind: ScopeKind,
    scope_id: str,
    command_type: str,
    granted_by: str,
    # Story 4.8 — widened to include "seeded" (a platform-seeded job's own
    # delivery authority, granted by the same startup routine that grandfathers
    # existing enabled jobs, never a human decision this story either).
    # Deferred from 4.6 pending real evidence a live caller needed it — 4.8's
    # `scheduler/assembly.py` seed-time grant calls are that caller.
    provenance: Literal["granted", "grandfathered", "seeded"] = "granted",
) -> StandingAuthorityRecord:
    """Write one new ACTIVE ``standing_authority`` row.

    Never checks for or closes an existing active grant for the same
    ``(scope_kind, scope_id, command_type)`` first -- a second call simply
    adds a second active row; :func:`find_active` always reads back
    whichever is newest (``ORDER BY granted_at DESC``). Closing an OLD grant
    is what :func:`revoke` is for, called explicitly.
    """
    authority_id = uuid.uuid4().hex
    granted_at = datetime.now(UTC).isoformat()
    target = _target(scope_kind, scope_id, command_type)
    # 1. ENTRY
    log.engine.debug(
        "[authz] standing_authority.grant: entry",
        extra={"_fields": {
            "scope_kind": scope_kind, "scope_id": scope_id,
            "command_type": command_type, "granted_by": granted_by,
        }},
    )
    # 3. STEP -- the row, the journal event and the audit_log row commit or
    # roll back together (AD-24).
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO standing_authority "
            "(id, scope_kind, scope_id, command_type, granted_by, provenance, "
            "granted_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (authority_id, scope_kind, scope_id, command_type, granted_by,
             provenance, granted_at),
        )
        await journal_record(conn, JournalEvent(
            type="authority.granted", schema_version=1,
            actor_kind=_actor_kind_for(granted_by), actor_id=granted_by,
            target_kind=ActorKind.OWNER, target_id=target, outcome=Outcome.OK,
            record_ref=RecordRef(
                kind="sqlite", locator={"table": _TABLE, "id": authority_id},
            ),
            attrs=AuthorityGrantedAttrs(
                scope_kind=scope_kind, scope_id=scope_id,
                command_type=command_type, granted_by=granted_by,
            ),
        ))
        await chain_append_via_pool(
            db, "authority_granted", granted_by, target, time.time(),
            json.dumps(
                {
                    "id": authority_id, "scope_kind": scope_kind,
                    "scope_id": scope_id, "command_type": command_type,
                    "provenance": provenance,
                },
                separators=(",", ":"), sort_keys=True,
            ),
            conn=conn,
        )
    # 4. EXIT
    log.engine.info(
        "[authz] standing_authority.grant: exit",
        extra={"_fields": {"id": authority_id, "target": target}},
    )
    return StandingAuthorityRecord(
        id=authority_id, scope_kind=scope_kind, scope_id=scope_id,
        command_type=command_type, granted_by=granted_by, provenance=provenance,
        granted_at=granted_at, revoked_at=None,
    )


async def revoke(
    db: DbPool,
    *,
    scope_kind: ScopeKind,
    scope_id: str,
    command_type: str,
    revoked_by: str,
) -> StandingAuthorityRecord | None:
    """Close EVERY currently-ACTIVE ``standing_authority`` row for
    ``(scope_kind, scope_id, command_type)``, if any exist.

    Closes all of them, not just the one :func:`find_active` happened to
    return -- :func:`grant` never dedupes against an existing active grant
    (its own docstring), so more than one active row for the same triple can
    exist. Closing only the newest would leave the older one(s) active and
    :func:`find_active` would keep reporting authority as granted after a
    "successful" revoke -- a real correctness bug for a signal whose whole
    purpose is authoritative on/off.

    Returns ``None`` -- never raises -- when none is active: revoking an
    already-revoked or never-granted authority is simply nothing to close,
    the same idempotent-no-op shape ``commands/spec/undo.py::request_undo``
    already uses for "nothing to undo".
    """
    target = _target(scope_kind, scope_id, command_type)
    # 1. ENTRY
    log.engine.debug(
        "[authz] standing_authority.revoke: entry",
        extra={"_fields": {
            "scope_kind": scope_kind, "scope_id": scope_id,
            "command_type": command_type, "revoked_by": revoked_by,
        }},
    )
    # 2. DECISION -- read the current active grant BEFORE opening the
    # transaction (mirrors pipeline/durable/store.py's own "read state, then
    # transact" shape) -- nothing to revoke is the common, non-transactional
    # case.
    existing = await find_active(
        db, scope_kind=scope_kind, scope_id=scope_id, command_type=command_type,
    )
    if existing is None:
        log.engine.info(
            "[authz] standing_authority.revoke: exit -- no active grant",
            extra={"_fields": {"target": target}},
        )
        return None
    revoked_at = datetime.now(UTC).isoformat()
    async with db.transaction() as conn:
        # Matches every currently-active row for this (scope_kind, scope_id,
        # command_type) triple -- NOT `WHERE id = ?` -- so a leftover
        # undeduplicated second grant (see this function's own docstring)
        # can never survive a revoke.
        cursor = await conn.execute(
            "UPDATE standing_authority SET revoked_at = ? "
            "WHERE scope_kind = ? AND scope_id = ? AND command_type = ? "
            "AND revoked_at IS NULL",
            (revoked_at, scope_kind, scope_id, command_type),
        )
        if cursor.rowcount == 0:
            # A concurrent revoke won the race between the read above and
            # this UPDATE -- nothing left for THIS call to close.
            log.engine.info(
                "[authz] standing_authority.revoke: exit -- already revoked "
                "concurrently",
                extra={"_fields": {"id": existing.id, "target": target}},
            )
            return None
        await journal_record(conn, JournalEvent(
            type="authority.revoked", schema_version=1,
            actor_kind=_actor_kind_for(revoked_by), actor_id=revoked_by,
            target_kind=ActorKind.OWNER, target_id=target, outcome=Outcome.OK,
            record_ref=RecordRef(
                kind="sqlite", locator={"table": _TABLE, "id": existing.id},
            ),
            attrs=AuthorityRevokedAttrs(
                scope_kind=scope_kind, scope_id=scope_id,
                command_type=command_type, revoked_by=revoked_by,
            ),
        ))
        await chain_append_via_pool(
            db, "authority_revoked", revoked_by, target, time.time(),
            json.dumps(
                {
                    "id": existing.id, "scope_kind": scope_kind,
                    "scope_id": scope_id, "command_type": command_type,
                },
                separators=(",", ":"), sort_keys=True,
            ),
            conn=conn,
        )
    # 4. EXIT
    log.engine.info(
        "[authz] standing_authority.revoke: exit -- revoked",
        extra={"_fields": {"id": existing.id, "target": target}},
    )
    return replace(existing, revoked_at=revoked_at)


async def find_active(
    db: DbPool, *, scope_kind: ScopeKind, scope_id: str, command_type: str,
) -> StandingAuthorityRecord | None:
    """The newest ACTIVE grant for ``(scope_kind, scope_id, command_type)``,
    or ``None``. Read-only -- see this module's docstring for why nothing
    live calls this yet."""
    log.engine.debug(
        "[authz] standing_authority.find_active: entry",
        extra={"_fields": {
            "scope_kind": scope_kind, "scope_id": scope_id,
            "command_type": command_type,
        }},
    )
    rows = await db.fetch_all(
        "SELECT id, granted_by, provenance, granted_at, revoked_at FROM "
        "standing_authority WHERE scope_kind = ? AND scope_id = ? AND "
        "command_type = ? AND revoked_at IS NULL ORDER BY granted_at DESC LIMIT 1",
        (scope_kind, scope_id, command_type),
    )
    if not rows:
        log.engine.debug(
            "[authz] standing_authority.find_active: exit -- none active",
            extra={"_fields": {
                "scope_kind": scope_kind, "scope_id": scope_id,
                "command_type": command_type,
            }},
        )
        return None
    row = rows[0]
    record = StandingAuthorityRecord(
        id=str(row["id"]), scope_kind=scope_kind, scope_id=scope_id,
        command_type=command_type, granted_by=str(row["granted_by"]),
        provenance=str(row["provenance"]), granted_at=str(row["granted_at"]),
        revoked_at=row["revoked_at"],
    )
    log.engine.debug(
        "[authz] standing_authority.find_active: exit -- active grant found",
        extra={"_fields": {"id": record.id}},
    )
    return record
