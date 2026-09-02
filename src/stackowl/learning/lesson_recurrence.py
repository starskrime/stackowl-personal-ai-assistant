"""The lesson did not work: a failure signature that recurs AFTER its own fix.

WHAT THIS IS, AND WHOSE DECISION IT IS. Bakir, 2026-09-02, choosing what may
interrupt him: **"Page on repeat signatures."** Not "unresolved" — 86 of 100 RCAs
conclude verified=False, so paging on those would take him from ~12 pages a day to
~70 — and not "gave up" either. The event worth an interrupt is the SAME failure
signature recurring after a fix was shipped for it, because that is the
self-healing loop failing, which is the one thing no amount of reading a brief
tomorrow will tell him in time.

MEASURED BEFORE BUILDING, over 2,000 failed outcomes across 30 days:

    shell/stop                10 recurrences after its fix (of 23 total)
    browser_navigate/stop      6 recurrences after its fix (of 21)
    shell/unachieved_effect    5 recurrences after its fix (of 33)
                              --
                              21 recurrences, over the ~3.5 days since those
                                 skills were authored

Twenty-one recurrences is 6 pages a day if every OCCURRENCE pages. It is THREE
pages ever if the SIGNATURE pages. A signature that recurs after its fix is one
fact worth one interrupt, not one per failure — and that distinction is the whole
difference between this and the thing he rejected. It becomes payable again only
when a NEW fix ships for the same signature and that one fails too, which is
exactly the event he asked to hear about.

WHY THE ALERT NAMES THE OWNER. All three signatures above belong to skills that
had NO owl owning them, so no owl was ever shown the lesson. Of course it did not
work. Had this detector existed on 2026-08-30 it would have said so on day one
instead of the defect being found by hand three days later. The owner is
therefore part of the message, not a detail: "the lesson recurred AND nobody owns
it" and "the lesson recurred and its owner ignored it" are different problems and
must not arrive looking the same.

NO NEW STORE. The dedup rides ``audit_log`` keyed on ``actor`` = the signature,
which is the same rail ``incident.diagnosed`` and ``capability.escalated`` already
use — "so a gap that fires every run alerts once rather than every sweep".
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Iterable, Mapping, Sequence

    from stackowl.learning.failure_outcome_miner import FailureCluster

#: The audit event that records "we told him this lesson is not working".
RECURRENCE_EVENT = "incident.lesson_recurred"


def outcome_signature(capability_class: str, failure_class: str) -> str:
    """The signature string for an outcome-lane incident.

    ONE SOURCE. This spelling is what ``audit_log.actor`` carries for every
    diagnosis, so a second copy of it here would dedupe against a key nothing
    else writes — a silent, permanent double-page.
    """
    return f"outcome:{capability_class}:{failure_class}"


@dataclass(frozen=True)
class Recurrence:
    """One signature whose own fix did not hold."""

    signature: str
    capability_class: str
    failure_class: str
    skill_name: str
    #: When the fix shipped. Part of the dedup key: a NEW fix that also fails is
    #: a new fact and pages again.
    fixed_at: float
    #: Failures of this signature strictly after ``fixed_at``.
    recurrences: int
    #: Owls that hit it since the fix — who the lesson was supposed to reach.
    owls: tuple[str, ...]
    #: Whether ANY owl owns the skill. False means the lesson was never presented
    #: to anyone, which is a different problem from a lesson that was ignored.
    has_owner: bool

    def brief(self) -> str:
        """What he reads on his phone. Names the fix, the count and the owner."""
        who = ", ".join(self.owls) if self.owls else "unknown"
        if self.has_owner:
            verdict = (
                f"The lesson '{self.skill_name}' is owned and still did not hold."
            )
        else:
            verdict = (
                f"The lesson '{self.skill_name}' is owned by NOBODY — no owl was "
                f"ever shown it, so it could not have worked."
            )
        return (
            f"{self.capability_class}/{self.failure_class} has failed "
            f"{self.recurrences} more time(s) since a fix was written for it. "
            f"{verdict} Affected: {who}."
        )


async def detect_recurrences(
    clusters: Iterable[FailureCluster],
    fixes: Mapping[str, float],
    owned_skills: Iterable[str],
    *,
    skill_names_for: object,
) -> list[Recurrence]:
    """Every signature that failed again after its own fix shipped.

    Args:
        clusters: Live failure clusters, from the miner's own clustering.
        fixes: skill name -> when it was authored.
        owned_skills: Skill names that at least one owl owns.
        skill_names_for: Callable ``(capability_class, failure_class) -> set[str]``
            giving the skill spellings that fix this signature. Injected rather
            than spelled here, because the miner owns the naming rule.
    """
    owned = set(owned_skills)
    out: list[Recurrence] = []
    for cluster in clusters:
        candidates = {
            n: fixes[n]
            for n in skill_names_for(  # type: ignore[operator]
                cluster.capability_class, cluster.failure_class,
            )
            if n in fixes
        }
        if not candidates:
            continue  # no fix was ever written — an ordinary incident, not this
        # THE EARLIEST fix. A signature is "fixed" from the moment the first
        # lesson for it existed; using the latest would reset the clock every
        # time the miner re-authored and hide a lesson that never worked.
        skill_name = min(candidates, key=lambda n: candidates[n])
        fixed_at = candidates[skill_name]
        after = [
            o for o in cluster.outcomes
            if float(getattr(o, "captured_at", 0) or 0) > fixed_at
        ]
        if not after:
            continue
        out.append(Recurrence(
            signature=outcome_signature(
                cluster.capability_class, cluster.failure_class,
            ),
            capability_class=cluster.capability_class,
            failure_class=cluster.failure_class,
            skill_name=skill_name,
            fixed_at=fixed_at,
            recurrences=len(after),
            owls=tuple(sorted({
                str(o.owl_name) for o in after if getattr(o, "owl_name", None)
            })),
            has_owner=any(n in owned for n in candidates),
        ))
    return out


async def already_paged(db: object) -> dict[str, float]:
    """signature -> the fix timestamp he was last told about.

    FAILS TOWARD PAGING, and that is deliberate. An unreadable ledger returns
    empty, so a signature pages again rather than being silently suppressed by a
    query error — the failure mode this whole arc exists to prevent. The cost of
    the wrong direction here is one duplicate message; the cost of the other is a
    self-healing loop that fails in silence.
    """
    try:
        rows = await db.fetch_all(  # type: ignore[attr-defined]
            "SELECT actor, details FROM audit_log WHERE event_type = ? "
            "AND actor IS NOT NULL",
            (RECURRENCE_EVENT,),
        )
    except Exception as exc:  # noqa: BLE001 — a ledger read may not cost a tick
        log.scheduler.warning(
            "[recurrence] already_paged: ledger unreadable — paging as if nothing "
            "had been reported before",
            exc_info=exc,
        )
        return {}
    out: dict[str, float] = {}
    for r in rows:
        try:
            fixed = float(json.loads(str(r["details"] or "{}")).get("fixed_at") or 0)
        except Exception:  # noqa: BLE001 — one bad row may not hide the rest
            fixed = 0.0
        sig = str(r["actor"])
        out[sig] = max(out.get(sig, 0.0), fixed)
    return out


def unreported(
    recurrences: Sequence[Recurrence], reported: Mapping[str, float],
) -> list[Recurrence]:
    """The ones he has not been told about for THIS fix.

    Compared on the fix timestamp, not on the signature alone: a signature whose
    fix was replaced and failed again is a NEW fact and pages again. A signature
    still failing against the same old fix is the same fact and stays quiet.
    """
    return [r for r in recurrences if reported.get(r.signature, -1.0) < r.fixed_at]


async def record_paged(db: object, rec: Recurrence, *, now: float | None = None) -> None:
    """Write the dedup marker. Never raises: a ledger write may not cost a tick."""
    stamp = time.time() if now is None else now
    try:
        await db.execute(  # type: ignore[attr-defined]
            "INSERT INTO audit_log (event_type, actor, target, timestamp, details, "
            "integrity_hash, chain_version) VALUES (?,?,?,?,?,?,?)",
            (RECURRENCE_EVENT, rec.signature, rec.skill_name, stamp,
             json.dumps({
                 "fixed_at": rec.fixed_at, "recurrences": rec.recurrences,
                 "has_owner": rec.has_owner, "owls": list(rec.owls),
             }), "", "v1"),
        )
    except Exception as exc:  # noqa: BLE001
        log.scheduler.warning(
            "[recurrence] record_paged: could not write the marker — this "
            "signature will page again on the next tick",
            exc_info=exc, extra={"_fields": {"signature": rec.signature}},
        )
