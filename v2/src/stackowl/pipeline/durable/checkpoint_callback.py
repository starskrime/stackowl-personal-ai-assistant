"""checkpoint_callback — the per-iteration checkpoint callback factory (S4).

This module ties S1+S2+S3 together into the single ``on_iteration_complete``
callback a durable ReAct drive uses.  It is a **unit** of the durable-ReAct
integration: it does not drive a live provider (that is S5), it only produces
the callback that S5 will hand to ``complete_with_tools``.

Responsibilities of the produced callback, fired once at the END of each
completed ReAct iteration N (design §2.1, §2.4):

1. **Persist** a :class:`~stackowl.pipeline.durable.react_checkpoint.ReActCheckpoint`
   for iteration N (the resume cursor: messages + tool-call records + counter),
   and advance the durable task's ``current_step`` to ``N + 1`` so the persisted
   record reflects "iterations 0..N completed".
2. **Align** the active context: set ``ctx.iteration = N + 1`` so the NEXT
   iteration's side-effecting tool dispatches — which read ``ctx.iteration`` as
   the ledger ``step_index`` via :mod:`stackowl.pipeline.durable.ledger_guard` —
   land under the correct step_index.

The alignment invariant (the property that makes exactly-once survive resume)
-----------------------------------------------------------------------------
Before the drive starts, the executor (S5) sets ``ctx.iteration = 0`` — the
index of the first iteration.  The provider then:

* runs iteration 0 with ``ctx.iteration == 0`` (its side-effecting tools
  dispatch with ledger ``step_index == 0``);
* at the bottom of iteration 0 fires ``on_iteration_complete(state.iteration=0)``;
  this callback persists the iter-0 checkpoint, writes ``current_step = 1`` and
  sets ``ctx.iteration = 1``;
* runs iteration 1 with ``ctx.iteration == 1`` (``step_index == 1``); …

So the INVARIANT is: **during iteration N, ``ctx.iteration == N``**, which equals
the ``state.iteration == N`` the callback reports at the end of iteration N.  The
callback advances ``ctx.iteration`` to ``N + 1`` to prepare iteration N + 1.

Resume note (for S5 — NOT implemented here)
-------------------------------------------
On resume, restoring from a checkpoint whose ``iteration == K`` means iterations
0..K completed.  The executor (S5) therefore seeds ``ctx.iteration = K + 1`` so
the loop re-enters at iteration K + 1 with the correct step_index.  This module
deliberately does not implement resume/re-entry.  S5 must treat the checkpoint
blob's ``iteration`` as authoritative over ``task.current_step``: in the crash
window where save_checkpoint succeeded but update_status did not, current_step
reads N while the blob reads iteration=N — both mean 'N iterations completed'.

KNOWN-DEFERRED (S9 hardening — do NOT fix here)
-----------------------------------------------
If ONE iteration dispatches the SAME tool with the SAME args twice, both calls
use ``step_index == N`` and therefore collide on the same idempotency key (the
second is wrongly treated as ``already_committed``).  Disambiguating identical
in-iteration calls requires an intra-iteration ordinal folded into the key
(design §2.4 caveat / OD3); that is S9 hardening and is intentionally not
addressed by S4.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.durable.context import DurableReActContext
from stackowl.pipeline.durable.react_checkpoint import ReActCheckpoint, serialize
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.providers.react_callback import IterationCallback, ReActIterationState


def make_checkpoint_callback(
    ctx: DurableReActContext,
    store: DurableTaskStore,
) -> IterationCallback:
    """Build the ``on_iteration_complete`` callback for one durable drive.

    The returned coroutine is invoked by the provider at the bottom of each
    completed ReAct iteration.  It persists the iteration's checkpoint, advances
    the durable task's ``current_step``, and aligns ``ctx.iteration`` so the next
    iteration's ledger ``step_index`` is correct (see module docstring).

    Args:
        ctx: the active :class:`DurableReActContext` for this drive.  Its
            mutable ``iteration`` field is advanced by the callback — this is the
            critical alignment with the ledger guard's ``step_index``.
        store: the owner-scoped :class:`DurableTaskStore` to checkpoint into.

    Returns:
        An :data:`~stackowl.providers.react_callback.IterationCallback`
        (``async def (state: ReActIterationState) -> None``).  If a persistence
        write fails the callback logs the error and re-raises — the provider lets
        it propagate and the executor (S5) owns recovery.  Nothing is swallowed.
    """

    async def _on_iter(state: ReActIterationState) -> None:
        # 1. ENTRY — a ReAct iteration completed; build its durable checkpoint.
        log.tasks.debug(
            "[tasks] checkpoint_callback: entry",
            extra={"_fields": {
                "task_id": ctx.task_id,
                "owner_id": ctx.owner_id,
                "iteration": state.iteration,
                "msg_count": len(state.messages),
                "call_count": len(state.tool_call_records),
            }},
        )
        checkpoint = ReActCheckpoint(
            iteration=state.iteration,
            messages=state.messages,
            tool_call_records=state.tool_call_records,
        )

        # 2. DECISION — persist the checkpoint blob AND advance current_step.
        #    current_step = state.iteration + 1 records "iterations 0..N done".
        next_step = state.iteration + 1
        try:
            blob = serialize(checkpoint)
            log.tasks.debug(
                "[tasks] checkpoint_callback: persisting checkpoint",
                extra={"_fields": {
                    "task_id": ctx.task_id,
                    "iteration": state.iteration,
                    "next_step": next_step,
                    "blob_len": len(blob),
                }},
            )
            # 3. STEP — durable writes (blob, then status/current_step).
            await store.save_checkpoint(ctx.task_id, blob)
            await store.update_status(ctx.task_id, "running", current_step=next_step)
        except Exception as exc:
            # No silent swallow: log and re-raise so the executor owns recovery.
            log.tasks.error(
                "[tasks] checkpoint_callback: checkpoint persistence failed",
                exc_info=exc,
                extra={"_fields": {
                    "task_id": ctx.task_id,
                    "owner_id": ctx.owner_id,
                    "iteration": state.iteration,
                    "next_step": next_step,
                }},
            )
            raise

        # CRITICAL ALIGNMENT — advance ctx.iteration so the NEXT iteration's
        # side-effecting dispatches read the correct ledger step_index.
        ctx.iteration = next_step

        # 4. EXIT
        log.tasks.debug(
            "[tasks] checkpoint_callback: exit — checkpointed and aligned",
            extra={"_fields": {
                "task_id": ctx.task_id,
                "completed_iteration": state.iteration,
                "ctx_iteration": ctx.iteration,
                "current_step": next_step,
            }},
        )

    return _on_iter
