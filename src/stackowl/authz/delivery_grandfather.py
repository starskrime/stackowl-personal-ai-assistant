"""``grandfather_existing_job_delivery_authority`` -- Story 4.8's own migration
of EXISTING enabled jobs onto standing authority, explicitly deferred from
Story 4.6 pending both scheduling commands (4.7) and delivery commands (4.8
itself) existing first (epic-4-context, Cross-Story Dependencies).

Runs ONCE at boot, after migrations and scheduler assembly (``startup/
orchestrator.py``): every ``jobs.enabled = 1`` row whose ``handler_name`` owns
a declared delivery command type (Story 4.8's ``notifications/commands.py``)
gets a ``provenance="grandfathered"`` grant for that (job_id, command_type)
pair, so its next unattended run finds a matching standing-authority grant
instead of parking for step-up it has no way to answer (nothing is
"attending" a scheduler tick).

Idempotent (``find_active``-guarded, mirrors ``standing_authority.grant``'s
own "never dedupes, caller checks first" contract): a repeat boot never mints
a second active grant for the same pair.

NOT grandfathered: a disabled job (nothing runs unattended for it to need
authority), ``telegram_canary`` (a synthetic probe, not named in AC1's
scope), and every other ``handler_name`` this module was never asked to
authorize.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from stackowl.authz.standing_authority import find_active, grant
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from stackowl.db.pool import DbPool

#: Closed mapping -- a job's ``handler_name`` -> the ONE delivery command type
#: its own ``execute()`` now submits through (Story 4.8's Code Map, named
#: explicitly). NEVER derived from ``jobs.preauthorized_command_types`` --
#: that column is a job's own STATIC declaration (FR33), a different concept
#: this routine does not read.
HANDLER_DELIVERY_COMMAND_TYPES: Final[dict[str, str]] = {
    "morning_brief": "notifications.deliver_brief",
    "check_in": "notifications.deliver_check_in",
    "goal_execution": "notifications.deliver_goal_result",
    "notification_digest": "notifications.deliver_digest",
}

#: Boot-time, unattended -- the SAME requester-kind string a scheduler-driven
#: command run carries (``authz.requester.requester_kind_from_trace``'s
#: ``"autonomous"`` reading): grandfathering a job is the platform acting on
#: its own, never a human decision, so it is attributed the same way an
#: actual unattended run of that job later will be.
_GRANTED_BY = "autonomous"

_SELECT_ENABLED_JOBS_SQL = "SELECT job_id, handler_name FROM jobs WHERE enabled = 1"


async def seed_job_delivery_authority(db: DbPool, *, job_id: str, command_type: str) -> None:
    """Grant ``provenance="seeded"`` standing authority for a platform-seeded
    job's own delivery command type (Story 4.8), ``find_active``-guarded so a
    repeat boot never mints a second active grant for the SAME
    ``(job_id, command_type)`` pair.

    Lives HERE, not in ``scheduler/assembly.py`` — ``standing_authority.
    grant``/``.revoke`` are writable ONLY from ``authz/``, ``commands/spec/``
    or a subsystem's own ``*/commands.py`` handler module (NFR30, FR37,
    tripwire-enforced by ``tests/authz/test_standing_authority_has_one_
    writer.py``); ``scheduler/assembly.py`` is none of those, so it calls
    this wrapper instead of ``grant`` directly.
    """
    existing = await find_active(
        db, scope_kind="job", scope_id=job_id, command_type=command_type,
    )
    if existing is not None:
        log.engine.debug(
            "[authz] delivery_grandfather.seed_job_delivery_authority: "
            "already granted — noop",
            extra={"_fields": {"job_id": job_id, "command_type": command_type}},
        )
        return
    await grant(
        db, scope_kind="job", scope_id=job_id, command_type=command_type,
        granted_by=_GRANTED_BY, provenance="seeded",
    )
    log.engine.info(
        "[authz] delivery_grandfather.seed_job_delivery_authority: granted",
        extra={"_fields": {"job_id": job_id, "command_type": command_type}},
    )


async def grandfather_existing_job_delivery_authority(db: DbPool) -> int:
    """Idempotently grant standing authority for every enabled job's own
    delivery command type. Returns the number of grants actually created
    this call (0 on a steady-state re-run).
    """
    # 1. ENTRY
    log.engine.debug(
        "[authz] delivery_grandfather.grandfather_existing_job_delivery_authority: "
        "entry",
    )
    rows = await db.fetch_all(_SELECT_ENABLED_JOBS_SQL)
    granted = 0
    # 2/3. DECISION+STEP -- per enabled job whose handler owns a delivery
    # command type, grant unless an active grant already exists.
    for row in rows:
        handler_name = str(row["handler_name"])
        command_type = HANDLER_DELIVERY_COMMAND_TYPES.get(handler_name)
        if command_type is None:
            continue
        job_id = str(row["job_id"])
        existing = await find_active(
            db, scope_kind="job", scope_id=job_id, command_type=command_type,
        )
        if existing is not None:
            log.engine.debug(
                "[authz] delivery_grandfather: already granted — noop",
                extra={"_fields": {"job_id": job_id, "command_type": command_type}},
            )
            continue
        await grant(
            db, scope_kind="job", scope_id=job_id, command_type=command_type,
            granted_by=_GRANTED_BY, provenance="grandfathered",
        )
        granted += 1
        log.engine.info(
            "[authz] delivery_grandfather: grandfathered",
            extra={"_fields": {
                "job_id": job_id, "handler_name": handler_name,
                "command_type": command_type,
            }},
        )
    # 4. EXIT
    log.engine.info(
        "[authz] delivery_grandfather.grandfather_existing_job_delivery_authority: "
        "exit",
        extra={"_fields": {"considered": len(rows), "granted": granted}},
    )
    return granted
