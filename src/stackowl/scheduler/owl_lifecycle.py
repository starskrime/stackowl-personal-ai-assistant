"""Owl-lifecycle → scheduler projection (UniOwl ADR-B, Story S9).

The owl manifest is the SINGLE SOURCE OF TRUTH; the ``jobs`` rows it implies are a
DERIVED PROJECTION, reconciled idempotently. This module NEVER imperatively pokes a
scheduler row as a side effect of saving an owl — it RECONCILES: for every
``lifecycle="scheduled"`` owl it idempotently upserts exactly one owl-owned row
(provenance-marked ``params['source']='owl_lifecycle'``, ``params['owner']=<name>``),
and it deletes every owl-owned row whose owl is gone / now on-demand / retired. A
hand-made cronjob (``params['created_by']='cronjob'``, no ``source`` marker) is NEVER
touched. Running it twice with no change is a no-op (stable, no duplicate rows).

Called at boot (after the owl registry is loaded + revalidated and the job store is
open) and immediately after every owl create / edit / retire, so a change takes
effect without a reboot. The projected job keys on the owl's STABLE name and
re-reads the owl spec at fire time (handler reads ``params``); no mutable spec is
snapshotted beyond the trigger fields it needs.

Trigger → handler: ``cron`` → ``goal_execution`` (runs ``params['goal']``),
``watch`` → ``website_watch`` (polls ``params['url']``), ``threshold`` →
``threshold_watch`` (polls ``params['source']``, fires on a predicate edge).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.owl_schedule_guards import (
    MAX_SCHEDULED_OWLS,
    OWL_LIFECYCLE_SOURCE,
    interval_floor_error,
)
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler_helpers import (
    compute_next_run,
    insert_job,
    row_to_job,
    write_audit,
)

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.owls.registry import OwlRegistry

# Trigger.kind → the handler that consumes its projected row.
_KIND_TO_HANDLER: dict[str, str] = {
    "cron": "goal_execution",
    "watch": "website_watch",
    "threshold": "threshold_watch",
}


@dataclass(frozen=True)
class ReconcileResult:
    """What one reconcile pass changed — for logging + test assertions."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0


def _job_id_for(name: str) -> str:
    """Deterministic, stable job id for an owl's projected row (keyed on name).

    A stable id makes the upsert keyed-by-name (no duplicate row on re-projection)
    and makes a delete-then-recreate reuse the same id — the row IS the owl's.
    """
    return f"{OWL_LIFECYCLE_SOURCE}-{name}"


def _desired(owl: OwlAgentManifest) -> tuple[str, str, dict[str, object]] | None:
    """Map a scheduled owl to ``(handler_name, schedule, content_params)`` or None.

    ``None`` means "do not project" (no trigger, or an unknown kind). ``content_params``
    carries only the handler's input (goal/url/source+predicate) + owl attribution —
    the provenance marker is added by the caller so it is identical on every row.
    """
    trig = owl.trigger
    if trig is None:
        return None
    handler = _KIND_TO_HANDLER.get(trig.kind)
    if handler is None:
        log.scheduler.info(
            "[owls] reconcile: trigger kind has no handler — skipped",
            extra={"_fields": {"owl": owl.name, "kind": trig.kind}},
        )
        return None
    if trig.kind == "cron":
        return handler, trig.schedule, {"goal": trig.prompt, "owl": owl.name}
    if trig.kind == "watch":
        # website_watch reads params['url']; schedule from the trigger.
        return handler, trig.schedule, {"url": trig.target, "owl": owl.name}
    if trig.kind == "threshold":
        # threshold_watch reads params['watch_source'/'op'/'threshold'/'prompt'];
        # 'owner' is added by _params_for. NB: the param key is 'watch_source', NOT
        # 'source' — 'source' is RESERVED for the provenance marker added by
        # _params_for, so reusing it would clobber the owl-ownership tag.
        return handler, trig.schedule, {
            "watch_source": trig.source,
            "op": trig.op,
            "threshold": trig.threshold,
            "prompt": trig.prompt,
            "owl": owl.name,
        }
    return None  # unreachable (all kinds handled) — keeps types total


def _params_for(owl: OwlAgentManifest, content: dict[str, object]) -> dict[str, object]:
    """The full projected ``params`` = content + the provenance marker."""
    return {**content, "source": OWL_LIFECYCLE_SOURCE, "owner": owl.name}


def _content_differs(existing: Job, handler: str, schedule: str, content: dict[str, object]) -> bool:
    """True when the live row no longer matches the owl's desired projection."""
    if existing.handler_name != handler or existing.schedule != schedule:
        return True
    return any(existing.params.get(k) != v for k, v in content.items())


async def reconcile_owl_schedules(
    owl_registry: OwlRegistry,
    db: DbPool,
    *,
    tz: str = "UTC",
    settings: Settings | None = None,
) -> ReconcileResult:
    """Idempotently project every scheduled owl into exactly one owned ``jobs`` row.

    Manifest = truth; rows = projection. Upserts owned rows for scheduled owls,
    deletes owned rows for gone/on-demand/retired owls, never touches hand-made
    cronjobs, and is a no-op on a second run with no change. Fail-safe per owl
    (one bad owl never aborts the pass). Returns a :class:`ReconcileResult`.
    """
    log.scheduler.info("[owls] reconcile_owl_schedules: entry")
    rows = await db.fetch_all("SELECT * FROM jobs")
    owned: dict[str, Job] = {}
    for raw in rows:
        job = row_to_job(raw)
        if job.params.get("source") == OWL_LIFECYCLE_SOURCE:
            owner = job.params.get("owner")
            if isinstance(owner, str):
                owned[owner] = job

    target_channels, target_addresses = _resolve_target(settings)

    # Desired set — scheduled owls with a projectable trigger, capped by quota.
    # An owl that ALREADY owns a row is always kept (cap is soft for existing,
    # hard for new) so the cap never thrashes a running job. Deterministic order.
    scheduled = sorted(
        (m for m in owl_registry.all() if m.lifecycle == "scheduled"),
        key=lambda m: m.name,
    )
    result = ReconcileResult()
    desired_names: set[str] = set()

    for owl in scheduled:
        mapped = _desired(owl)
        if mapped is None:
            result = _bump(result, skipped=1)
            continue
        handler, schedule, content = mapped
        floor_err = interval_floor_error(schedule)
        if floor_err is not None:
            log.scheduler.warning(
                "[owls] reconcile: refusing sub-floor schedule — not projected",
                extra={"_fields": {"owl": owl.name, "schedule": schedule, "reason": floor_err}},
            )
            result = _bump(result, skipped=1)
            continue
        if len(desired_names) >= MAX_SCHEDULED_OWLS and owl.name not in owned:
            log.scheduler.warning(
                "[owls] reconcile: scheduled-owl quota reached — new owl not projected",
                extra={"_fields": {"owl": owl.name, "cap": MAX_SCHEDULED_OWLS}},
            )
            result = _bump(result, skipped=1)
            continue
        desired_names.add(owl.name)
        try:
            changed = await _upsert(db, owl, handler, schedule, content, owned, tz,
                                    target_channels, target_addresses)
            result = _bump(result, created=changed[0], updated=changed[1])
        except Exception as exc:  # B5 / fail-safe — one bad owl never aborts the pass
            log.scheduler.error(
                "[owls] reconcile: upsert failed for owl — skipping",
                exc_info=exc,
                extra={"_fields": {"owl": owl.name}},
            )
            result = _bump(result, skipped=1)

    # Delete owl-owned rows whose owl is gone / on-demand / retired / unprojected.
    for owner, job in owned.items():
        if owner in desired_names:
            continue
        try:
            await db.execute("DELETE FROM jobs WHERE job_id = ?", (job.job_id,))
            await write_audit(db, "owl_schedule_deleted", job.job_id, actor="reconcile",
                              details={"owner": owner})
            result = _bump(result, deleted=1)
            log.scheduler.info(
                "[owls] reconcile: deleted orphaned owl-owned row",
                extra={"_fields": {"owner": owner, "job_id": job.job_id}},
            )
        except Exception as exc:  # B5 — never silent, never abort the loop
            log.scheduler.error(
                "[owls] reconcile: delete failed for orphaned row",
                exc_info=exc,
                extra={"_fields": {"owner": owner, "job_id": job.job_id}},
            )

    log.scheduler.info(
        "[owls] reconcile_owl_schedules: exit",
        extra={"_fields": {
            "created": result.created, "updated": result.updated,
            "deleted": result.deleted, "skipped": result.skipped,
        }},
    )
    return result


async def _upsert(
    db: DbPool,
    owl: OwlAgentManifest,
    handler: str,
    schedule: str,
    content: dict[str, object],
    owned: dict[str, Job],
    tz: str,
    target_channels: list[str],
    target_addresses: dict[str, str | int],
) -> tuple[int, int]:
    """Insert or update one owl's projected row. Returns ``(created, updated)`` 0/1."""
    params = _params_for(owl, content)
    existing = owned.get(owl.name)
    if existing is None:
        job = Job(
            job_id=_job_id_for(owl.name),
            handler_name=handler,
            schedule=schedule,
            idempotency_key=f"{OWL_LIFECYCLE_SOURCE}:{owl.name}",
            last_run_at=None,
            # Future-dated by construction (every/daily/cron all next-future), so
            # boot never backfills a missed run — coalesces to ≤1 catch-up.
            next_run_at=compute_next_run(schedule, tz=tz),
            status="pending",
            params=params,
            target_channels=target_channels,
            target_addresses=target_addresses,
        )
        await insert_job(db, job)
        await write_audit(db, "owl_schedule_created", job.job_id, actor="reconcile",
                          details={"owner": owl.name, "handler": handler})
        log.scheduler.info(
            "[owls] reconcile: created owl-owned row",
            extra={"_fields": {"owner": owl.name, "handler": handler, "schedule": schedule}},
        )
        return 1, 0

    if not _content_differs(existing, handler, schedule, content):
        return 0, 0

    # Recompute the cadence slot ONLY when the schedule itself changed (a goal/url
    # edit must not reset a running job's next fire). Re-enable + re-arm so an edit
    # recovers a job the circuit-breaker had paused.
    next_run = (
        existing.next_run_at if existing.schedule == schedule else compute_next_run(schedule, tz=tz)
    )
    await db.execute(
        "UPDATE jobs SET handler_name = ?, schedule = ?, params = ?, next_run_at = ?, "
        "status = 'pending', enabled = 1, failure_count = 0, last_error = NULL WHERE job_id = ?",
        (
            handler,
            schedule,
            json.dumps(params, separators=(",", ":"), sort_keys=True),
            next_run,
            existing.job_id,
        ),
    )
    await write_audit(db, "owl_schedule_updated", existing.job_id, actor="reconcile",
                      details={"owner": owl.name, "handler": handler})
    log.scheduler.info(
        "[owls] reconcile: updated owl-owned row in place",
        extra={"_fields": {"owner": owl.name, "handler": handler, "schedule": schedule}},
    )
    return 0, 1


def _resolve_target(settings: Settings | None) -> tuple[list[str], dict[str, str | int]]:
    """Resolve the owner's durable proactive recipient for a scheduled owl's output.

    A scheduled owl fires with no live session, so (like a cron brief) its answer
    must address durable config. Reuses the shared owner→native-token resolver over
    the configured brief channels. No resolvable owner → empty (the handler records
    the result honestly as undeliverable, never a fake "delivered").
    """
    if settings is None:
        return [], {}
    try:
        from stackowl.notifications.recipient import resolve_owner_addresses

        channels = list(settings.brief.channels)
        addresses = resolve_owner_addresses(settings, channels)
    except Exception as exc:  # B5 — target resolution must never break reconcile
        log.scheduler.warning(
            "[owls] reconcile: owner-target resolution failed — rows unaddressed",
            exc_info=exc,
        )
        return [], {}
    if not addresses:
        return [], {}
    return list(addresses.keys()), dict(addresses)


def _bump(
    r: ReconcileResult, *, created: int = 0, updated: int = 0, deleted: int = 0, skipped: int = 0
) -> ReconcileResult:
    """Return a new result with the given counters incremented (frozen dataclass)."""
    return ReconcileResult(
        created=r.created + created,
        updated=r.updated + updated,
        deleted=r.deleted + deleted,
        skipped=r.skipped + skipped,
    )
