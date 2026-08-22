"""CapabilityGapEscalationHandler — tell the operator when an owl keeps being blocked.

BAKIR, 2026-08-22, cutting through a side-issue I had wandered into: "Why are we
doing anything for system health agent? It is not a root cause of issue."

He was right. THE ROOT CAUSE, MEASURED over three days: 85 bounds refusals — 36 on
08-20, 37 on 08-21, 12 by midday on 08-22 — and not one of them ever reached him.

    mailbutler   shell              24
    mailbutler   browser_navigate    7
    mailbutler   execute_code        7
    mailbutler   owl_build           4   <- the appeal path itself
    syshealth    send_message        3   <- once per scheduled run, every run
    sysdesign    web_search          2

`mailbutler` needs `shell`. It asks on every run, is refused, reports honestly, and
begins the next run knowing nothing. Twenty-four times. That is what "my agents
still failing on doing their jobs" looks like from the inside, and no amount of
self-healing could fix it because the decision — should this owl HAVE this tool —
is the operator's and he was never asked.

WHY IT STAYED INVISIBLE. The refusal WAS recorded, by
`tool_outcome_ledger.record_denied_capability`, into a ContextVar that `reset()`
clears when the turn ends. Its single reader is the honest-reporting path. So the
platform knew the owl was blocked while the turn ran and forgot the moment it
finished: nothing accumulated, so nothing could cross a threshold, so nothing ever
asked. Per-turn state doing a durable job — this codebase's first shape, one scope
too narrow rather than absent.

WHAT THIS DOES. `execute` now also appends `capability.denied` to `audit_log`
(append-only, integrity-chained, already home to `consent.decision` and
`job_failed_terminal`). This handler reads those rows, groups them by owl+tool, and
raises a gap that has recurred at least `min_occurrences` times to the operator
ONCE — with the exact `owl_build` command that would grant it.

ONCE IS THE WHOLE POINT. A gap fires on every run by definition, so a naive alert
would send `mailbutler shell` twenty-four times and train its reader to ignore the
channel — the cries-wolf failure this codebase already pays for elsewhere. An
escalation is therefore itself audited (`capability.escalated`), and a gap already
escalated is skipped until it recurs beyond the count it was raised at.

NO NEW ENGINE. One existing store (`audit_log`), one existing loop (the scheduler),
one existing delivery path (`ProactiveJobDeliverer`) — per the standing rule that
work is a task on the one loop and nothing may duplicate what already runs.

WHAT IT DELIBERATELY DOES NOT DO: grant anything. Widening an owl's bounds is an
authority decision, and the platform deciding its own authority is the inversion
that started this whole arc. It reports; the operator grants.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.audit.logger import AuditLogger
    from stackowl.db.pool import DbPool
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer

#: Refusals of the SAME owl+tool before the operator is asked. One refusal is an
#: owl probing what it has; a handful is a capability it actually needs and cannot
#: get. Set low because the cost of asking is one message and the cost of NOT
#: asking was measured at 24 silent failures.
_DEFAULT_MIN_OCCURRENCES = 3

#: How far back to look. Long enough that a gap recurring a few times a day is
#: caught, short enough that a tool an owl stopped needing weeks ago stops nagging.
_DEFAULT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class CapabilityGap:
    """One owl's repeated need for one tool it does not have."""

    owl: str
    tool: str
    occurrences: int

    def grant_command(self) -> str:
        """The exact thing the operator can run to close it."""
        return f"owl_build edit {self.owl} --allow-tool {self.tool}"


def within_ceiling(manifest: object, tool: str) -> bool:
    """True when ``tool`` is inside the owl's creation_ceiling but not its bounds.

    THE WHOLE SELF-HEALING DISTINCTION. Bakir, 2026-08-22: "Why platform does not
    have capability to self heal himself instead you manually grant access to".

    The ceiling is the authority the operator ALREADY delegated when the owl was
    minted — for an operator-created owl it is `SAFE_DEFAULT_CEILING`, which is
    read-only-ish by construction and deliberately excludes shell/exec/write. So
    widening bounds UP TO the ceiling grants nothing new: it is the platform using
    authority it was already given. Only CROSSING the ceiling is new authority, and
    that still goes to the operator.

    `owl_build` did not draw this line — its widening path "always asks the user"
    for every tool, inside the ceiling or not. That is why a blocked owl needed a
    human for something the human had already approved.
    """
    bounds = getattr(manifest, "bounds", None)
    ceiling = getattr(manifest, "creation_ceiling", None)
    if ceiling is None or getattr(ceiling, "tools", None) is None:
        return False  # unbounded ceiling is not a licence to self-grant
    if tool not in ceiling.tools:
        return False
    if bounds is None or getattr(bounds, "tools", None) is None:
        return False  # already unbounded — nothing to widen
    return tool not in bounds.tools


async def find_recurring_gaps(
    db: DbPool, *, min_occurrences: int, window_days: int
) -> list[CapabilityGap]:
    """Every owl+tool pair refused in the window and not already raised.

    RETURNS ALL OF THEM, including single refusals — ``min_occurrences`` is applied
    LATER and only to escalation. The threshold exists to protect the operator's
    attention, so it has no business gating a self-heal he is never told about: a
    tool already inside the ceiling was authorised at mint time, and making an owl
    fail three more times before using it buys nothing. `sysdesign` runs DAILY, so
    a threshold of 3 would have left a within-ceiling gap open for three days.

    A pair already carrying `capability.escalated` is skipped, so a gap that fires
    every run alerts once rather than every sweep.
    """
    since = time.time() - (window_days * 86_400)
    rows = await db.fetch_all(
        "SELECT event_type, actor, target FROM audit_log "
        "WHERE event_type IN ('capability.denied', 'capability.escalated') "
        "AND timestamp >= ? AND target IS NOT NULL",
        (since,),
    )
    denied: Counter[tuple[str, str]] = Counter()
    escalated_at: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["actor"]), str(row["target"]))
        if row["event_type"] == "capability.denied":
            denied[key] += 1
        else:
            escalated_at[key] = escalated_at.get(key, 0) + 1

    gaps: list[CapabilityGap] = []
    for (owl, tool), count in denied.items():
        # Escalated once already, and it has not meaningfully worsened since.
        if escalated_at.get((owl, tool)):
            continue
        gaps.append(CapabilityGap(owl=owl, tool=tool, occurrences=count))
    gaps.sort(key=lambda g: (-g.occurrences, g.owl, g.tool))
    return gaps


async def heal_within_ceiling(gap: CapabilityGap) -> bool:
    """Widen ``gap.owl``'s bounds to include ``gap.tool``, never past the ceiling.

    Returns True when the grant landed and was PERSISTED. Best-effort by contract:
    a self-heal that cannot be written must report False rather than claim a grant
    the next restart would lose — the exact failure that made grants look forgotten.

    Reuses `persist_owl` + `registry.replace` (the pair fixed on 2026-08-22 when
    `register` was silently refusing to overwrite an existing owl) rather than
    writing a second persistence path.
    """
    from stackowl.commands.owls_helpers import persist_owl, snapshot_owl
    from stackowl.pipeline.services import get_services

    manifest = await snapshot_owl(gap.owl)
    if manifest is None:
        log.scheduler.warning(
            "[scheduler] capability_gap_escalation: owl vanished before self-heal",
            extra={"_fields": {"owl": gap.owl, "tool": gap.tool}},
        )
        return False
    if not within_ceiling(manifest, gap.tool):
        return False  # re-checked at the moment of writing, not just at selection

    bounds = manifest.bounds
    if bounds is None or bounds.tools is None:
        # `within_ceiling` already guarantees both, but the invariant is restated
        # locally rather than asserted at a distance: the two calls are separated by
        # an await, and a bounds set that is None here would mean UNBOUNDED — the
        # one state where "widening" would quietly do the opposite of what it says.
        return False
    widened = bounds.model_copy(update={"tools": frozenset(bounds.tools) | {gap.tool}})
    # BELT AND BRACES: intersect with the ceiling so a bug above can never write
    # bounds that exceed it. The clamp is the invariant; the check is the intent.
    from stackowl.authz.bounds_guard import effective_bounds

    clamped = effective_bounds(widened, manifest.creation_ceiling)
    updated = manifest.model_copy(update={"bounds": clamped})
    try:
        await persist_owl(updated)
    except Exception as exc:  # noqa: BLE001 — a failed write must never claim success
        log.scheduler.error(
            "[scheduler] capability_gap_escalation: self-heal could not be persisted",
            exc_info=exc,
            extra={"_fields": {"owl": gap.owl, "tool": gap.tool}},
        )
        return False
    registry = getattr(get_services(), "owl_registry", None)
    if registry is not None:
        with contextlib.suppress(Exception):
            registry.replace(updated)
    log.scheduler.info(
        "[scheduler] capability_gap_escalation: SELF-HEALED — bounds widened to a "
        "tool the creation ceiling already allowed; no operator approval needed",
        extra={"_fields": {
            "owl": gap.owl, "tool": gap.tool, "occurrences": gap.occurrences,
        }},
    )
    return True


def render_gap_message(gaps: list[CapabilityGap]) -> str:
    """One message for all gaps — not one per gap.

    Six separate notifications for six gaps is the same flood, differently shaped.
    """
    lines = [
        "Some of your agents keep asking for tools they do not have, and I cannot "
        "grant those myself — widening an owl's bounds is your decision.",
        "",
    ]
    for gap in gaps:
        times = "time" if gap.occurrences == 1 else "times"
        lines.append(
            f"• {gap.owl} needs {gap.tool} — refused {gap.occurrences} {times}"
        )
    lines.append("")
    lines.append("To grant one:")
    for gap in gaps:
        lines.append(f"  {gap.grant_command()}")
    return "\n".join(lines)


class CapabilityGapEscalationHandler(JobHandler):
    """Raise repeatedly-refused capabilities to the operator, once each.

    Optional job ``params``: ``{"min_occurrences": 3, "window_days": 7}``.
    """

    def __init__(
        self,
        db: DbPool,
        audit_logger: AuditLogger | None = None,
        job_deliverer: ProactiveJobDeliverer | None = None,
    ) -> None:
        self._db = db
        self._audit = audit_logger
        self._job_deliverer = job_deliverer

    @property
    def handler_name(self) -> str:
        return "capability_gap_escalation"

    async def execute(self, job: Job) -> JobResult:
        # 1. ENTRY
        t0 = time.monotonic()
        min_occurrences = int(job.params.get("min_occurrences", _DEFAULT_MIN_OCCURRENCES))
        window_days = int(job.params.get("window_days", _DEFAULT_WINDOW_DAYS))
        log.scheduler.info(
            "[scheduler] capability_gap_escalation.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id, "min_occurrences": min_occurrences,
                "window_days": window_days,
            }},
        )

        # 2. DECISION
        try:
            gaps = await find_recurring_gaps(
                self._db, min_occurrences=min_occurrences, window_days=window_days
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] capability_gap_escalation: could not read the audit log",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="read_only", success=False,
                output="", error=f"audit read failed: {exc}", duration_ms=duration_ms,
                metadata={"gaps": 0, "healed": 0, "escalated": 0},
            )

        if not gaps:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.info(
                "[scheduler] capability_gap_escalation.execute: exit — no new gaps",
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="read_only", success=True,
                output="gaps=0 escalated=0", error=None, duration_ms=duration_ms,
                metadata={"gaps": 0, "healed": 0, "escalated": 0},
            )

        # 3a. SELF-HEAL FIRST. A gap whose tool is already inside the owl's
        # creation ceiling needs no operator: that authority was delegated when the
        # owl was minted. Only what remains is genuinely new authority, and only
        # that is worth interrupting a human for.
        healed: list[CapabilityGap] = []
        remaining: list[CapabilityGap] = []
        for gap in gaps:
            try:
                if await heal_within_ceiling(gap):
                    healed.append(gap)
                    continue
            except Exception as exc:  # noqa: BLE001 — a failed heal escalates instead
                log.scheduler.warning(
                    "[scheduler] capability_gap_escalation: self-heal raised — "
                    "falling back to asking the operator",
                    exc_info=exc,
                    extra={"_fields": {"owl": gap.owl, "tool": gap.tool}},
                )
            remaining.append(gap)

        if healed and self._audit is not None:
            for gap in healed:
                with contextlib.suppress(Exception):
                    self._audit.append(
                        event_type="capability.self_healed",
                        actor=gap.owl, target=gap.tool,
                        details={"occurrences": gap.occurrences, "within_ceiling": True},
                    )

        if not remaining:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.info(
                "[scheduler] capability_gap_escalation.execute: exit — all gaps "
                "self-healed within the ceiling, operator not involved",
                extra={"_fields": {
                    "job_id": job.job_id, "healed": len(healed),
                    "duration_ms": duration_ms,
                }},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output=f"gaps={len(gaps)} healed={len(healed)} escalated=0",
                error=None, duration_ms=duration_ms,
                metadata={"gaps": len(gaps), "healed": len(healed),
                          "escalated": 0, "delivered": False},
            )

        # THE THRESHOLD APPLIES HERE AND ONLY HERE. A single refusal may be an owl
        # probing what it has; interrupting a human for that is the cries-wolf shape
        # this codebase already pays for. Nothing was healed for these, so they stay
        # unraised until they recur — and the sweep says so rather than dropping
        # them silently.
        below = [g for g in remaining if g.occurrences < min_occurrences]
        gaps = [g for g in remaining if g.occurrences >= min_occurrences]
        if below:
            log.scheduler.info(
                "[scheduler] capability_gap_escalation: gaps below the escalation "
                "threshold — held, not dropped",
                extra={"_fields": {
                    "job_id": job.job_id, "min_occurrences": min_occurrences,
                    "held": json.dumps([[g.owl, g.tool, g.occurrences] for g in below]),
                }},
            )
        if not gaps:
            duration_ms = (time.monotonic() - t0) * 1000
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change" if healed else "read_only",
                success=True,
                output=f"healed={len(healed)} escalated=0 held={len(below)}",
                error=None, duration_ms=duration_ms,
                metadata={"gaps": len(remaining), "healed": len(healed),
                          "escalated": 0, "delivered": False},
            )

        # 3b. STEP — one message for what is left, then stamp what was raised.
        log.scheduler.warning(
            "[scheduler] capability_gap_escalation: owls are repeatedly blocked on "
            "tools they do not have",
            extra={"_fields": {
                "job_id": job.job_id,
                "gaps": json.dumps([[g.owl, g.tool, g.occurrences] for g in gaps]),
            }},
        )
        delivered = False
        if self._job_deliverer is not None:
            outcome = await self._job_deliverer.deliver_for_job(
                job, message=render_gap_message(gaps),
                category="capability_gap", urgency="normal",
            )
            delivered = bool(getattr(outcome, "delivered", False)) or bool(
                getattr(outcome, "per_channel", None)
            )
        else:
            # NOT silent. A gap nobody can be told about is still a gap, and the
            # warning above is the record that it went unreported.
            log.scheduler.warning(
                "[scheduler] capability_gap_escalation: no deliverer wired — the "
                "gaps above could not be reported to the operator",
                extra={"_fields": {"job_id": job.job_id}},
            )

        escalated = 0
        if self._audit is not None:
            for gap in gaps:
                try:
                    self._audit.append(
                        event_type="capability.escalated",
                        actor=gap.owl, target=gap.tool,
                        details={"occurrences": gap.occurrences, "delivered": delivered},
                    )
                    escalated += 1
                except Exception as exc:  # noqa: BLE001
                    log.scheduler.warning(
                        "[scheduler] capability_gap_escalation: could not stamp an "
                        "escalation — it may be raised again next sweep",
                        exc_info=exc,
                        extra={"_fields": {"owl": gap.owl, "tool": gap.tool}},
                    )

        # 4. EXIT
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] capability_gap_escalation.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "gaps": len(gaps), "escalated": escalated,
                "delivered": delivered, "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="delivery", success=True,
            output=f"gaps={len(gaps)} escalated={escalated} delivered={delivered}",
            error=None, duration_ms=duration_ms,
            metadata={"gaps": len(gaps), "healed": len(healed),
                      "escalated": escalated, "delivered": delivered},
        )


def register_capability_gap_escalation_handler(
    db: DbPool,
    audit_logger: AuditLogger | None = None,
    job_deliverer: ProactiveJobDeliverer | None = None,
) -> None:
    """Construct + register the escalation on the process registry."""
    handler = CapabilityGapEscalationHandler(
        db=db, audit_logger=audit_logger, job_deliverer=job_deliverer
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] capability_gap_escalation handler registered",
        extra={"_fields": {
            "handler": handler.handler_name,
            "has_deliverer": job_deliverer is not None,
        }},
    )
