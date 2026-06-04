"""Durable execution primitives (Stage 1 Pass 3a + S1 durable-react, agentic-os).

Standalone durable-state building blocks for long-running agentic tasks:

* :class:`DurableTask` / :data:`TaskStatus` — the persisted goal record.
* :class:`DurableTaskStore` — owner-scoped CRUD over the ``tasks`` table.
* :class:`SideEffectLedger` / :class:`LedgerDecision` — the exactly-once
  intent->commit contract that makes side-effecting tool calls replay-safe,
  with :func:`idempotency_key` and :func:`is_side_effecting` helpers.
* :class:`ReActCheckpoint` — the durable working-set snapshot for a ReAct loop
  iteration, with :func:`serialize` / :func:`deserialize` round-trip helpers
  and :class:`ReActCheckpointDecodeError` for malformed blobs.

These are primitives only: the executor / graph runner / scheduler wiring that
consumes them is intentionally out of scope for this pass.
"""

from __future__ import annotations

from stackowl.pipeline.durable.executor import (
    CallableStep,
    DurableExecutor,
    TaskStep,
)
from stackowl.pipeline.durable.ledger import (
    LedgerDecision,
    LedgerOutcome,
    SideEffectLedger,
    idempotency_key,
    is_side_effecting,
)
from stackowl.pipeline.durable.react_checkpoint import (
    ReActCheckpoint,
    ReActCheckpointDecodeError,
    deserialize,
    serialize,
)
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask, TaskStatus

__all__ = [
    "CallableStep",
    "DurableExecutor",
    "DurableTask",
    "DurableTaskStore",
    "LedgerDecision",
    "LedgerOutcome",
    "ReActCheckpoint",
    "ReActCheckpointDecodeError",
    "SideEffectLedger",
    "TaskStatus",
    "TaskStep",
    "deserialize",
    "idempotency_key",
    "is_side_effecting",
    "serialize",
]
