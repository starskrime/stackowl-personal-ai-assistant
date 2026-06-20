"""Startup WIRING-CLOSURE audit (WS-E) — the meta-root guard for dangling half-edges.

The platform validated that scheduler handlers were REGISTERED but never that
they were REACHABLE. Three production bugs were all "dangling half-edges" that
shipped green: ``check_in`` was a registered+honest handler with NO producer
(nothing seeded its ``jobs`` row, so the poll loop never dispatched it); the
``event_bridge`` was a subscriber with no live publisher; ``goal_execution`` was
registered with a dangling delivery half. Nothing checked the closure of the
wiring graph, so this whole CLASS of bug was invisible until live.

:func:`audit_scheduler_wiring` is that closure check. It enumerates every
registered :class:`~stackowl.scheduler.base.JobHandler` and, for each "seeded"
handler, asserts a standing ``jobs`` row exists (else it is DANGLING — registered
but it will never fire). It also flags every subscribed event that has no
declared publisher. The audit is ADVISORY: it warns loudly per dangling item and
emits one consolidated summary, but NEVER raises — a degraded audit must not
block startup.

Runtime complement (the OTHER half of no-orphan-output): the runtime guarantee
that "a proactive producer with a deliverable resolves a destination or is
recorded undeliverable + logged loud" is satisfied by the durable seam, NOT here.
:meth:`stackowl.notifications.proactive_job.ProactiveJobDeliverer.deliver_for_job`
computes ``DeliverySpec.unresolved_channels()`` and rolls them up as
``undeliverable`` (loud warning), and the WS-B/WS-D handlers map that rollup to
honest job statuses. This module is the BOOT-time structural complement: it
catches a producer that will never fire at all, before any output exists.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.scheduler.base import HandlerRegistry

_JOBS_HANDLER_NAMES_SQL = "SELECT DISTINCT handler_name FROM jobs"


@dataclass
class WiringReport:
    """Result of a wiring-closure audit — the dangling half-edges + counts."""

    dangling_handlers: list[str] = field(default_factory=list)
    dangling_events: list[str] = field(default_factory=list)
    total_handlers: int = 0
    seeded: int = 0
    on_demand: int = 0
    event: int = 0


async def audit_scheduler_wiring(
    db: Any,
    registry: HandlerRegistry,
    *,
    allowed_events: Collection[str],
    declared_publishers: Collection[str],
) -> WiringReport:
    """Audit the scheduler/event wiring graph for dangling (unreachable) edges.

    A registered "seeded"-kind handler with no standing ``jobs`` row is DANGLING
    (it will never be dispatched). A subscribed event in ``allowed_events`` with
    no entry in ``declared_publishers`` is a dangling subscription (nobody emits
    it). Never raises — on any error (e.g. the ``jobs`` query fails) it logs and
    returns the best report it can, so startup is never blocked.

    :param db: a DbPool-like object exposing ``async fetch_all(sql, params)``.
    :param registry: the live :class:`HandlerRegistry`.
    :param allowed_events: the event_bridge ``_ALLOWED_EVENTS`` set.
    :param declared_publishers: events some module actually emits.
    """
    handlers = registry.all()
    # 1. ENTRY
    log.startup.debug(
        "[startup] wiring_audit.audit: entry",
        extra={"_fields": {
            "handlers": len(handlers),
            "allowed_events": len(allowed_events),
            "declared_publishers": len(declared_publishers),
        }},
    )

    report = WiringReport(total_handlers=len(handlers))

    # Query the DISTINCT seeded handler_names. A failure here must NOT crash boot:
    # we degrade to "cannot prove unreachability" (no dangling-handler assertions)
    # rather than raising. seeded_names stays None to signal the degraded state.
    seeded_names: set[str] | None = None
    try:
        rows = await db.fetch_all(_JOBS_HANDLER_NAMES_SQL, ())
        seeded_names = {str(r["handler_name"]) for r in rows}
        log.startup.debug(
            "[startup] wiring_audit.audit: seeded jobs queried",
            extra={"_fields": {"seeded_rows": len(seeded_names)}},
        )
    except Exception as exc:  # never silent, never fatal
        log.startup.warning(
            "[startup] wiring_audit.audit: jobs query failed — degraded audit "
            "(cannot verify seeded handlers; not blocking startup)",
            exc_info=exc,
            extra={"_fields": {}},
        )

    # 2. DECISION — classify each registered handler by its declared trigger_kind.
    for handler in handlers:
        kind = getattr(handler, "trigger_kind", "seeded")
        name = handler.handler_name
        if kind == "on_demand":
            report.on_demand += 1
            continue
        if kind == "event":
            report.event += 1
            continue
        # "seeded" (or any unknown kind, treated conservatively as seeded).
        report.seeded += 1
        if seeded_names is None:
            # Degraded: the jobs query failed — we cannot prove unreachability,
            # so we do NOT report a false dangling. Already warned above.
            continue
        if name not in seeded_names:
            report.dangling_handlers.append(name)
            log.startup.warning(
                "[startup] wiring_audit.audit: DANGLING handler %r — registered as "
                "'seeded' but has NO standing jobs row, so the poll loop will "
                "NEVER dispatch it. Seed it in SchedulerAssembly or override "
                "trigger_kind to 'on_demand'/'event'.",
                name,
                extra={"_fields": {"handler": name}},
            )

    # 3. STEP — events: a subscribed event with no declared publisher is dangling.
    declared = set(declared_publishers)
    for event in allowed_events:
        if event not in declared:
            report.dangling_events.append(event)
            log.startup.warning(
                "[startup] wiring_audit.audit: DANGLING event subscription %r — "
                "subscribed but NO module declares it as a publisher, so the "
                "subscriber will NEVER fire. Add a publisher (and register it in "
                "the declared-publishers set) or drop the subscription.",
                event,
                extra={"_fields": {"event": event}},
            )

    # 4. EXIT — one consolidated summary, ALWAYS.
    dangling_total = len(report.dangling_handlers) + len(report.dangling_events)
    log.startup.info(
        "[startup] wiring audit: %d handlers — %d seeded, %d on_demand, %d event; "
        "%d dangling",
        report.total_handlers,
        report.seeded,
        report.on_demand,
        report.event,
        dangling_total,
        extra={"_fields": {
            "total_handlers": report.total_handlers,
            "seeded": report.seeded,
            "on_demand": report.on_demand,
            "event": report.event,
            "dangling_handlers": report.dangling_handlers,
            "dangling_events": report.dangling_events,
        }},
    )
    return report
