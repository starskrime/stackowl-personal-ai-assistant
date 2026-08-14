"""DreamWorker — a registered, unscheduled seat, kept deliberately empty.

WHAT THIS USED TO BE. A nightly consolidation pass over the fact store:
contradiction scan → promotion → pruning → kuzu_sync, plus a re-embed phase that
cured embedding-model drift, all with checkpoint-resume semantics across a
``dreamworker_runs`` table.

WHY IT IS EMPTY NOW. Every one of those five phases was fact work, and the fact
store is gone — ``committed_facts`` has held zero rows since D08.1's migration
0112 and has no writers left. The phases were not merely idle; they operated on
a table that cannot be populated. Checked rather than assumed, because
``_phase_kuzu_sync`` looked like a counterexample: it dispatches a ``kuzu_sync``
job rather than touching facts directly, and D08.1 deliberately KEPT the Kuzu
adapter because ``owls/evolution.py`` and ``pipeline/steps/classify.py`` query
the graph. But ``kuzu_sync_handler`` joins ON ``committed_facts``, so it syncs
FACTS into the graph and has had nothing to sync since 0112. There is also no
independent ``kuzu_sync`` job row — this handler was its only trigger, and this
handler is ``enabled = 0``.

WHY THE CLASS SURVIVES ANYWAY. D08.1 UNSCHEDULED this handler rather than
deleting it (migration 0113), specifically so **N01 Dreaming** — Bakir's own
idea, outside the reference map — would have somewhere to land. The seat is the
point. Deleting it to tidy up would remove the foundation of a feature that was
kept on purpose, and the plan for this removal said to delete it until the
record was re-read.

WHAT AN IMPLEMENTER OF N01 INHERITS. A handler that is registered under the name
``dream_worker``, defers under load, and currently reports honestly that it has
nothing to do. Give it phases; the scheduler wiring, the job row and the name
are already in place. Nothing here assumes memory at all any more — the
constructor takes no fact-store dependencies, so a dreaming pass is free to
define its own.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

from stackowl.infra.observability import log
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.scheduler.job import Job


class DreamWorkerJobHandler(JobHandler):
    """The seat for N01 Dreaming. Registered, unscheduled, and currently empty.

    See the module docstring for why this is a seat rather than a deletion.
    """

    _handler_name: ClassVar[str] = "dream_worker"

    def __init__(self) -> None:
        # 1. ENTRY / 4. EXIT — nothing to build until N01 gives it phases.
        log.memory.debug("[memory] dream_worker.init: seat constructed, no phases")

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def defer_under_load(self) -> bool:
        # Kept True: whatever N01 puts here will be a background pass, and a seat
        # that yields to live turns is the safe default to inherit.
        return True

    async def execute(self, job: Job) -> JobResult:
        """Report honestly that there is nothing to run.

        NOT a silent success. An empty pass that logged nothing would look
        identical to a working one, and this programme has been bitten enough
        times by exactly that — a call that happens and an effect that does not.
        ``effect_class="read_only"`` is the truthful classification: this touches
        nothing.
        """
        # 1. ENTRY
        t0 = time.monotonic()
        log.memory.info(
            "[memory] dream_worker.execute: entry — seat has no phases",
            extra={"_fields": {"job_id": job.job_id}},
        )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.memory.info(
            "[memory] dream_worker.execute: exit — nothing to do",
            extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="read_only",
            success=True,
            output="dream_worker: no phases configured (seat reserved for N01)",
            error=None,
            duration_ms=duration_ms,
            metadata={"phases_run": 0},
        )
