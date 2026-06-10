"""B4 crash-recovery — resume orphaned durable tasks at startup.

When a process dies mid-drive it leaves :class:`DurableTask` rows stuck
``running`` (or ``recovering`` — see below): the goal never reached a terminal
status, but no live coroutine is driving it. :func:`recover_durable_tasks` reaps
those orphans on the next startup and DRIVES EACH TO COMPLETION (not merely parks
them), reusing the same B1/B2/B3 durable seams a fresh goal uses:

* The ReAct checkpoint (``tasks.checkpoint_blob``) restores the transcript so the
  provider continues from the last completed iteration instead of restarting.
* The side-effect ledger short-circuits any already-committed effect (replay,
  never re-run) — exactly-once survives the crash.
* :class:`~stackowl.pipeline.durable.task_runner.DurableTaskRunner` finalizes the
  task through its idempotent terminal-status guard.

Which rows are orphans — ``running`` AND ``recovering``
-------------------------------------------------------
At STARTUP the prior process is DEAD, so there are no concurrent live drives. A
task left ``running`` is the obvious orphan, but a task left ``recovering`` is
ALSO an orphan: it was claimed (``running -> recovering``) by a process that was
then killed BEFORE it could resume. The sweep therefore reaps BOTH statuses —
without that, a ``recovering`` row would be stuck forever (it is never ``running``
again, so a running-only sweep would never see it).

Concurrency control — atomic CLAIM (CAS)
----------------------------------------
Before touching a task, recovery atomically latches it
(``status -> recovering`` via :meth:`DurableTaskStore.claim_for_recovery`, a
single owner-scoped CAS over ``status IN ('running','recovering')``). Exactly one
worker can win that transition; a loser sees rows-affected=0 and SKIPS it. This
is the real concurrency control — the runner's terminal-status guard is only the
backstop.

Background drive — startup is never blocked by serial ReAct drives
------------------------------------------------------------------
Recovery is split in two: a FAST claim+reconstruct pass that IS awaited (DB-only)
so the on-disk orphans are latched before the gateway proceeds, then each task's
actual ``runner.resume()`` drive is LAUNCHED as a BACKGROUND task. N orphans
therefore do NOT block the gateway for N x a full ReAct drive — the drives finish
behind a live gateway. Strong references to the background drives are held by the
recoverer (a done-callback discards each on finish) so none is GC'd mid-flight;
each drive is independently fail-open.

Fail-open per task
------------------
Each task's claim/reconstruct is wrapped so one bad task (an undecodable
checkpoint, a backend error, …) is LOGGED and the sweep continues to the next;
each background drive is likewise fail-open. Recovery must never crash startup;
the wiring (in ``startup/orchestrator.py``) additionally fails open around the
whole call.

Reconstruction
--------------
The recovered :class:`PipelineState` carries the PERSISTED ``owl_name``/``channel``
the task was created with (threaded from the originating PipelineState in
:meth:`DurableTaskRunner.run`), falling back to the documented goal-handler
defaults (``owl_name="secretary"``, ``channel="cli"``) ONLY for legacy rows
(pre-migration 0047) where those columns are NULL. ``interactive=False`` and the
task's own ``goal`` as ``input_text`` round out the state. The durable scope
(``task_id`` / ``durable_owner_id``) and, when a checkpoint exists, the
``durable_resume_*`` trio are stamped so the B2 execute step picks up the drive
mid-transcript.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.durable.react_checkpoint import deserialize
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.backends.base import OrchestratorBackend

#: Reconstruction fallbacks for LEGACY rows only — a task created before
#: migration 0047 has NULL ``owl_name``/``channel`` (the context was never
#: persisted). These mirror the goal handler's PipelineState build
#: (goal_execution.py) so a legacy recovered run is equivalent to the original.
#: A task created at or after 0047 carries its real originating owl/channel
#: (threaded from the creating PipelineState in DurableTaskRunner.run), so these
#: are used ONLY when the persisted value is NULL.
_DEFAULT_OWL = "secretary"
_DEFAULT_CHANNEL = "cli"


class DurableTaskRecoverer:
    """Resumes orphaned durable tasks for one owner — background drive.

    Owner-scoped: constructs its :class:`DurableTaskStore` bound to ``owner_id``
    so every claim/resume belongs to exactly one principal. Drives each recovered
    task through ``backend`` via the shared :class:`DurableTaskRunner` lifecycle.

    Recovery is split into two passes so startup is never blocked by serial ReAct
    drives (each resume can run a full multi-iteration ReAct loop):

    * :meth:`recover` — a FAST claim+reconstruct pass that IS awaited (DB-only:
      list orphans, atomically CLAIM each, reconstruct its :class:`PipelineState`)
      and then LAUNCHES each task's actual ``runner.resume()`` drive as a
      BACKGROUND task. It returns the count LAUNCHED, so the gateway becomes
      available immediately rather than waiting N x a full drive.
    * Each background drive is FAIL-OPEN (its own try/except logs) and a STRONG
      reference is held in :attr:`_drives` (a done-callback logs completion /
      exception and discards the ref) so the task is never GC'd mid-flight.

    :meth:`drain` awaits all in-flight background drives — used by tests and by a
    clean shutdown that wants every recovery to finish before the pool closes.
    """

    def __init__(
        self,
        db: DbPool,
        backend: OrchestratorBackend,
        *,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> None:
        self._db = db
        self._backend = backend
        self._owner_id = owner_id
        self._store = DurableTaskStore(db, owner_id=owner_id)
        #: STRONG references to in-flight background resume drives. Without this
        #: set, asyncio only holds a weak ref and a drive could be GC'd mid-flight
        #: (fire-and-forget bug). The done-callback discards each ref on finish.
        self._drives: set[asyncio.Task[None]] = set()
        #: How many drives the last :meth:`recover` LAUNCHED (for caller logging).
        self._launched = 0

    @property
    def launched(self) -> int:
        """Number of background drives launched by the last :meth:`recover`."""
        return self._launched

    @property
    def in_flight(self) -> int:
        """Number of background recovery drives still running."""
        return len(self._drives)

    async def recover(self) -> int:
        """Claim+reconstruct every orphan, LAUNCH its drive in the background.

        At STARTUP the prior process is dead, so there are NO concurrent live
        drives: ANY task still ``running`` OR ``recovering`` is an orphan. A
        ``recovering`` row is necessarily stale — it was claimed (running ->
        recovering) by a process that was then killed before it could resume, so
        without reaping it here it would be stuck ``recovering`` forever. This
        sweep lists BOTH statuses, atomically CLAIMS each (the CAS accepts both),
        reconstructs its :class:`PipelineState`, and LAUNCHES the resume drive as
        a background task. Returns how many drives were LAUNCHED by THIS sweep
        (the gateway proceeds immediately; the drives finish behind it).
        """
        # 1. ENTRY
        log.tasks.info(
            "[tasks] recovery.recover: entry — scanning for orphaned tasks",
            extra={"_fields": {"owner_id": self._owner_id}},
        )
        # Both 'running' and 'recovering' are orphans at startup (no live drive
        # exists to own a 'recovering' latch — the prior process died). De-dupe by
        # task_id in case a status flips mid-scan.
        running = await self._store.list(status="running")
        recovering = await self._store.list(status="recovering")
        seen: set[str] = set()
        orphans: list[DurableTask] = []
        for task in (*running, *recovering):
            # D1 §9 — roots only. Children are resumed transitively when the
            # parent re-executes its delegate_task and re-derives the same child
            # id; listing them here would double-drive them as detached
            # top-level goals with no one to return to.
            if task.parent_task_id is not None:
                continue
            if task.task_id not in seen:
                seen.add(task.task_id)
                orphans.append(task)
        # D1 §7.3 — reap zombie children whose parent is already terminal (they
        # are unreachable by transitive resolution). Fail-open (logged).
        await self._reap_zombie_children()
        # 2. DECISION — FAST pass: claim + reconstruct each orphan (DB-only,
        #    awaited), then LAUNCH its drive in the background. Fail-open per task.
        launched = 0
        for task in orphans:
            try:
                claimed = await self._claim_and_reconstruct(task)
            except Exception as exc:  # noqa: BLE001 — fail-open per task (logged)
                # A bad task (undecodable checkpoint, backend error during claim)
                # must NOT block recovery of the others or crash startup.
                log.tasks.error(
                    "[tasks] recovery.recover: claim/reconstruct failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {
                        "task_id": task.task_id, "owner_id": self._owner_id,
                    }},
                )
                continue
            if claimed is None:
                continue  # CAS lost (another worker / already terminal) — skip.
            self._launch_drive(*claimed)
            launched += 1
        self._launched = launched
        # 4. EXIT
        log.tasks.info(
            "[tasks] recovery.recover: exit — launched background drives",
            extra={"_fields": {
                "owner_id": self._owner_id,
                "orphans_seen": len(orphans),
                "running_seen": len(running),
                "recovering_seen": len(recovering),
                "launched": launched,
            }},
        )
        return launched

    async def drain(self) -> None:
        """Await every in-flight background recovery drive (tests / clean shutdown).

        Fail-open: drives are individually fail-open (each logs its own error and
        never re-raises), so gathering them cannot raise. A no-op when no drive is
        in flight.
        """
        if not self._drives:
            return
        log.tasks.info(
            "[tasks] recovery.drain: awaiting background drives",
            extra={"_fields": {"owner_id": self._owner_id, "in_flight": len(self._drives)}},
        )
        await asyncio.gather(*tuple(self._drives), return_exceptions=True)

    async def _reap_zombie_children(self) -> None:
        """Mark running/recovering children of terminal parents 'failed' (D1 §7.3).

        With parent-driven terminalization this is normally empty; it is the
        belt-and-suspenders for crash interleavings. Fail-open: a store error is
        logged and never crashes recovery.
        """
        try:
            zombies = await self._store.list_zombie_children()
        except Exception as exc:  # noqa: BLE001 — fail-open, logged
            log.tasks.error(
                "[tasks] recovery: zombie-child sweep query failed — skipping",
                exc_info=exc,
                extra={"_fields": {"owner_id": self._owner_id}},
            )
            return
        for z in zombies:
            try:
                await self._store.terminalize_child(
                    z.task_id, "failed",
                    result="abandoned: parent already terminal",
                )
                log.tasks.warning(
                    "[tasks] recovery: reaped zombie child under terminal parent",
                    extra={"_fields": {
                        "task_id": z.task_id, "parent_task_id": z.parent_task_id,
                    }},
                )
            except Exception as exc:  # noqa: BLE001 — per-zombie fail-open
                log.tasks.error(
                    "[tasks] recovery: reaping a zombie child failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {"task_id": z.task_id}},
                )

    async def _claim_and_reconstruct(
        self, task: DurableTask
    ) -> tuple[str, PipelineState] | None:
        """Claim one orphan and reconstruct its state — the awaited DB-only pass.

        Returns ``(task_id, state)`` iff THIS call won the CAS claim (caller then
        launches the drive); ``None`` if the claim was lost (another worker, or
        the row is no longer claimable — skip, no error).
        """
        task_id = task.task_id
        # 2. DECISION — atomic CAS claim: only the winner proceeds.
        claimed = await self._store.claim_for_recovery(task_id)
        if not claimed:
            log.tasks.info(
                "[tasks] recovery: task already claimed by another worker — skipping",
                extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
            )
            return None
        log.tasks.info(
            "[tasks] recovery: claimed orphaned task — reconstructing state",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        # 3. STEP — reconstruct an equivalent PipelineState (from checkpoint if any).
        state = await self._reconstruct_state(task)
        # The runner's terminal-status guard only transitions AWAY from 'running',
        # so return the claimed ('recovering') task to 'running' BEFORE the drive.
        await self._return_to_running(task_id)
        return task_id, state

    def _launch_drive(self, task_id: str, state: PipelineState) -> None:
        """Launch one orphan's resume drive as a referenced background task.

        Keeps a STRONG reference in :attr:`_drives` (asyncio holds only a weak
        ref) so the drive can't be GC'd mid-flight; the done-callback discards the
        ref and logs completion/exception. The drive itself is fail-open.
        """
        drive = asyncio.create_task(
            self._drive_one(task_id, state), name=f"durable-recover-{task_id[:12]}"
        )
        self._drives.add(drive)
        drive.add_done_callback(self._on_drive_done)
        log.tasks.info(
            "[tasks] recovery: launched background resume drive",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )

    async def _drive_one(self, task_id: str, state: PipelineState) -> None:
        """Resume one claimed task to a terminal outcome — FAIL-OPEN background body.

        Wrapped so a single failed drive (backend error, bad checkpoint) is LOGGED
        and never propagates out of the background task (which would otherwise
        surface only as an unretrieved-exception warning). The runner finalizes
        the task through its idempotent terminal-status guard.
        """
        try:
            runner = DurableTaskRunner(self._store, self._backend)
            final_state, _ = await runner.resume(task_id=task_id, state=state)
            log.tasks.info(
                "[tasks] recovery: background drive resumed task to terminal outcome",
                extra={"_fields": {
                    "task_id": task_id, "parked": final_state.durable_parked,
                    "errors": len(final_state.errors),
                }},
            )
        except Exception as exc:  # noqa: BLE001 — fail-open background drive (logged)
            log.tasks.error(
                "[tasks] recovery: background drive failed — task left for next sweep",
                exc_info=exc,
                extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
            )

    def _on_drive_done(self, drive: asyncio.Task[None]) -> None:
        """Done-callback: discard the strong ref and log completion/exception.

        The body of :meth:`_drive_one` is fail-open, so an exception here is only
        a cancellation (clean shutdown) or a truly unexpected escape — logged, not
        raised (a done-callback must never raise).
        """
        self._drives.discard(drive)
        if drive.cancelled():
            log.tasks.info(
                "[tasks] recovery: background drive cancelled",
                extra={"_fields": {"task": drive.get_name(), "owner_id": self._owner_id}},
            )
            return
        exc = drive.exception()
        if exc is not None:
            log.tasks.error(
                "[tasks] recovery: background drive raised past its fail-open guard",
                exc_info=exc,
                extra={"_fields": {"task": drive.get_name(), "owner_id": self._owner_id}},
            )

    async def _return_to_running(self, task_id: str) -> None:
        """Move a claimed (``recovering``) task back to ``running`` before resume.

        The runner's terminal-status guard only transitions AWAY from ``running``,
        so the claimed task must be ``running`` again before :meth:`resume` drives
        and finalizes it. This is an owner-scoped status write (no checkpoint
        change).
        """
        await self._store.update_status(task_id, "running")

    async def _reconstruct_state(self, task: DurableTask) -> PipelineState:
        """Rebuild a runnable :class:`PipelineState` for an orphaned task.

        Loads the checkpoint blob: if present, deserialize it and stamp the
        ``durable_resume_*`` trio so the provider continues from
        ``iteration + 1``; if absent (crashed before iteration 0 completed),
        return a fresh durable state that starts at iteration 0. Either way the
        owl/channel context is taken from the PERSISTED ``owl_name``/``channel``
        on the task (threaded from the originating PipelineState at creation),
        falling back to the documented goal-handler defaults only for legacy
        rows where those columns are NULL. ``input_text`` is the goal.
        """
        task_id = task.task_id
        # D1 §9 depth-from-tree — only ROOTS are reconstructed here (recover()
        # filters parent_task_id IS NULL), so depth starts at 0 correctly. Interior
        # nodes are NEVER directly resumed: the parent re-delegates on resume and
        # the child's depth is re-derived from delegation_chain growth, never from
        # a stale ContextVar.
        owl_name = task.owl_name or _DEFAULT_OWL
        channel = task.channel or _DEFAULT_CHANNEL
        if task.owl_name is None or task.channel is None:
            # Legacy row (pre-0047) — context was never persisted; use defaults.
            log.tasks.info(
                "[tasks] recovery: legacy task missing owl/channel — using defaults",
                extra={"_fields": {
                    "task_id": task_id,
                    "owl_name": owl_name,
                    "channel": channel,
                    "had_owl": task.owl_name is not None,
                    "had_channel": task.channel is not None,
                }},
            )
        base = PipelineState(
            trace_id=f"recover-{task_id[:12]}",
            session_id=f"recover-{task_id[:12]}",
            input_text=task.goal,
            channel=channel,
            owl_name=owl_name,
            pipeline_step="",
            interactive=False,
        )
        blob = await self._store.load_checkpoint(task_id)
        if blob is None:
            # No checkpoint — crashed before iteration 0 completed. Resume fresh:
            # task_id set (durable drive), no resume_* (provider starts at iter 0).
            log.tasks.info(
                "[tasks] recovery: no checkpoint — resuming from a fresh state",
                extra={"_fields": {"task_id": task_id}},
            )
            return base.evolve(
                task_id=task_id,
                durable_owner_id=self._owner_id,
                creation_ceiling=task.creation_ceiling,
                task_envelope=task.task_envelope,
            )
        # A checkpoint exists — continue the transcript from the next iteration.
        cp = deserialize(blob)
        log.tasks.info(
            "[tasks] recovery: checkpoint loaded — resuming mid-transcript",
            extra={"_fields": {
                "task_id": task_id,
                "from_iteration": cp.iteration + 1,
                "messages": len(cp.messages),
                "tool_calls": len(cp.tool_call_records),
            }},
        )
        return base.evolve(
            task_id=task_id,
            durable_owner_id=self._owner_id,
            creation_ceiling=task.creation_ceiling,
            task_envelope=task.task_envelope,
            durable_resume_messages=cp.messages,
            durable_resume_tool_calls=cp.tool_call_records,
            durable_resume_iteration=cp.iteration + 1,
        )


async def recover_durable_tasks(
    db: DbPool,
    backend: OrchestratorBackend,
    *,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> DurableTaskRecoverer:
    """Claim+reconstruct every orphan and LAUNCH its drive in the background.

    The B4 startup entry-point. Builds a :class:`DurableTaskRecoverer`, runs its
    fast (awaited, DB-only) claim+reconstruct pass over BOTH ``running`` and
    ``recovering`` orphans, and LAUNCHES each resume drive as a referenced
    background task. Returns the RECOVERER so the caller can (a) hold a strong
    reference — the recoverer owns the strong refs to the in-flight drives, so it
    MUST outlive them or they would be GC'd — read ``recoverer`` for the launched
    count via the returned object, and (b) :meth:`~DurableTaskRecoverer.drain`
    them on a clean shutdown. The gateway proceeds immediately; the drives finish
    behind it. Fails open PER TASK (a bad task is logged and skipped) and each
    background drive is itself fail-open, so recovery never blocks/crashes
    startup.

    The launched count is logged by :meth:`DurableTaskRecoverer.recover`; the
    caller logs the startup "launched N durable-task recoveries" line from it.
    """
    recoverer = DurableTaskRecoverer(db, backend, owner_id=owner_id)
    launched = await recoverer.recover()
    log.tasks.info(
        "[tasks] recovery: launched durable-task recoveries in background",
        extra={"_fields": {"owner_id": owner_id, "launched": launched}},
    )
    return recoverer
