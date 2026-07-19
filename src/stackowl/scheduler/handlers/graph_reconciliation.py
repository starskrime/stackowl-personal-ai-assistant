"""GraphReconciliationHandler — weekly diff-and-backfill-and-prune between
SQLite (authoritative: skill_ownership, owl_dna) and the Kuzu graph (derived
index: Owl/Skill/Trait nodes, OWNS/HAS_TRAIT edges).

Backstops the best-effort inline sync in synthesizer.py/evolution.py — an
extended Kuzu outage (or a bug) can only ever leave the graph stale until the
next weekly sweep closes the gap. Never fails the tick on one bad row (mirrors
retry_sweep/objective_driver's per-item isolation); a no-graph-wired box is a
clean, honest no-op.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool
    from stackowl.memory.kuzu_adapter import KuzuAdapter

_SELECT_SKILL_OWNERSHIP = "SELECT owner_id, owl_name, skill_name FROM skill_ownership"
_SELECT_OWL_DNA = "SELECT owl_name, " + ", ".join(TRAIT_NAMES) + " FROM owl_dna"


class GraphReconciliationHandler(JobHandler):
    """Diff SQLite against the graph; backfill what's missing, prune what's stale."""

    def __init__(self, db: DbPool, kuzu: KuzuAdapter | None) -> None:
        self._db = db
        self._kuzu = kuzu

    @property
    def handler_name(self) -> str:
        return "graph_reconciliation"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] graph_reconciliation.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "has_kuzu": self._kuzu is not None}},
        )
        if self._kuzu is None:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.info(
                "[scheduler] graph_reconciliation.execute: exit",
                extra={"_fields": {
                    "job_id": job.job_id,
                    "duration_ms": duration_ms,
                    "reason": "no graph wired",
                }},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output="graph_reconciliation: noop (no graph wired)", error=None,
                duration_ms=duration_ms,
            )

        backfilled_skills = await self._reconcile_skills()
        backfilled_traits = await self._reconcile_traits()

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] graph_reconciliation.execute: exit",
            extra={"_fields": {
                "backfilled_skills": backfilled_skills,
                "backfilled_traits": backfilled_traits,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"skills={backfilled_skills} traits={backfilled_traits}",
            error=None, duration_ms=duration_ms,
            metadata={"backfilled_skills": backfilled_skills, "backfilled_traits": backfilled_traits},
        )

    async def _reconcile_skills(self) -> int:
        assert self._kuzu is not None
        rows = await self._db.fetch_all(_SELECT_SKILL_OWNERSHIP)
        want: dict[str, tuple[str, str, str]] = {}
        for row in rows:
            owner_id = str(row["owner_id"])
            owl_name = str(row["owl_name"])
            skill_name = str(row["skill_name"])
            skill_id = f"{owner_id}::{skill_name}"
            want[skill_id] = (owner_id, owl_name, skill_name)

        have = set(await self._kuzu.list_skill_ids())
        touched = 0
        for skill_id, (owner_id, owl_name, skill_name) in want.items():
            if skill_id in have:
                continue
            try:
                await self._kuzu.upsert_owl_node(owl_name)
                await self._kuzu.upsert_skill_node(skill_id, owner_id, skill_name)
                await self._kuzu.link_owl_owns_skill(owl_name, skill_id)
                touched += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_skills: row failed",
                    exc_info=exc,
                    extra={"_fields": {"skill_id": skill_id}},
                )

        for stale_id in have - want.keys():
            try:
                await self._kuzu.delete_skill_node(stale_id)
            except Exception as exc:  # noqa: BLE001
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_skills: prune failed",
                    exc_info=exc,
                    extra={"_fields": {"skill_id": stale_id}},
                )
        return touched

    async def _reconcile_traits(self) -> int:
        assert self._kuzu is not None
        rows = await self._db.fetch_all(_SELECT_OWL_DNA)
        want: dict[str, tuple[str, str, float]] = {}
        for row in rows:
            owl_name = str(row["owl_name"])
            for trait_name in TRAIT_NAMES:
                trait_id = f"{owl_name}::{trait_name}"
                want[trait_id] = (owl_name, trait_name, float(row[trait_name]))

        have = set(await self._kuzu.list_trait_ids())
        touched = 0
        for trait_id, (owl_name, trait_name, value) in want.items():
            if trait_id in have:
                continue
            try:
                await self._kuzu.upsert_owl_node(owl_name)
                await self._kuzu.upsert_trait_node(trait_id, owl_name, trait_name, value)
                await self._kuzu.link_owl_has_trait(owl_name, trait_id)
                touched += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_traits: row failed",
                    exc_info=exc,
                    extra={"_fields": {"trait_id": trait_id}},
                )

        for stale_id in have - want.keys():
            try:
                await self._kuzu.delete_trait_node(stale_id)
            except Exception as exc:  # noqa: BLE001
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_traits: prune failed",
                    exc_info=exc,
                    extra={"_fields": {"trait_id": stale_id}},
                )
        return touched
