"""Wiring: commit_coupling resolution drives the honest-terminal gate (D1 §6.2).

Drives ``DelegateTaskTool.execute`` under a DURABLE parent (task_id + active
DurableReActContext + db_pool) and asserts the §6.2 resolution end-to-end:

* never-started child + retriable live status ⇒ DEFINITE safe-retry → the child
  IS re-delegated (delegate() called again), not halted honest_uncertain.
* terminal child whose only effect is transactional+committed ⇒ DEFINITE done →
  the persisted child result is reused.
* an unconfirmed effect in-flight (intent, not committed) ⇒ honest_uncertain →
  NO re-delegation (delegate() called once).

A NON-durable parent (no task_id) keeps Story-D's _can_side_effect honest-terminal
behavior verbatim (a write-capable timeout halts honest_uncertain, called once).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.registry import OwlAgentManifest, OwlRegistry
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import DelegateTaskTool

_WRITE_CAPABLE = BoundsSpec(tools=frozenset({"edit"}))  # edit = write severity


class _ScriptedDelegator:
    """Returns successive A2AResults from a script and records every call."""

    def __init__(self, results: list[A2AResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def delegate(
        self, *, from_owl: str, to_owl: str, sub_task: str, parent_state: PipelineState
    ) -> A2AResult:
        self.calls.append({"to_owl": to_owl})
        return self._results.pop(0)


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


def _registry(bounds: BoundsSpec | None) -> OwlRegistry:
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(
        name="analyst", role="data-analyst", system_prompt="analyse",
        model_tier="standard", bounds=bounds,
    ))
    return reg


def _services(pool: DbPool | None, delegator: object, bounds: BoundsSpec | None) -> StepServices:
    from stackowl.tools.registry import ToolRegistry
    return StepServices(
        owl_registry=_registry(bounds),
        a2a_delegator=delegator,  # type: ignore[arg-type]
        db_pool=pool,
        tool_registry=ToolRegistry.with_defaults(),
    )


def _record(out: str) -> dict[str, object]:
    return json.loads(out)["record"]


def _child_id(parent_task_id: str, iteration: int, goal: str, to_owl: str) -> str:
    canonical = {"goal": goal, "to_owl": to_owl, "role": None, "context": None}
    key = idempotency_key(parent_task_id, iteration, "delegate_task", canonical)
    return derive_child_task_id(key)


async def _run_durable(
    pool: DbPool, delegator: object, bounds: BoundsSpec | None,
    *, parent_task_id: str, iteration: int, goal: str, to_owl: str,
) -> object:
    token = set_services(_services(pool, delegator, bounds))
    trace_token = TraceContext.start(
        "sess", trace_id="tr", owl_name="secretary",
        task_id=parent_task_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id=parent_task_id, owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=iteration,
    )
    try:
        with activate(ctx):
            return await DelegateTaskTool().execute(goal=goal, to_owl=to_owl)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)


@pytest.mark.asyncio
async def test_durable_never_started_is_safe_retry(pool: DbPool) -> None:
    """A durable write-capable child that NEVER ledgered an effect + a retriable
    live status ⇒ DEFINITE safe-retry: the child IS re-delegated (no honest halt).
    """
    # Two timeouts: the first triggers the gate; safe_retry must re-delegate (2nd).
    delegator = _ScriptedDelegator([
        A2AResult(status="timeout", resolved_owl="analyst"),
        A2AResult(status="timeout", resolved_owl="analyst"),
    ])
    await _run_durable(
        pool, delegator, _WRITE_CAPABLE,
        parent_task_id="p-safe", iteration=1, goal="g", to_owl="analyst",
    )
    # Re-delegated (safe-retry), proving the gate did NOT halt honest_uncertain.
    assert len(delegator.calls) >= 2


@pytest.mark.asyncio
async def test_durable_terminal_transactional_is_done(pool: DbPool) -> None:
    """A durable child that is terminal with ONLY a transactional+committed effect
    ⇒ DEFINITE done: the persisted child result is reused (not honest_uncertain).
    """
    cid = _child_id("p-done", 1, "g", "analyst")
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    # Pre-create the child terminal with a persisted result (create_child_task is
    # idempotent ON CONFLICT DO NOTHING, so execute() won't clobber this row).
    await store.create_child_task(
        child_task_id=cid, parent_task_id="p-done", parent_owl="secretary",
        delegate_key="k", goal="g", owl_name="analyst", channel="internal",
    )
    await store.update_status(cid, "completed", result="the durable answer")
    # Ledger a transactional+committed effect under the child (write_file=transactional).
    ledger = SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID)
    await ledger.begin(cid, 0, "write_file", {"a": 1})
    await ledger.commit(cid, 0, "write_file", {"a": 1}, "wrote")

    delegator = _ScriptedDelegator([A2AResult(status="timeout", resolved_owl="analyst")])
    res = await _run_durable(
        pool, delegator, _WRITE_CAPABLE,
        parent_task_id="p-done", iteration=1, goal="g", to_owl="analyst",
    )
    assert res.success is True  # type: ignore[attr-defined]
    rec = _record(res.output)  # type: ignore[attr-defined]
    assert rec["status"] == "ok"
    assert "the durable answer" in str(rec["result"])
    # delegate() called once: NO re-delegation (done reuses the persisted answer).
    assert len(delegator.calls) == 1


@pytest.mark.asyncio
async def test_durable_unconfirmed_in_flight_stays_honest_uncertain(pool: DbPool) -> None:
    """A durable child with an unconfirmed effect in-flight (intent, not committed)
    and not terminal ⇒ honest_uncertain: NO re-delegation (called once).
    """
    cid = _child_id("p-unc", 1, "g", "analyst")
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id=cid, parent_task_id="p-unc", parent_owl="secretary",
        delegate_key="k", goal="g", owl_name="analyst", channel="internal",
    )
    # Child stays running (not terminal); shell = unconfirmed coupling, intent only.
    ledger = SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID)
    await ledger.begin(cid, 0, "shell", {"command": "x"})  # intent, never committed

    delegator = _ScriptedDelegator([A2AResult(status="timeout", resolved_owl="analyst")])
    res = await _run_durable(
        pool, delegator, _WRITE_CAPABLE,
        parent_task_id="p-unc", iteration=1, goal="g", to_owl="analyst",
    )
    assert res.success is False  # type: ignore[attr-defined]
    rec = _record(res.output)  # type: ignore[attr-defined]
    assert rec["status"] == "uncertain"
    assert "FAILED" in str(rec["result"])
    assert len(delegator.calls) == 1  # NO re-delegation


@pytest.mark.asyncio
async def test_non_durable_write_capable_timeout_unchanged(pool: DbPool) -> None:
    """NON-durable parent (no task_id): Story-D _can_side_effect path is unchanged —
    a write-capable timeout halts honest_uncertain, delegate() called once.
    """
    delegator = _ScriptedDelegator([A2AResult(status="timeout", resolved_owl="analyst")])
    token = set_services(_services(pool, delegator, _WRITE_CAPABLE))
    trace_token = TraceContext.start("sess", trace_id="tr", owl_name="secretary")
    try:
        res = await DelegateTaskTool().execute(goal="g", to_owl="analyst")
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)
    assert res.success is False
    rec = _record(res.output)
    assert rec["status"] == "uncertain"
    assert len(delegator.calls) == 1  # no re-delegation, unchanged Story-D behavior
