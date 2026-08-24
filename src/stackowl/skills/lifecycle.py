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
  * **Built-ins decay too**, on the same windows, with pinning as the protection
    (D09.3 R2Q6). This reverses the original exclusion and is the operator's
    explicit consent, which the never-disable-a-feature rule requires: 9 of 14
    built-ins have never run, and archival is recoverable so being wrong costs
    one un-archive.
  * **The first pass is deferred.** On a catalog that has never been curated,
    the first run records that it observed the catalog and changes nothing, so
    an operator gets a full interval to review and pin before anything moves.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
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

#: Days without use before a stale skill is archived out of the catalog. 2x the
#: stale window, so a skill must be unused across an entire stale period before
#: it leaves. Shortened from 90 (D09.3 R1Q4): the reversible transition has now
#: proved itself on the live catalog — 297 skills marked stale, none lost — and
#: 92% of the catalog being dead weight makes three months of ranking pollution
#: a real cost.
ARCHIVE_AFTER_DAYS = 60.0

#: ESC-45 — THE MOMENT SKILLS BECAME VISIBLE, and the floor under every idle
#: clock. 2026-08-23T18:17:47Z, the boot at which ESC-44's value-ordered
#: catalogue went live (measured: the last alphabetically-cut truncation record
#: is 17:59:41Z presenting 11, the first value-ordered one is 18:34 presenting 12).
#:
#: WHY A CLOCK NEEDS A FLOOR AT ALL. Idle time is charged to a skill as evidence
#: that nobody wanted it. Until ESC-44 the catalogue was cut ALPHABETICALLY at
#: ~12 of 160, so 92 stale skills had never been shown to anyone — their idle
#: clocks were measuring RETRIEVAL's failure and billing it to them, and they sat
#: ~51 days into a 60-day archive window because of it.
#:
#: It had already happened once: 8 of 14 shipped BUILTINS are archived for
#: non-use, plan-and-track and recover-and-retry among them. They were not
#: chosen and rejected; they were never offered.
#:
#: A skill cannot have been ignored before it could be seen. Bakir chose "reset
#: the clock, then let decay run" — so this buys a fair window, NOT immunity: a
#: skill visible for a full archive window and still unused ages out normally.
#: It also expires by itself, becoming a no-op once the window has passed.
VISIBILITY_FLOOR_EPOCH = 1787509067.0  # 2026-08-23T18:17:47Z

#: The QUALITY trigger, absorbed from the synthesizer's deprecate path (X11).
#: A skill that has been used enough to have a verdict and fails most of the
#: time is retired without waiting out the unused windows — "nobody used it" and
#: "it was used and did not work" are different findings with the same remedy.
#: Values carried over unchanged from synthesizer._DEPRECATE_BELOW /
#: _MIN_EXECUTIONS_FOR_RATE so behaviour is preserved, not redesigned.
FAILING_BELOW = 0.4
MIN_RUNS_FOR_RATE = 5

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
    #: X11 — archived for failing, as opposed to for going unused. Kept apart in
    #: the report because they are different findings about the catalog: "we
    #: write skills nobody needs" vs "we write skills that do not work".
    archived_failing: list[str] = field(default_factory=list)
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
            f"revived {len(self.revived)}, failing {len(self.archived_failing)}, "
            f"pinned-skipped {self.skipped_pinned})"
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
        thresholds: Callable[[], Awaitable[Mapping[str, float]]] | None = None,
    ) -> None:
        self._store = store
        # AD-7 survives the move from the synthesizer as a per-skill threshold
        # map the CALLER produces (owls.skill_ownership.owl_drive_thresholds).
        #
        # A PROVIDER, not a mapping, for one reason: ``completion_drive`` is
        # mutated by the evolution jobs, and this curator is constructed once at
        # assembly and then lives for the process's lifetime. A map captured in
        # __init__ would freeze boot-time drives and quietly stop tracking the
        # trait it exists to follow. Resolved once per pass instead.
        #
        # A skill absent from the map uses the flat floor — "no owning owl" and
        # "no opinion" are the same thing — and the curator stays owl-agnostic.
        self._thresholds = thresholds
        self._failing_below: Mapping[str, float] = {}
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
        self._failing_below = await self._resolve_thresholds()

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
            failing = self._is_failing(row)
            target = self._target_state(row, idle, failing=failing)
            if target == row.lifecycle_state:
                continue

            if target == STALE:
                report.to_stale.append(row.name)
            elif target == ARCHIVED:
                report.to_archived.append(row.name)
                if failing:
                    report.archived_failing.append(row.name)
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
                "archived_failing": len(report.archived_failing),
                "skipped_pinned": report.skipped_pinned,
                "dry_run": dry_run,
                # Names, capped: an operator reading this at 2am needs to know
                # WHICH skills left the catalog, not just how many.
                "archived_names": report.to_archived[:20],
            }},
        )
        return report

    async def _resolve_thresholds(self) -> Mapping[str, float]:
        """Per-skill failure floors for this pass, or the flat floor for all.

        Fails SOFT. A threshold provider that raises must cost us the owl-drive
        nudge, never the whole decay pass — the nudge is advisory (AD-7 is
        explicitly "additive weight on an existing threshold, never a new
        veto"), so degrading to the flat floor is the correct, stated fallback.
        """
        if self._thresholds is None:
            return {}
        try:
            resolved = await self._thresholds()
        except Exception as exc:  # B5
            log.skills.warning(
                "[curator] threshold provider failed — this pass uses the flat "
                "failure floor for every skill",
                exc_info=exc, extra={"_fields": {"flat_floor": FAILING_BELOW}},
            )
            return {}
        log.skills.debug(
            "[curator] thresholds resolved",
            extra={"_fields": {"adjusted_skills": len(resolved)}},
        )
        return resolved

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
        # ESC-45 — never age a skill from before it could be SEEN. See
        # VISIBILITY_FLOOR_EPOCH: until ESC-44 the catalogue was cut
        # alphabetically, so an unshown skill's idle time measured retrieval's
        # failure rather than its own uselessness.
        #
        # A FLOOR RATHER THAN A DATA MIGRATION, deliberately. Bumping `loaded_at`
        # on the affected rows would mutate a provenance field to mean something
        # it does not, and would be a one-shot to get right in a single pass over
        # live data. This needs no migration, is idempotent, and stops mattering
        # by itself once the window has passed.
        return max(now - max(anchor, VISIBILITY_FLOOR_EPOCH), 0.0)

    def _is_failing(self, row: _CurationRow) -> bool:
        """The QUALITY trigger (X11): used enough to have a verdict, and losing.

        ``n_executions`` gates the rate so a single early failure cannot retire a
        skill, and ``success_rate is None`` — no verdict yet — is never failing.
        Both were true of the synthesizer path this replaces; changing either
        here would be a silent behaviour change wearing a refactor's clothes.
        """
        if row.n_executions < MIN_RUNS_FOR_RATE or row.success_rate is None:
            return False
        return row.success_rate < self._failing_below.get(row.name, FAILING_BELOW)

    def _target_state(self, row: _CurationRow, idle: float, *, failing: bool) -> str:
        """The state ``row`` should be in.

        PRIORITY ORDER — ordering is behaviour, and ordering is what regresses:

        1. ``pinned`` never reaches here; the caller skips it entirely.
        2. FAILING beats everything else. A skill with enough runs to have a
           verdict and a success rate below the floor is archived even if it ran
           this morning — that is the whole point of the trigger, and if recent
           use outranked it the trigger could never fire at all.
        3. Age applies last, longest window first, so archive is tested before
           stale and a very old skill is not merely marked stale forever.
        4. Recent use falls through to ACTIVE, which is the revival path.
        """
        if failing:
            return ARCHIVED
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
    #: X11 — the quality trigger needs the measured rate. None means "no verdict
    #: yet", which must never be read as failing.
    success_rate: float | None = None
    #: Built-ins decay on the same windows as learned skills (D09.3 R2Q6), with
    #: pinning as the protection. Carried so the report can distinguish them.
    source: str = "learned"
