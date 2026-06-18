"""DurableReActContext — the per-drive durable scope for a ReAct loop (S2).

When a durable task is actively being driven, the executor (S4/S5 — NOT this
sub-story) activates a :class:`DurableReActContext` for the duration of one
provider loop ("drive").  While it is active, the ledger guard
(:mod:`stackowl.pipeline.durable.ledger_guard`) routes every side-effecting
tool call through the :class:`~stackowl.pipeline.durable.ledger.SideEffectLedger`
for exactly-once execution across crashes/replays.

The context carries exactly what the guard needs:

* ``task_id`` — the durable task whose ledger rows this drive writes.
* ``owner_id`` — the owning principal (already bound into the ledger, kept here
  for logging / cross-checking).
* ``ledger`` — the owner-scoped :class:`SideEffectLedger` instance.
* ``iteration`` — the monotonic per-iteration step_index folded into the ledger
  idempotency key (design §2.4).  All tool calls dispatched within one LLM round
  share that round's iteration index; it is bumped once per completed iteration
  by ``on_iteration_complete`` (S3/S4) and seeded from the persisted
  ``current_step`` on resume (S5).  It is therefore *mutable* — owned and
  advanced by the executor — which is why it lives on a mutable holder rather
  than on a frozen value object.

Activation uses :mod:`contextvars` (async-safe, per-task isolation) — NOT a
module global — so concurrent drives never see each other's context.  S2 leaves
the ContextVar **dormant**: nothing in production activates it yet, so
:func:`get_active` returns ``None`` on every existing code path and the guard is
a pass-through.  The executor begins calling :func:`activate` in S4/S5.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from stackowl.infra.observability import log
from stackowl.pipeline.durable.ledger import SideEffectLedger


@dataclass
class DurableReActContext:
    """The active durable scope for one ReAct drive.

    Mutable by design: ``iteration`` is the per-iteration step_index advanced by
    the executor as the loop progresses (design §2.4).  The identity fields
    (``task_id`` / ``owner_id`` / ``ledger``) are set once at construction and
    are not meant to change for the lifetime of a drive.
    """

    task_id: str
    owner_id: str
    ledger: SideEffectLedger
    #: The current ReAct iteration index (= ledger step_index).  Owned by the
    #: executor; bumped once per completed LLM round.  Starts at 0.
    iteration: int = field(default=0)


#: Per-task active durable context.  Default ``None`` → no durable task running
#: → the ledger guard is a pure pass-through (dormant in S2).
_active_durable_ctx: ContextVar[DurableReActContext | None] = ContextVar(
    "_active_durable_ctx", default=None
)


def get_active() -> DurableReActContext | None:
    """Return the durable context active in the current async scope, or ``None``.

    Returns ``None`` whenever no durable task is being driven — which is every
    existing code path until the executor activates a context (S4/S5).  The
    ledger guard treats ``None`` as "behave exactly as before".
    """
    return _active_durable_ctx.get()


@contextmanager
def activate(ctx: DurableReActContext) -> Iterator[DurableReActContext]:
    """Scope ``ctx`` as the active durable context for the duration of the block.

    Sets the :class:`~contextvars.ContextVar` on entry and resets it to its
    previous value on exit (even on error), so nested/sequential drives restore
    the prior scope cleanly.  Async-safe: each task gets its own view of the
    ContextVar, so concurrent drives never leak into one another.

    NOTE (S2): no production code path calls this yet — the executor wires it in
    S4/S5.  It exists now so the guard's read side (:func:`get_active`) has a
    well-defined, tested write side.
    """
    # 1. ENTRY
    log.tasks.debug(
        "[tasks] durable_ctx.activate: entry",
        extra={"_fields": {"task_id": ctx.task_id, "owner_id": ctx.owner_id, "iteration": ctx.iteration}},
    )
    token: Token[DurableReActContext | None] = _active_durable_ctx.set(ctx)
    try:
        # 2. STEP — yield the active context to the caller's scope
        yield ctx
    finally:
        # 3. EXIT — always restore the prior scope (no leak across drives)
        _active_durable_ctx.reset(token)
        log.tasks.debug(
            "[tasks] durable_ctx.activate: exit — context reset",
            extra={"_fields": {"task_id": ctx.task_id, "owner_id": ctx.owner_id}},
        )
