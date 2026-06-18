"""ReAct iteration callback types — shared neutral carrier for S3.

Placed in ``providers/`` so providers can reference it without any upward
dependency on ``pipeline.durable`` (B-boundary rule: providers must NOT import
pipeline.durable).  S4 wires a concrete callback from the execute step; until
then ``on_iteration_complete=None`` everywhere → zero behavior change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReActIterationState:
    """Plain JSON-serialisable snapshot of one completed ReAct iteration.

    All fields are immutable copies of the provider's running state so a
    downstream checkpoint callback cannot accidentally mutate loop-internal data.

    Attributes:
        iteration: Zero-based monotonic index of the completed iteration.  The
            same counter the loop uses, so it aligns with the ledger
            ``step_index`` defined in §2.4 of the durable-ReAct design.
        messages: Snapshot of the full ``messages`` list **after** this
            iteration's tool-call/observation turns have been appended (i.e.
            ready for the next LLM round).  Shallow copy — each dict is the
            same object the loop holds, but the list itself is a new list so
            appends by the callback cannot corrupt loop state.
        tool_call_records: Snapshot of ``all_calls`` accumulated up to and
            including this iteration — same shallow-copy contract as
            ``messages``.
    """

    iteration: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_call_records: list[dict[str, Any]] = field(default_factory=list)


# Callback type: receives the completed-iteration state and may do async work
# (checkpoint write, metric recording, …).  If it raises, the exception
# propagates to the caller — providers do NOT swallow callback errors.
#
# Return contract (Task 9 — live-steer splice): the callback returns a list of
# messages to FOLD into the provider's live ``messages`` list before the next LLM
# round (e.g. a ``[{"role": "user", "content": "[steering] …"}]`` injection), or
# ``None`` to fold nothing.  Side-effect-only callbacks (checkpoint, budget gate)
# return ``None``.  Providers do ``folded = await cb(...); if folded:
# messages.extend(folded)`` at every call site, so a returned list is observed by
# the very next ``complete_with_tools`` LLM call.
IterationCallback = Callable[
    [ReActIterationState], Awaitable[list[dict[str, Any]] | None]
]
