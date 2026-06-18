"""Parent terminalizes the durable child when the delegation resolves (D1 §7.2).

The child's terminal status is a PROJECTION of the parent's delegate resolution:
a successful (ok / recovered) ladder result stamps the child ``completed``; an
honest-terminal (uncertain / irrelevant) result stamps it ``failed``. The
non-durable path never terminalizes (no child row exists).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import DelegateTaskTool


class _OkDelegator:
    async def delegate(self, *, from_owl: str, to_owl: str, sub_task: str,
                       parent_state: PipelineState) -> A2AResult:
        return A2AResult(status="ok", content="handled fully", resolved_owl=to_owl)


class _FailDelegator:
    """Always returns an honest-terminal (refused) — the ladder cannot recover it,
    and a refused status is NOT re-delegatable, so it terminalizes ``failed``."""

    async def delegate(self, *, from_owl: str, to_owl: str, sub_task: str,
                       parent_state: PipelineState) -> A2AResult:
        return A2AResult(status="refused", resolved_owl=to_owl)


def _registry() -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    # Layer in scout/librarian/archivist so `to_owl="scout"` resolves to a real
    # target and the delegation round-trip reaches the delegator.
    registry.register_builtin_personas()
    return registry


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


async def test_completed_child_is_terminalized_completed(pool: DbPool) -> None:
    token = set_services(StepServices(
        owl_registry=_registry(),
        a2a_delegator=_OkDelegator(), db_pool=pool,
    ))
    args = {"goal": "do x", "to_owl": "scout"}
    trace_token = TraceContext.start(
        "s", trace_id="tr", owl_name="secretary",
        task_id="parent-1", durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id="parent-1", owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=0,
    )
    try:
        with activate(ctx):
            await DelegateTaskTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    canonical = {"goal": "do x", "to_owl": "scout", "role": None, "context": None}
    child_id = derive_child_task_id(idempotency_key("parent-1", 0, "delegate_task", canonical))
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child = await store.get(child_id)
    assert child.status == "completed"


async def test_failed_child_is_terminalized_failed(pool: DbPool) -> None:
    token = set_services(StepServices(
        owl_registry=_registry(),
        a2a_delegator=_FailDelegator(), db_pool=pool,
    ))
    args = {"goal": "do x", "to_owl": "scout"}
    trace_token = TraceContext.start(
        "s", trace_id="tr", owl_name="secretary",
        task_id="parent-1", durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id="parent-1", owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=0,
    )
    try:
        with activate(ctx):
            await DelegateTaskTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    canonical = {"goal": "do x", "to_owl": "scout", "role": None, "context": None}
    child_id = derive_child_task_id(idempotency_key("parent-1", 0, "delegate_task", canonical))
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child = await store.get(child_id)
    assert child.status == "failed"


async def test_non_durable_delegation_creates_no_child_row(pool: DbPool) -> None:
    token = set_services(StepServices(
        owl_registry=_registry(),
        a2a_delegator=_OkDelegator(), db_pool=pool,
    ))
    # No task_id on TraceContext + no active DurableReActContext → non-durable.
    trace_token = TraceContext.start("s", trace_id="tr", owl_name="secretary")
    try:
        await DelegateTaskTool().execute(goal="do x", to_owl="scout")
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    rows = await pool.fetch_all("SELECT task_id FROM tasks", ())
    assert rows == []
