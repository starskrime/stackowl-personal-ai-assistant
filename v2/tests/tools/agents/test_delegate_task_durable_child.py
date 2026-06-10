"""delegate_task creates a durable child sub-task under a durable parent (D1 §8.3).

Asserts the WIRING: when the parent turn carries a durable scope (task_id +
active DurableReActContext + db_pool), the parent_state handed to the delegator
carries the derived child_task_id (not the parent's task_id) and a child tasks
row is claimed. A non-durable parent is unchanged (no task_id on the child state).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import DelegateTaskTool


class _CapturingDelegator:
    """Records the parent_state it was handed; replies a trivial ok."""

    def __init__(self) -> None:
        self.seen_parent_state: PipelineState | None = None

    async def delegate(self, *, from_owl: str, to_owl: str, sub_task: str,
                       parent_state: PipelineState) -> A2AResult:
        self.seen_parent_state = parent_state
        return A2AResult(status="ok", content="handled", resolved_owl=to_owl)


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _services(pool: DbPool | None, delegator: object) -> StepServices:
    from stackowl.owls.registry import OwlRegistry
    registry = OwlRegistry.with_default_secretary()
    # Layer in scout/librarian/archivist so `to_owl="scout"` resolves to a real
    # target and the delegation round-trip reaches the capturing delegator.
    registry.register_builtin_personas()
    return StepServices(
        owl_registry=registry,
        a2a_delegator=delegator,  # type: ignore[arg-type]
        db_pool=pool,
    )


async def test_durable_parent_child_carries_child_task_id(pool: DbPool) -> None:
    delegator = _CapturingDelegator()
    token = set_services(_services(pool, delegator))
    args = {"goal": "do the thing", "to_owl": "scout"}
    parent_task_id = "parent-1"
    trace_token = TraceContext.start(
        "sess", trace_id="tr", owl_name="secretary",
        task_id=parent_task_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id=parent_task_id, owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=2,
    )
    try:
        with activate(ctx):
            await DelegateTaskTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    assert delegator.seen_parent_state is not None
    # The parent's delegate_key keys on the validated (frozen) args dict.
    canonical = {"goal": "do the thing", "to_owl": "scout", "role": None, "context": None}
    expected_key = idempotency_key(parent_task_id, 2, "delegate_task", canonical)
    expected_child = derive_child_task_id(expected_key)
    assert delegator.seen_parent_state.task_id == expected_child
    assert delegator.seen_parent_state.task_id != parent_task_id
    # The child tasks row was claimed.
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child = await store.get(expected_child)
    assert child.parent_task_id == parent_task_id
    assert child.parent_owl == "secretary"


async def test_non_durable_parent_child_has_no_task_id(pool: DbPool) -> None:
    delegator = _CapturingDelegator()
    token = set_services(_services(pool, delegator))
    trace_token = TraceContext.start("sess", trace_id="tr", owl_name="secretary")
    try:
        await DelegateTaskTool().execute(goal="do x", to_owl="scout")
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)
    assert delegator.seen_parent_state is not None
    assert delegator.seen_parent_state.task_id is None
    rows = await pool.fetch_all("SELECT task_id FROM tasks", ())
    assert rows == []
