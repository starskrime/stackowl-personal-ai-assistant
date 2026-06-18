"""A2A timeout cancels the asyncio task but a durable child survives recovering (D1 §9).

Also pins D1 §8.2 Break-A: the child's durable scope rides on the child
``PipelineState`` (carried by VALUE and stamped inside the child's OWN
``TraceContext.start`` in ``AsyncioBackend.run``) — never via a ``.set()`` on the
parent coroutine's ContextVar. So after a child runs, the PARENT frame's
``TraceContext`` task_id is unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


class _SlowSpecialistDelegator(A2ADelegator):
    """A delegator whose child blocks past the timeout so the parent CANCELS it.

    Overrides only the run seam to ``sleep`` inside the real ``_run_specialist``
    ``try`` block — so the parent's ``receive`` times out and cancels the child
    asyncio task, exercising the REAL ``except asyncio.CancelledError`` path. The
    child's durable row (created ``running``) must survive the cancel.
    """

    async def _run_under_governor(
        self, backend: AsyncioBackend, sub_state: PipelineState,
    ) -> PipelineState:
        await asyncio.sleep(5.0)  # cancelled long before this elapses
        return await super()._run_under_governor(backend, sub_state)


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


async def test_timeout_does_not_mark_durable_child_failed(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    # Seed a running durable child the specialist will "run" under.
    child_id = "child-cancel"
    await store.create_child_task(
        child_task_id=child_id, parent_task_id="p", parent_owl="secretary",
        delegate_key="dk", goal="sub", owl_name="scout", channel="cli",
    )

    # A delegator whose specialist never replies → forces the timeout/cancel path.
    services = StepServices(db_pool=pool)
    delegator = _SlowSpecialistDelegator(A2AQueue(), services, timeout_seconds=0.05)

    parent_state = PipelineState(
        trace_id="tr", session_id="s", input_text="sub", channel="internal",
        owl_name="secretary", pipeline_step="dispatch",
        task_id=child_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    res = await delegator.delegate(
        from_owl="secretary", to_owl="scout", sub_task="sub", parent_state=parent_state,
    )
    assert res.status == "timeout"
    # Let any cancellation settle.
    await asyncio.sleep(0.05)
    rec = await store.get(child_id)
    # The durable child must NOT be marked failed by the cancel — it stays
    # running/recovering so recovery can resume it.
    assert rec.status in ("running", "recovering"), (
        f"durable child wrongly finalized to {rec.status!r} on a2a cancel"
    )


async def test_break_a_child_scope_does_not_leak_into_parent_frame(pool: DbPool) -> None:
    """Break-A (D1 §8.2): a child's durable scope never mutates the parent's ContextVar.

    The parent coroutine has NO durable scope of its own. The child carries the
    durable id by VALUE on its ``PipelineState`` and stamps it inside its OWN
    ``TraceContext.start``. After the child runs (and is cancelled on timeout),
    the parent frame's ``TraceContext`` task_id must STILL be ``None`` — proving
    ``_run_specialist`` never did a parent-frame ``.set()``.
    """
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child_id = "child-breaka"
    await store.create_child_task(
        child_task_id=child_id, parent_task_id="p", parent_owl="secretary",
        delegate_key="dk", goal="sub", owl_name="scout", channel="cli",
    )

    services = StepServices(db_pool=pool)
    delegator = _SlowSpecialistDelegator(A2AQueue(), services, timeout_seconds=0.05)

    parent_state = PipelineState(
        trace_id="tr", session_id="s", input_text="sub", channel="internal",
        owl_name="secretary", pipeline_step="dispatch",
        task_id=child_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )

    # The parent coroutine's own ContextVar scope starts clean (no durable task).
    assert TraceContext.get()["task_id"] is None
    assert TraceContext.durable_owner_id() is None

    res = await delegator.delegate(
        from_owl="secretary", to_owl="scout", sub_task="sub", parent_state=parent_state,
    )
    assert res.status == "timeout"
    await asyncio.sleep(0.05)

    # Break-A invariant: the child's durable scope (child_id) never leaked back
    # onto the parent coroutine's ContextVar — it rode on the child state and was
    # stamped/reset inside the child's own TraceContext.start, never via a .set()
    # on this (parent) frame.
    assert TraceContext.get()["task_id"] is None, (
        "Break-A violated: child durable scope leaked into the parent frame's ContextVar"
    )
    assert TraceContext.durable_owner_id() is None
