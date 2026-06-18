"""DurableExecutor — drives a multi-step task to completion (Pass 3b).

The executor orchestrates DURABILITY over an abstract sequence of steps. It is
deliberately DECOUPLED from the LLM / pipeline: a :class:`TaskStep` is any
awaitable unit of work, so the executor is unit-testable with plain async
functions (via :class:`CallableStep`) and later epics drive it with real owl
turns / tool dispatch.

Two durability journeys it guarantees:

* **J1 RESUME** — after an interruption (crash / process exit) the executor
  continues from the last *committed* step, never restarting from scratch.
  Per-step progress is checkpointed via ``store.update_status(current_step=…)``.
* **J2 EXACTLY-ONCE SIDE-EFFECTS** — a side-effecting step is wrapped in the
  :class:`~stackowl.pipeline.durable.ledger.SideEffectLedger` intent->commit
  contract: a committed effect is *replayed from the ledger* on resume rather
  than re-executed, and an ``intent`` without a commit (a crash mid-side-effect)
  PARKS the task for human surfacing instead of blindly re-running a possibly
  half-done effect.

Pure / read steps (``side_effecting=False``) skip the ledger entirely and just
run on every pass — they are assumed idempotent.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


class TaskStep(ABC):
    """One abstract unit of durable work.

    A step has a STABLE :attr:`name` (used verbatim as the ledger ``tool_name``,
    so it must be deterministic across replays), a :attr:`side_effecting` flag
    deciding whether the ledger guards it, and an awaitable :meth:`run`.
    """

    #: Stable identifier — reused as the ledger ``tool_name``. Must be
    #: deterministic across process restarts so the idempotency key is stable.
    name: str
    #: True when running this step mutates the world (must be ledger-guarded for
    #: exactly-once). False for pure/read steps (run every pass, no ledger).
    side_effecting: bool

    @abstractmethod
    async def run(self, context: str) -> str:
        """Execute the step and return its result string.

        ``context`` is the running goal/aggregate carried by the executor; the
        return value is recorded (in the ledger for side-effecting steps) and
        folded into the task's final result.
        """
        raise NotImplementedError


class CallableStep(TaskStep):
    """Concrete :class:`TaskStep` wrapping an async function.

    Lets tests and callers pass ``CallableStep("send_email", fn,
    side_effecting=True)`` without subclassing.
    """

    def __init__(
        self,
        name: str,
        fn: Callable[[str], Awaitable[str]],
        *,
        side_effecting: bool = False,
        action_severity: str | None = None,
    ) -> None:
        """Wrap ``fn`` as a step.

        ``side_effecting`` may be given directly, or derived from
        ``action_severity`` (the ``ToolManifest`` taxonomy) via
        :func:`~stackowl.pipeline.durable.ledger.is_side_effecting` when
        ``action_severity`` is supplied — the severity wins if both are given.
        """
        from stackowl.pipeline.durable.ledger import is_side_effecting

        if not name:
            raise ValueError("CallableStep.name must be non-empty")
        self.name = name
        self._fn = fn
        self.side_effecting = (
            is_side_effecting(action_severity)
            if action_severity is not None
            else side_effecting
        )

    async def run(self, context: str) -> str:
        return await self._fn(context)


class DurableExecutor:
    """Runs a :class:`DurableTask` to completion with checkpointing + ledger.

    Owner-scoped: it constructs its store and ledger bound to ``owner_id`` so
    every read/write/effect belongs to exactly one principal.
    """

    #: Separator joining each step's result into the task's aggregate result.
    _RESULT_SEP = "\n"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        self._db = db
        self._owner_id = owner_id
        self._store = DurableTaskStore(db, owner_id=owner_id)
        self._ledger = SideEffectLedger(db, owner_id=owner_id)

    async def start(
        self,
        goal: str,
        steps: Sequence[TaskStep],
        task_id: str | None = None,
    ) -> DurableTask:
        """Create a fresh task (status=running) and drive it to completion.

        A ``task_id`` is generated (UUID4) when not supplied. Drives steps from
        ``current_step=0`` with the durability contract in :meth:`_drive`.
        """
        # 1. ENTRY
        resolved_id = task_id or str(uuid.uuid4())
        log.tasks.debug(
            "[tasks] executor.start: entry",
            extra={"_fields": {
                "task_id": resolved_id, "owner_id": self._owner_id,
                "n_steps": len(steps),
            }},
        )
        now = datetime.now(tz=UTC)
        task = DurableTask(
            task_id=resolved_id,
            owner_id=self._owner_id,
            goal=goal,
            status="running",
            current_step=0,
            created_at=now,
            updated_at=now,
        )
        await self._store.create(task)
        # 2. DECISION — drive from the very first step
        log.tasks.debug(
            "[tasks] executor.start: created, driving steps",
            extra={"_fields": {"task_id": resolved_id, "from_step": 0}},
        )
        return await self._drive(task, steps)

    async def resume(self, task_id: str, steps: Sequence[TaskStep]) -> DurableTask:
        """Continue an interrupted task from its last checkpointed step.

        Loads the task (owner-scoped). Terminal tasks (completed/failed) are
        returned untouched. Otherwise drives the remaining steps with the SAME
        ledger semantics, so committed side-effects are skipped (exactly-once).
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] executor.resume: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        task = await self._store.get(task_id)
        # 2. DECISION — terminal tasks are not re-driven
        if task.status in ("completed", "failed"):
            log.tasks.info(
                "[tasks] executor.resume: task already terminal — no-op",
                extra={"_fields": {"task_id": task_id, "status": task.status}},
            )
            return task
        # A resumed task re-enters the running state from its checkpoint.
        if task.status != "running":
            await self._store.update_status(task_id, "running")
            task = task.model_copy(update={"status": "running"})
        log.tasks.debug(
            "[tasks] executor.resume: driving from checkpoint",
            extra={"_fields": {
                "task_id": task_id, "from_step": task.current_step,
            }},
        )
        return await self._drive(task, steps)

    async def recover(self) -> int:
        """Resume every task left ``running`` by a crash; return the count.

        A ``running`` row whose process died is orphaned mid-flight. This reaps
        them by re-driving each from its checkpoint. Recovery without the step
        sequence cannot re-run abstract steps, so callers that own the step
        definitions pass them via :meth:`resume`; this convenience method exists
        for the startup wiring where no in-memory steps are available — it can
        only re-evaluate already-committed/terminal progress, so it PARKS any
        task that still has un-run work (surfacing, never silently dropping).

        **Known limitations (tracked for future epics):**

        * Tasks are listed for this executor's ``owner_id`` only
          (``DEFAULT_PRINCIPAL_ID`` at startup). Orphaned tasks owned by other
          principals are not reaped here; multi-owner recovery is tracked for
          Epic 9.
        * Parked tasks have no in-memory step definitions: callers that own those
          definitions must call :meth:`resume(task_id, steps)` to re-drive them.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] executor.recover: entry",
            extra={"_fields": {"owner_id": self._owner_id}},
        )
        running = await self._store.list(status="running")
        # 2. DECISION — each orphaned running task is surfaced as parked, since
        #    the startup path holds no in-memory step definitions to re-drive.
        recovered = 0
        for task in running:
            try:
                await self._store.update_status(
                    task.task_id,
                    "parked",
                    result="recovered: interrupted mid-flight, awaiting re-drive",
                )
                recovered += 1
                log.tasks.info(
                    "[tasks] executor.recover: parked orphaned running task",
                    extra={"_fields": {"task_id": task.task_id}},
                )
            except Exception as exc:  # fail-loud per task, continue the sweep
                log.tasks.error(
                    "[tasks] executor.recover: failed to park task — continuing",
                    exc_info=exc,
                    extra={"_fields": {"task_id": task.task_id}},
                )
        # 4. EXIT
        log.tasks.info(
            "[tasks] executor.recover: exit",
            extra={"_fields": {"owner_id": self._owner_id, "recovered": recovered}},
        )
        return recovered

    async def _drive(
        self,
        task: DurableTask,
        steps: Sequence[TaskStep],
    ) -> DurableTask:
        """Run ``steps`` from ``task.current_step`` with checkpoint + ledger.

        Shared by :meth:`start` and :meth:`resume`. Returns the final task in a
        terminal (completed/failed) or ``parked`` state.
        """
        task_id = task.task_id
        # Seed the accumulator from any persisted partial result so that a
        # resumed pass includes outputs from steps run before the interruption.
        if task.current_step > 0 and task.result:
            results: list[str] = task.result.split(self._RESULT_SEP)
        else:
            results = []
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] executor._drive: entry",
            extra={"_fields": {
                "task_id": task_id, "from_step": task.current_step,
                "n_steps": len(steps),
                "seeded_results": len(results),
            }},
        )
        for index in range(task.current_step, len(steps)):
            step = steps[index]
            args = {"goal": task.goal, "step_index": index}
            try:
                if step.side_effecting:
                    parked = await self._run_side_effecting(
                        task_id, index, step, task.goal, args, results,
                    )
                    if parked is not None:
                        return parked
                else:
                    # 2. DECISION — pure/read step: run every pass, no ledger
                    log.tasks.debug(
                        "[tasks] executor._drive: pure step — run (no ledger)",
                        extra={"_fields": {
                            "task_id": task_id, "step_index": index,
                            "step": step.name,
                        }},
                    )
                    results.append(await step.run(task.goal))
            except Exception as exc:
                # A step raising is unrecoverable for this run — fail loud.
                reason = f"step {index} ({step.name}) failed: {exc}"
                log.tasks.error(
                    "[tasks] executor._drive: step raised — failing task",
                    exc_info=exc,
                    extra={"_fields": {
                        "task_id": task_id, "step_index": index, "step": step.name,
                    }},
                )
                await self._store.update_status(
                    task_id, "failed", current_step=index, result=reason,
                )
                return await self._store.get(task_id)
            # 3. STEP — checkpoint progress past this completed step, persisting
            #    the running aggregate so resume() can re-seed from it.
            next_step = index + 1
            running_aggregate = self._RESULT_SEP.join(results)
            await self._store.update_status(
                task_id, "running", current_step=next_step,
                result=running_aggregate,
            )
            log.tasks.debug(
                "[tasks] executor._drive: checkpointed",
                extra={"_fields": {"task_id": task_id, "current_step": next_step}},
            )
        # 4. EXIT — all steps done: complete with aggregate result
        aggregate = self._RESULT_SEP.join(results)
        await self._store.update_status(
            task_id, "completed", current_step=len(steps), result=aggregate,
        )
        log.tasks.info(
            "[tasks] executor._drive: task completed",
            extra={"_fields": {
                "task_id": task_id, "n_steps": len(steps),
                "result_len": len(aggregate),
            }},
        )
        return await self._store.get(task_id)

    async def _run_side_effecting(
        self,
        task_id: str,
        index: int,
        step: TaskStep,
        goal: str,
        args: dict[str, object],
        results: list[str],
    ) -> DurableTask | None:
        """Run a side-effecting step under the ledger contract.

        Returns ``None`` when the step proceeded/replayed normally (its result is
        appended to ``results``), or the PARKED task when the ledger reports an
        ``uncertain`` outcome (crash mid-side-effect) — the caller stops driving.
        """
        # 2. DECISION — consult the ledger before any effect
        decision = await self._ledger.begin(task_id, index, step.name, args)
        if decision.outcome == "already_committed":
            # Exactly-once: the effect already ran — replay its recorded result.
            log.tasks.info(
                "[tasks] executor: side-effect already committed — replay result",
                extra={"_fields": {
                    "task_id": task_id, "step_index": index, "step": step.name,
                }},
            )
            results.append(decision.result or "")
            return None
        if decision.outcome == "uncertain":
            # An intent without a commit: a prior attempt may have died mid-effect.
            # Do NOT re-run a possibly half-done side-effect — PARK for surfacing.
            log.tasks.warning(
                "[tasks] executor: uncertain side-effect — parking task",
                extra={"_fields": {
                    "task_id": task_id, "step_index": index, "step": step.name,
                }},
            )
            await self._store.update_status(
                task_id,
                "parked",
                current_step=index,
                result=(
                    f"parked at step {index} ({step.name}): uncertain side-effect "
                    "(intent without commit) — needs human review"
                ),
            )
            return await self._store.get(task_id)
        # proceed — run the effect exactly once then commit its result
        log.tasks.debug(
            "[tasks] executor: side-effect proceed — running then committing",
            extra={"_fields": {
                "task_id": task_id, "step_index": index, "step": step.name,
            }},
        )
        result = await step.run(goal)
        await self._ledger.commit(task_id, index, step.name, args, result)
        results.append(result)
        return None
