"""SkillConsolidator — collapse ``-N`` duplicate families into one skill.

MEASURED ON THE LIVE CATALOG, 2026-08-08: 437 skills, 43 duplicate families
holding 312 rows, of which 269 are removable. One lesson —
``recover_tool_search_unachieved_effect`` — had been written twenty-one times,
and every one of those twenty-one copies still competed for tool-search ranking
and prompt space on every single turn.

WHY THE CATALOG GOT LIKE THIS. ``_cluster_already_covered`` deduped on the
EVIDENCE (parent trace ids), not on the conclusion, so the same lesson
re-derived from a new incident always looked new. The synthesizer needed a free
directory, appended ``-N``, and wrote it again. D10.2 closed the source — a
numeric suffix is now refused at the write. This module cleans up what was
already written.

DECAY IS NOT ENOUGH, which is worth stating because the earlier decision was
that it would be. The curator retires what goes UNUSED, and a duplicate family
is unused by construction: only one of twenty-one can ever rank first. But
decay leaves them at ``archived`` — still rows, still files, still 269 of them
— and it cannot merge the one member that DID earn its executions back into the
base name. Consolidation is the part decay structurally cannot do.

THE SAFETY MODEL, and it is deliberately not the curator's:

  * **Dry run by default.** ``apply=False`` plans and reports, touching nothing.
    Every other retirement path in this codebase is reversible; this one is not,
    so it does not act unless it is told to twice.
  * **A timestamped archive is taken BEFORE any delete**, outside the catalog
    root, so the loader can never rediscover it. This is what makes an
    irreversible operation recoverable in practice.
  * **The most-used member survives**, renamed to the base name, and inherits
    the family's SUMMED executions — otherwise consolidating a family would
    destroy the usage history that justified keeping that member, and the
    curator would then archive the survivor for looking unused.
  * **A pinned member wins the survivor election outright.** A human veto has to
    mean the same thing here as it does everywhere else.

DIVERGENCE FROM THE REFERENCE PLATFORM: theirs consolidates with an LLM judging
semantic similarity. This pass is deterministic and only collapses families that
share a base name — a strictly narrower claim. Semantic near-duplicates that do
NOT share a base name are out of scope and stay out of scope; merging those is a
content decision and belongs to the LLM migration pass, not here.
"""

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.skills import standard

if TYPE_CHECKING:
    from stackowl.skills.lifecycle import _CurationRow
    from stackowl.skills.manifest import SkillSource
    from stackowl.skills.store import SkillIndexStore

__all__ = ["ConsolidationPlan", "FamilyPlan", "SkillConsolidator"]

#: The ``name:`` line inside a SKILL.md frontmatter block. Anchored to the line
#: start and applied only to the leading ``---`` block, so a body that discusses
#: a "name:" field is never rewritten.
_FRONTMATTER_NAME_RE = re.compile(r"^name:\s*\S.*$", re.MULTILINE)


@dataclass(frozen=True)
class FamilyPlan:
    """What will happen to one ``-N`` family."""

    base: str
    source: SkillSource
    survivor: str
    survivor_id: int
    #: Why this member won — reported so a dry run can be argued with.
    reason: str
    removed: tuple[str, ...]
    #: Executions the survivor inherits: the family total, not its own.
    merged_executions: int
    #: True when the survivor is already correctly named and only siblings go.
    rename_needed: bool

    def describe(self) -> str:
        return (
            f"{self.base} ({self.source}): keep {self.survivor} "
            f"[{self.reason}], drop {len(self.removed)}, "
            f"executions -> {self.merged_executions}"
        )


@dataclass
class ConsolidationPlan:
    """The whole pass. Returned rather than logged so a CLI can print it and a
    test can assert on it before anything irreversible happens."""

    families: list[FamilyPlan] = field(default_factory=list)
    applied: bool = False
    archive_path: Path | None = None
    #: Families skipped, with the reason. Never silently dropped — a skipped
    #: family that goes unreported reads as "there was nothing to do".
    skipped: list[str] = field(default_factory=list)

    @property
    def rows_removed(self) -> int:
        return sum(len(f.removed) for f in self.families)

    def summary(self) -> str:
        verb = "consolidated" if self.applied else "would consolidate"
        return (
            f"{verb} {len(self.families)} families, removing {self.rows_removed} "
            f"skills ({len(self.skipped)} skipped)"
        )


class SkillConsolidator:
    """Plan and optionally apply ``-N`` family consolidation.

    One public entry point, :meth:`run`, which plans unconditionally and only
    mutates when ``apply=True``. Planning and applying deliberately share the
    same code path: a preview produced by different logic than the action is a
    preview of nothing.
    """

    def __init__(
        self,
        store: SkillIndexStore,
        skills_root: Path,
        *,
        archive_root: Path | None = None,
    ) -> None:
        self._store = store
        self._root = skills_root
        # OUTSIDE the catalog by default. Inside it, the loader's `_`-prefix skip
        # rule would be the only thing stopping rediscovery — one convention away
        # from resurrecting everything this pass just removed.
        self._archive_root = archive_root or skills_root.parent / "consolidated"

    async def run(self, *, apply: bool = False, stamp: str) -> ConsolidationPlan:
        """Plan, and if ``apply`` is set, carry the plan out.

        ``stamp`` names the archive directory. Passed in rather than read from
        the clock so a caller can correlate the archive with the run that made
        it, and so tests are deterministic.
        """
        # 1. ENTRY
        log.skills.debug(
            "[consolidate] run: entry",
            extra={"_fields": {"apply": apply, "stamp": stamp}},
        )
        plan = await self._plan()
        plan.applied = False

        if not apply:
            # 4. EXIT — preview
            log.skills.info(
                "[consolidate] run: exit (dry run) — %s", plan.summary(),
                extra={"_fields": {
                    "families": len(plan.families),
                    "rows_removed": plan.rows_removed,
                    "dry_run": True,
                }},
            )
            return plan

        if not plan.families:
            log.skills.info("[consolidate] run: exit — nothing to consolidate")
            return plan

        plan.archive_path = self._archive_root / stamp
        plan.archive_path.mkdir(parents=True, exist_ok=True)

        for family in plan.families:
            await self._apply_family(family, plan.archive_path)

        plan.applied = True
        # WARNING, not info: this deleted things. ADR-19 I6 — a silent
        # irreversible operation is indistinguishable from one that never ran.
        log.skills.warning(
            "[consolidate] run: exit — %s", plan.summary(),
            extra={"_fields": {
                "families": len(plan.families),
                "rows_removed": plan.rows_removed,
                "archive": str(plan.archive_path),
                "removed_names": [n for f in plan.families for n in f.removed][:40],
            }},
        )
        return plan

    # ------------------------------------------------------------------ plan

    async def _plan(self) -> ConsolidationPlan:
        plan = ConsolidationPlan()
        rows = await self._store.rows_for_curation()

        families: dict[tuple[str, str], list[_CurationRow]] = defaultdict(list)
        for row in rows:
            families[(row.source, standard.base_name(row.name))].append(row)

        for (source_str, base), members in sorted(families.items()):
            source: SkillSource = source_str  # type: ignore[assignment]  # from the DB
            if len(members) < 2:
                continue

            survivor, reason = self._elect(members)
            removed = tuple(sorted(m.name for m in members if m.name != survivor.name))
            if not removed:
                continue

            # A base-name collision the election cannot resolve safely: two
            # DIFFERENT pinned members. Refusing is correct — picking one would
            # override a human veto to satisfy a naming rule.
            pinned = [m for m in members if m.pinned]
            if len(pinned) > 1:
                plan.skipped.append(
                    f"{base} ({source}): {len(pinned)} pinned members — "
                    f"a human veto on each, so this family is not ours to collapse"
                )
                continue

            plan.families.append(FamilyPlan(
                base=base,
                source=source,
                survivor=survivor.name,
                survivor_id=survivor.skill_id,
                reason=reason,
                removed=removed,
                merged_executions=sum(m.n_executions for m in members),
                rename_needed=survivor.name != base,
            ))

        log.skills.debug(
            "[consolidate] plan: exit",
            extra={"_fields": {
                "families": len(plan.families),
                "rows_removed": plan.rows_removed,
                "skipped": len(plan.skipped),
            }},
        )
        return plan

    def _elect(self, members: list[_CurationRow]) -> tuple[_CurationRow, str]:
        """Pick the survivor, and say why.

        Order matters and is the whole decision:

        1. **Pinned.** A human veto outranks every measurement.
        2. **Most executions.** Usage is the only evidence we have that a member
           was ever the one that worked.
        3. **Already correctly named**, then the shortest name. A pure
           tie-breaker among members with identical evidence — deterministic so
           the same catalog always yields the same plan, which is what makes a
           dry run worth reading.
        """
        pinned = [m for m in members if m.pinned]
        if len(pinned) == 1:
            return pinned[0], "pinned"

        best = max(members, key=lambda m: m.n_executions)
        if best.n_executions > 0 and sum(
            1 for m in members if m.n_executions == best.n_executions
        ) == 1:
            return best, f"most used ({best.n_executions} executions)"

        # Nobody has used any of them — the common case, since a duplicate family
        # is unused by construction. Fall back to the name.
        base = standard.base_name(members[0].name)
        exact = [m for m in members if m.name == base]
        if exact:
            return exact[0], "already correctly named, none used"
        return min(members, key=lambda m: (len(m.name), m.name)), "none used, shortest name"

    # ----------------------------------------------------------------- apply

    async def _apply_family(self, family: FamilyPlan, archive: Path) -> None:
        """Archive, delete, then fix up the survivor. In that order.

        The order is load-bearing: archive BEFORE delete, and delete the losers
        BEFORE renaming the survivor onto the base name — otherwise the rename
        can collide with a member that has not been removed yet.
        """
        log.skills.debug(
            "[consolidate] apply_family: entry",
            extra={"_fields": {"base": family.base, "removing": len(family.removed)}},
        )
        for name in family.removed:
            row = await self._store.get(family.source, name)
            if row is None:
                # Already gone — nothing to archive and nothing to delete. Not an
                # error, but it must be visible: a plan that no longer matches
                # the catalog means something else wrote while we were planning.
                log.skills.warning(
                    "[consolidate] apply_family: member vanished between plan "
                    "and apply — skipping",
                    extra={"_fields": {"name": name, "source": family.source}},
                )
                continue

            src = Path(row.path)
            if src.exists():
                try:
                    shutil.copytree(src, archive / name, dirs_exist_ok=True)
                except Exception as exc:  # B5
                    # REFUSE TO DELETE WHAT WE COULD NOT ARCHIVE. The archive is
                    # the only thing making this operation recoverable; deleting
                    # anyway would trade the whole safety model for tidiness.
                    log.skills.error(
                        "[consolidate] apply_family: archive failed — NOT deleting",
                        exc_info=exc, extra={"_fields": {"name": name, "path": str(src)}},
                    )
                    continue
                shutil.rmtree(src, ignore_errors=True)

            await self._store.delete(row.skill_id)

        # The survivor inherits the family's usage, or the curator will archive
        # it for looking unused the moment consolidation finishes.
        await self._store.set_n_executions(family.survivor_id, family.merged_executions)

        if family.rename_needed:
            await self._rename_survivor(family)

        log.skills.info(
            "[consolidate] apply_family: exit — %s", family.describe(),
            extra={"_fields": {
                "base": family.base, "survivor": family.survivor,
                "removed": len(family.removed),
            }},
        )

    async def _rename_survivor(self, family: FamilyPlan) -> None:
        """Move the survivor onto the base name, on disk and in the index."""
        row = await self._store.get(family.source, family.survivor)
        if row is None:
            log.skills.error(
                "[consolidate] rename_survivor: survivor is gone — family left "
                "collapsed but still carrying its -N name",
                extra={"_fields": {"survivor": family.survivor, "base": family.base}},
            )
            return

        src = Path(row.path)
        dst = src.parent / family.base
        if src.exists() and not dst.exists():
            try:
                shutil.move(str(src), str(dst))
            except Exception as exc:  # B5
                log.skills.error(
                    "[consolidate] rename_survivor: move failed — index left "
                    "pointing at the old path, which is the recoverable half",
                    exc_info=exc,
                    extra={"_fields": {"src": str(src), "dst": str(dst)}},
                )
                return

        # A SKILL NAME LIVES IN THREE PLACES: the index row, the directory, and
        # the frontmatter. Rewriting only the first two is what this method did
        # until the conformance line in the morning brief caught it — the next
        # boot re-scanned the directory, read the stale `-N` name out of the
        # frontmatter, and upserted a SECOND row pointing at the same directory.
        # Consolidation partially undid itself overnight, resurrecting exactly
        # the numbered names it had just removed.
        self._rewrite_frontmatter_name(dst, family.base)
        await self._store.rename(row.skill_id, family.base, str(dst))
        log.skills.info(
            "[consolidate] rename_survivor: exit",
            extra={"_fields": {"from": family.survivor, "to": family.base}},
        )

    def _rewrite_frontmatter_name(self, skill_dir: Path, name: str) -> None:
        """Point the SKILL.md's own ``name:`` at the new name.

        Fails SOFT and LOUD. A frontmatter we could not rewrite leaves the file
        disagreeing with the index, which the next boot will notice by
        re-creating the old row — so this must be reported, but it must not
        abort a consolidation that has already deleted the losing members.
        """
        md = skill_dir / "SKILL.md"
        if not md.exists():
            log.skills.warning(
                "[consolidate] rewrite_frontmatter_name: no SKILL.md",
                extra={"_fields": {"dir": str(skill_dir)}},
            )
            return
        try:
            text = md.read_text(encoding="utf-8")
            head, sep, rest = text.partition("\n---")
            if not sep or not text.startswith("---"):
                log.skills.warning(
                    "[consolidate] rewrite_frontmatter_name: no frontmatter block "
                    "— the next re-scan will restore the old name",
                    extra={"_fields": {"path": str(md)}},
                )
                return
            new_head, n = _FRONTMATTER_NAME_RE.subn(f"name: {name}", head, count=1)
            if not n:
                log.skills.warning(
                    "[consolidate] rewrite_frontmatter_name: no name: line found",
                    extra={"_fields": {"path": str(md)}},
                )
                return
            md.write_text(new_head + sep + rest, encoding="utf-8")
        except Exception as exc:  # B5
            log.skills.error(
                "[consolidate] rewrite_frontmatter_name: failed — the file and "
                "the index now disagree, and the next boot will re-create the "
                "old numbered row",
                exc_info=exc, extra={"_fields": {"path": str(md), "name": name}},
            )
            return
        log.skills.debug(
            "[consolidate] rewrite_frontmatter_name: exit",
            extra={"_fields": {"path": str(md), "name": name}},
        )
