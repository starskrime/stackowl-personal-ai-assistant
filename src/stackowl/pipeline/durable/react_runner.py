"""DurableReActRunner — drive a ReAct task under durability + resume (S5).

This is the executor seam that ties the already-built durable primitives
together into a runnable unit:

* it creates/loads a :class:`~stackowl.pipeline.durable.task.DurableTask`;
* it activates a :class:`~stackowl.pipeline.durable.context.DurableReActContext`
  for the duration of one *drive* so the ledger guard
  (:mod:`stackowl.pipeline.durable.ledger_guard`) routes every side-effecting
  tool call through the :class:`~stackowl.pipeline.durable.ledger.SideEffectLedger`
  for exactly-once execution;
* it wires the per-iteration checkpoint callback
  (:func:`~stackowl.pipeline.durable.checkpoint_callback.make_checkpoint_callback`)
  so each completed iteration persists a resume cursor and advances
  ``ctx.iteration``;
* on resume it restores the ``messages`` transcript from the last checkpoint
  and seeds ``ctx.iteration`` so committed side-effects are *skipped* on replay.

Decoupling from the real provider
----------------------------------
The runner never touches a provider directly.  It is given a
:data:`ReactDrive` — an injected coroutine that, given the starting
``messages`` and the per-iteration callback, drives the provider's
``complete_with_tools`` loop and returns ``(final_result_text, final_messages)``.
S6 supplies a real drive that calls
``provider.complete_with_tools(..., on_iteration_complete=callback)``; tests
supply a fake drive.  This keeps the durability/resume logic fully unit-testable
without a live LLM.

The exactly-once-on-resume proof (J1/J2)
----------------------------------------
On :meth:`resume`, the checkpoint blob's ``iteration`` (K) means iterations
0..K completed.  The runner restores ``messages`` from the blob *directly*
(never re-injecting a system prompt — for OpenAI the system turn is already
``messages[0]``; see the caveat in ``react_checkpoint.py``) and seeds
``ctx.iteration = K + 1``.  The :class:`SideEffectLedger` already holds the
committed side-effects of iterations 0..K.  When the resumed drive replays those
iterations' tool calls, the ledger guard returns ``already_committed`` and skips
re-execution — exactly-once.  The restored ``messages`` give the LLM the same
context so it continues from iteration K + 1 to the natural terminal.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableReplayUncertain
from stackowl.infra.observability import log
from stackowl.pipeline.durable.checkpoint_callback import make_checkpoint_callback
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.react_checkpoint import deserialize
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.providers.react_callback import IterationCallback
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

#: Drives a provider's ``complete_with_tools`` loop for one *drive*.
#:
#: Given the starting ``messages`` and the per-iteration ``callback`` (which the
#: provider must invoke once at the bottom of each completed ReAct iteration),
#: it runs the loop to its natural terminal and returns
#: ``(final_result_text, final_messages)``.  S6 supplies a real one bound to a
#: provider; tests supply a fake one.
ReactDrive = Callable[
    [list[dict[str, Any]], IterationCallback],
    Awaitable[tuple[str, list[dict[str, Any]]]],
]


class DurableReActRunner:
    """Drive a durable ReAct task to completion, with crash-resume.

    Owns an owner-scoped :class:`DurableTaskStore` and :class:`SideEffectLedger`
    for the bound principal.  The actual provider loop is injected as a
    :data:`ReactDrive` so this class is testable without an LLM (S6 wires the
    real provider).
    """

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        self._db = db
        self._owner_id = owner_id
        self._store = DurableTaskStore(db, owner_id)
        self._ledger = SideEffectLedger(db, owner_id)

    async def start(
        self,
        goal: str,
        drive: ReactDrive,
        task_id: str | None = None,
    ) -> DurableTask:
        """Create a fresh durable task and drive it to a terminal state.

        Creates a ``running`` :class:`DurableTask`, activates a durable context,
        and hands the injected ``drive`` the initial messages + checkpoint
        callback.  On success the task is marked ``completed`` with the final
        result; on an ``uncertain`` ledger replay it is ``parked``; on any other
        error it is ``failed`` and the error is re-raised (fail-loud).

        Args:
            goal: the user/owl goal that seeds ``messages[0]``.
            drive: the injected provider-loop driver (see :data:`ReactDrive`).
            task_id: optional explicit id; a UUID is minted when omitted.

        Returns:
            The :class:`DurableTask` in its terminal (or parked) state.
        """
        resolved_id = task_id or uuid.uuid4().hex
        # 1. ENTRY
        log.tasks.info(
            "[tasks] react_runner.start: entry",
            extra={"_fields": {
                "task_id": resolved_id, "owner_id": self._owner_id,
                "goal_len": len(goal),
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

        # 2. DECISION — fresh task: seed messages from the goal, iteration 0.
        messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
        ctx = DurableReActContext(
            task_id=resolved_id,
            owner_id=self._owner_id,
            ledger=self._ledger,
            iteration=0,
        )
        log.tasks.debug(
            "[tasks] react_runner.start: driving fresh task",
            extra={"_fields": {
                "task_id": resolved_id, "iteration": ctx.iteration,
                "msg_count": len(messages),
            }},
        )
        return await self._drive_and_finalize(resolved_id, messages, ctx, drive)

    async def resume(self, task_id: str, drive: ReactDrive) -> DurableTask:
        """Resume an interrupted durable task from its last checkpoint.

        Loads the task (owner-scoped; raises
        :class:`~stackowl.exceptions.DurableTaskNotFoundError` if absent).
        Already-terminal tasks (``completed`` / ``failed``) return unchanged
        (idempotent).  Otherwise the last checkpoint is loaded:

        * **blob present** — restore ``messages`` directly from the checkpoint
          (do NOT reconstruct/re-inject a system prompt) and seed
          ``ctx.iteration = cp.iteration + 1`` (the blob's ``iteration`` is
          authoritative over ``current_step``: iterations ``0..cp.iteration``
          completed, so the loop resumes at ``cp.iteration + 1``).  The ledger
          already holds those iterations' committed side-effects, so the replayed
          calls return ``already_committed`` — exactly-once.
        * **no blob** — the prior attempt crashed before the first iteration
          completed; start fresh from the goal with ``iteration = 0``.

        Same completion / uncertain / failure handling as :meth:`start`.
        """
        # 1. ENTRY
        log.tasks.info(
            "[tasks] react_runner.resume: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        task = await self._store.get(task_id)

        # 2. DECISION — already-terminal tasks are a no-op (idempotent resume).
        if task.status in ("completed", "failed"):
            log.tasks.info(
                "[tasks] react_runner.resume: task already terminal — no-op",
                extra={"_fields": {"task_id": task_id, "status": task.status}},
            )
            return task

        ctx = DurableReActContext(
            task_id=task_id,
            owner_id=self._owner_id,
            ledger=self._ledger,
            iteration=0,
        )
        blob = await self._store.load_checkpoint(task_id)
        if blob is not None:
            # Restore messages DIRECTLY — the system turn (if any) is already
            # messages[0]; re-injecting would duplicate it (react_checkpoint S5
            # caveat).  Seed iteration to cp.iteration + 1 (blob authoritative).
            cp = deserialize(blob)
            messages: list[dict[str, Any]] = list(cp.messages)
            ctx.iteration = cp.iteration + 1
            log.tasks.debug(
                "[tasks] react_runner.resume: restored from checkpoint",
                extra={"_fields": {
                    "task_id": task_id,
                    "checkpoint_iteration": cp.iteration,
                    "resume_iteration": ctx.iteration,
                    "msg_count": len(messages),
                }},
            )
        else:
            # Crashed before the first iteration completed: no resume cursor.
            messages = [{"role": "user", "content": task.goal}]
            ctx.iteration = 0
            log.tasks.debug(
                "[tasks] react_runner.resume: no checkpoint — restarting fresh",
                extra={"_fields": {"task_id": task_id, "iteration": ctx.iteration}},
            )

        # status -> running before re-driving.
        await self._store.update_status(task_id, "running")
        return await self._drive_and_finalize(task_id, messages, ctx, drive)

    async def _drive_and_finalize(
        self,
        task_id: str,
        messages: list[dict[str, Any]],
        ctx: DurableReActContext,
        drive: ReactDrive,
    ) -> DurableTask:
        """Run one drive under the active context and finalize the task.

        Shared tail of :meth:`start` and :meth:`resume`: activates ``ctx``, runs
        the injected ``drive`` with a fresh checkpoint callback, then maps the
        outcome onto a terminal task status.  No exception is swallowed:
        ``DurableReplayUncertain`` parks; anything else fails-loud and re-raises.
        """
        cb = make_checkpoint_callback(ctx, self._store)
        # 3. STEP — drive the provider loop under the active durable context.
        try:
            # ``activate`` is a sync context manager (contextvars set/reset);
            # the awaited drive runs inside it, so the active context is visible
            # to the ledger guard for the whole drive.
            with activate(ctx):
                result, _final_messages = await drive(messages, cb)
        except DurableReplayUncertain as exc:
            # An intent-without-commit on replay: never blindly re-run a possibly
            # half-done side effect — park for human review (design §2.2/§2.3).
            log.tasks.warning(
                "[tasks] react_runner: uncertain ledger replay — parking task",
                extra={"_fields": {
                    "task_id": task_id, "owner_id": self._owner_id,
                    "step_index": exc.step_index, "tool_name": exc.tool_name,
                }},
            )
            await self._store.update_status(
                task_id, "parked", result=str(exc),
            )
            return await self._store.get(task_id)
        except Exception as exc:
            # Fail-loud: record the failure reason and re-raise.
            log.tasks.error(
                "[tasks] react_runner: drive failed — marking task failed",
                exc_info=exc,
                extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
            )
            await self._store.update_status(task_id, "failed", result=str(exc))
            raise

        # 4. EXIT — terminal success.
        await self._store.update_status(task_id, "completed", result=result)
        log.tasks.info(
            "[tasks] react_runner: drive completed",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id,
                "result_len": len(result),
            }},
        )
        return await self._store.get(task_id)
