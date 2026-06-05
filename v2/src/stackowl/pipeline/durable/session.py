"""DurableSession — the assembled durable scope for one pipeline drive (B2).

A durable ReAct drive needs three collaborators wired to the SAME owning
principal and the SAME resume cursor:

* a :class:`~stackowl.pipeline.durable.ledger.SideEffectLedger` — the
  exactly-once intent->commit store,
* a :class:`~stackowl.pipeline.durable.store.DurableTaskStore` — owner-scoped
  persistence for the task row + its checkpoints, and
* a :class:`~stackowl.pipeline.durable.context.DurableReActContext` — the active
  per-drive scope the ledger guard reads (task_id, owner, ledger, iteration).

Assembling those three from a :class:`~stackowl.pipeline.state.PipelineState`
involves two non-obvious rules:

1. **Owner resolution** — ``state.durable_owner_id or DEFAULT_PRINCIPAL_ID``.
2. **Iteration seeding** — ``state.durable_resume_iteration`` is authoritative
   on resume, but it is an ``int | None`` where ``0`` is a VALID seed (resume at
   iteration 0). A truthy ``or 0`` would therefore wrongly coerce a genuine
   resume-at-0 the same as "no resume". The seed uses an explicit
   ``is not None`` check so iteration 0 is preserved.

This factory OWNS both rules so B2 (execute), B3 (router/park handling) and B4
(checkpoint reconstruction) build the durable scope identically — there is one
assembly seam, not three divergent inline copies.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.pipeline.durable.context import DurableReActContext
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@dataclass(frozen=True)
class DurableSession:
    """The three collaborators of one durable drive, wired to one owner.

    Frozen: the assembled scope is identity-immutable for the lifetime of a
    drive. (``ctx.iteration`` is still mutable on the context object itself —
    the executor advances it per iteration — but which ctx/ledger/store this
    session refers to never changes.)
    """

    ctx: DurableReActContext
    ledger: SideEffectLedger
    store: DurableTaskStore


def durable_session_for_state(state: PipelineState, db: DbPool) -> DurableSession:
    """Assemble the :class:`DurableSession` for ``state``'s durable drive.

    Resolves the owning principal and the resume iteration from ``state`` (see
    module docstring), then builds the owner-scoped ledger + store and the active
    :class:`DurableReActContext`. ``state.task_id`` MUST be set (the caller only
    reaches the durable path when it is); callers on the non-durable path never
    invoke this.

    Args:
        state: the durable pipeline turn (``state.task_id`` is non-None).
        db: the live :class:`DbPool` backing the ledger/store.

    Returns:
        A :class:`DurableSession` whose ``ctx``/``ledger``/``store`` are all
        bound to the resolved owner.
    """
    # 1. ENTRY
    task_id = state.task_id or ""  # caller guarantees non-None; guard for typing
    owner_id = state.durable_owner_id or DEFAULT_PRINCIPAL_ID
    # 2. DECISION — explicit None check so a genuine resume-at-iteration-0 is
    #    preserved (a truthy `or 0` would conflate seed 0 with "no resume").
    seed_iteration = (
        state.durable_resume_iteration
        if state.durable_resume_iteration is not None
        else 0
    )
    log.tasks.debug(
        "[tasks] durable_session: assembling",
        extra={"_fields": {
            "task_id": task_id,
            "owner_id": owner_id,
            "seed_iteration": seed_iteration,
            "resuming": state.durable_resume_messages is not None,
        }},
    )
    # 3. STEP — build the three owner-scoped collaborators.
    ledger = SideEffectLedger(db, owner_id)
    store = DurableTaskStore(db, owner_id)
    ctx = DurableReActContext(
        task_id=task_id,
        owner_id=owner_id,
        ledger=ledger,
        iteration=seed_iteration,
    )
    # 4. EXIT
    log.tasks.debug(
        "[tasks] durable_session: assembled",
        extra={"_fields": {
            "task_id": task_id, "owner_id": owner_id, "iteration": ctx.iteration,
        }},
    )
    return DurableSession(ctx=ctx, ledger=ledger, store=store)
