"""SkillCurator — the DECAY leg of the self-improvement loop (ADR-19 #1).

WHY THIS EXISTS, measured 2026-08-05 on the live database:

    421 skills in the catalog
     33 ever executed (7.8%)
    208 executions in total
      0 ever retired

and the top of the usage table is four near-duplicates of one skill
(``structure-incident-evidence``, ``structure-incident-evidence-brief``,
``structure-evidence-brief``, ``evidence-brief-structuring``).

The platform's ability to CREATE skills works. Nothing measures whether they
earn their place, so the catalog has become 92% dead weight that still competes
for tool-search ranking and prompt space on every turn. An improvement loop
without decay poisons its own signal — that is the whole reason this module is
part of a self-IMPROVING system and not a housekeeping script.

WHAT IT IS NOT. It does not use an LLM, it does not merge or rewrite skills, and
it does not delete anything. Those are later, opt-in interventions (ADR-19 #5).
This pass is deterministic, cheap, reversible, and runs on the usage signal the
store already records — which is the one thing about our design that is ahead of
the reference platform's (it derives usage from file mtimes; we measure it).

THE FOUR SAFETY RULES, all of them ADR-19 invariants:

  * **Never delete** (I3). ``archived`` is terminal and fully recoverable — the
    row keeps its body, manifest, embedding and history.
  * **Pinned wins** (I4). A human veto outranks every automatic transition.
  * **Built-ins are not ours to retire.** Only ``source='learned'`` skills decay.
    A shipped built-in is a product decision, and silently archiving one would be
    disabling a feature nobody asked to disable.
  * **The first pass is deferred.** On a catalog that has never been curated,
    the first run records that it observed the catalog and changes nothing, so
    an operator gets a full interval to review and pin before anything moves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.skills.store import SkillIndexStore

__all__ = ["ACTIVE", "ARCHIVED", "STALE", "CuratorReport", "SkillCurator"]

ACTIVE = "active"
STALE = "stale"
ARCHIVED = "archived"

#: Days without use before a learned skill is marked ``stale``. Stale is a
#: RANKING signal, not a removal: the skill stays reachable and is restored to
#: ``active`` the moment it is used again.
STALE_AFTER_DAYS = 30.0

#: Days without use before a stale skill is archived out of the catalog. Chosen
#: as 3x the stale window so a skill must be unused across an entire stale
#: period before it leaves — measured against the live catalog, this archives
#: NOTHING on the first eligible run (283 of 379 unused skills are 30-90 days
#: old), which is deliberate: the mechanism proves itself on the reversible
#: transition before it performs the consequential one.
ARCHIVE_AFTER_DAYS = 90.0

_SECONDS_PER_DAY = 86_400.0


@dataclass
class CuratorReport:
    """What a pass did — or, in dry-run, what it WOULD do.

    Returned rather than logged-and-forgotten so the scheduler can report it,
    a CLI can preview it, and a test can assert on it. ADR-19 I6: a silent
    self-heal is indistinguishable from a system that never had the problem.
    """

    scanned: int = 0
    to_stale: list[str] = field(default_factory=list)
    to_archived: list[str] = field(default_factory=list)
    revived: list[str] = field(default_factory=list)
    skipped_pinned: int = 0
    skipped_builtin: int = 0
    deferred: bool = False
    dry_run: bool = False

    @property
    def changed(self) -> int:
        return len(self.to_stale) + len(self.to_archived) + len(self.revived)

    def summary(self) -> str:
        if self.deferred:
            return "first observation — catalog seeded, nothing changed"
        verb = "would change" if self.dry_run else "changed"
        return (
            f"scanned {self.scanned}, {verb} {self.changed} "
            f"(stale {len(self.to_stale)}, archived {len(self.to_archived)}, "
            f"revived {len(self.revived)}, pinned-skipped {self.skipped_pinned})"
        )


class SkillCurator:
    """Deterministic skill lifecycle pass.

    Stateless apart from the store it is given, so a caller can run it dry as
    often as it likes. One public entry point: :meth:`run`.
    """

    def __init__(
        self,
        store: SkillIndexStore,
        *,
        stale_after_days: float = STALE_AFTER_DAYS,
        archive_after_days: float = ARCHIVE_AFTER_DAYS,
    ) -> None:
        self._store = store
        self._stale_after = stale_after_days * _SECONDS_PER_DAY
        self._archive_after = archive_after_days * _SECONDS_PER_DAY
        if self._archive_after <= self._stale_after:
            # Not a theoretical guard: inverted windows would archive a skill in
            # the same pass that first marked it stale, turning a reversible
            # signal into an irreversible-feeling one with no warning period.
            log.skills.error(
                "[curator] archive window is not longer than the stale window — "
                "clamping so archival can never happen in the same pass as stale",
                extra={"_fields": {
                    "stale_after_days": stale_after_days,
                    "archive_after_days": archive_after_days,
                }},
            )
            self._archive_after = self._stale_after * 3.0

    async def run(self, *, dry_run: bool = False, now: float | None = None) -> CuratorReport:
        """One deterministic pass over the learned catalog."""
        now = time.time() if now is None else now
        report = CuratorReport(dry_run=dry_run)

        log.skills.debug(
            "[curator] run: entry",
            extra={"_fields": {"dry_run": dry_run}},
        )

        rows = await self._store.rows_for_curation()
        report.scanned = len(rows)

        # FIRST-PASS DEFERRAL. On a catalog that has never been curated, every
        # unused skill is simultaneously eligible, so the first run would be the
        # single largest change the curator ever makes — taken before anyone has
        # had a chance to pin anything. Seed the clock instead and do the real
        # work next interval. A dry run is exempt: previewing is the whole point.
        if not dry_run and not await self._store.curator_has_run():
            await self._store.mark_curator_ran(now)
            report.deferred = True
            log.skills.warning(
                "[curator] first observation — catalog seeded, nothing changed. "
                "The next pass will act; pin anything that must never be retired.",
                extra={"_fields": {"scanned": report.scanned}},
            )
            return report

        for row in rows:
            if row.pinned:
                report.skipped_pinned += 1
                continue

            idle = self._idle_seconds(row, now)
            target = self._target_state(row, idle)
            if target == row.lifecycle_state:
                continue

            if target == STALE:
                report.to_stale.append(row.name)
            elif target == ARCHIVED:
                report.to_archived.append(row.name)
            else:
                report.revived.append(row.name)

            if not dry_run:
                await self._store.set_lifecycle_state(row.skill_id, target, now)

        if not dry_run:
            await self._store.mark_curator_ran(now)

        # WARNING, not info: this changes what the agent can reach. ADR-19 I6.
        (log.skills.warning if report.changed else log.skills.info)(
            "[curator] run: exit — %s", report.summary(),
            extra={"_fields": {
                "scanned": report.scanned,
                "to_stale": len(report.to_stale),
                "to_archived": len(report.to_archived),
                "revived": len(report.revived),
                "skipped_pinned": report.skipped_pinned,
                "dry_run": dry_run,
                # Names, capped: an operator reading this at 2am needs to know
                # WHICH skills left the catalog, not just how many.
                "archived_names": report.to_archived[:20],
            }},
        )
        return report

    def _idle_seconds(self, row: _CurationRow, now: float) -> float:
        """How long since this skill was last useful.

        A skill that has run ages from its last use. One that never has ages
        from when it entered the catalog — otherwise a skill created and never
        used would be immortal, which is exactly the population that has grown
        to 92% of the catalog.
        """
        anchor = row.last_used_at if row.n_executions > 0 and row.last_used_at else row.loaded_at
        if not anchor:
            # No clock at all: refuse to age it rather than guess. A missing
            # timestamp must never be read as "infinitely old" — that would
            # archive on a data defect instead of on evidence.
            return 0.0
        return max(now - anchor, 0.0)

    def _target_state(self, row: _CurationRow, idle: float) -> str:
        """The state ``row`` should be in, given how long it has been idle."""
        if idle >= self._archive_after:
            return ARCHIVED
        if idle >= self._stale_after:
            # A skill already archived does NOT come back to stale on its own.
            # Archival is terminal by design; only real use revives it, which
            # the branch below handles.
            return ARCHIVED if row.lifecycle_state == ARCHIVED else STALE
        # Recently used (or recently added) — active. This is the revival path:
        # using an archived skill brings it back, which is what makes archival
        # safe to be aggressive about.
        return ACTIVE


@dataclass(frozen=True)
class _CurationRow:
    """The minimal projection a curation decision needs.

    Deliberately not the full ``SkillRecord``: the curator must never be able to
    read or write a skill's body, and a narrow row makes that structural rather
    than a rule someone has to remember.
    """

    skill_id: int
    name: str
    lifecycle_state: str
    pinned: bool
    n_executions: int
    last_used_at: float | None
    loaded_at: float | None
