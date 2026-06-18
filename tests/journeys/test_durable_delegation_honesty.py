"""commit_coupling honesty + non-durable-parent-unchanged journeys (D1 §11.2).

Asserts the user-visible OUTCOME of the honesty axis:
  (a) non-durable parent → honest_uncertain on timeout + no tasks row (UNCHANGED).
  (b) durable + unconfirmed effect in-flight → honest_uncertain (not false-safe).
  (c) durable + transactional committed → done; durable never-started → safe-retry.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.registry import OwlAgentManifest, OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.agents.delegate_task import (
    DelegateTaskTool,
    resolve_commit_coupling_answer,
)
from stackowl.tools.registry import ToolRegistry

# A write-capable specialist (edit = write severity) the parent can delegate to,
# so the target both RESOLVES (delegate_task resolves the owl up-front) and is
# treated as side-effecting by _can_side_effect — exactly the precondition for
# the Story-D honest-uncertain halt to fire on a timeout.
_WRITE_CAPABLE = BoundsSpec(tools=frozenset({"edit"}))
_SPECIALIST = "filer"


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


class _TimeoutDelegator:
    async def delegate(self, *, from_owl, to_owl, sub_task, parent_state):  # noqa: ANN001
        return A2AResult(status="timeout", resolved_owl=to_owl)


async def test_non_durable_parent_timeout_is_honest_uncertain_no_tasks_row(pool: DbPool) -> None:
    # A write-capable specialist must exist + RESOLVE so the delegation actually
    # runs (delegate_task resolves the owl up-front) and _can_side_effect(target)
    # is True — the precondition for the Story-D honest-uncertain halt on timeout.
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(
        name=_SPECIALIST, role="report-filer", system_prompt="file it",
        model_tier="standard", bounds=_WRITE_CAPABLE,
    ))
    token = set_services(StepServices(
        owl_registry=reg, a2a_delegator=_TimeoutDelegator(), db_pool=pool,
        tool_registry=ToolRegistry.with_defaults(),
    ))
    trace_token = TraceContext.start("s", trace_id="tr", owl_name="secretary")  # NO task_id
    try:
        res = await DelegateTaskTool().execute(goal="file it", to_owl=_SPECIALIST)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)
    # The honest-uncertain contract is preserved for non-durable parents.
    assert res.success is False
    assert "uncertain" in (res.output or "") or "NOT" in (res.error or "")
    rows = await pool.fetch_all("SELECT task_id FROM tasks", ())
    assert rows == [], "non-durable parent must not create a durable child row"


def test_resolution_table_done_safe_retry_uncertain() -> None:
    assert resolve_commit_coupling_answer(
        child_started=False, has_uncertain_effect=False,
        has_uncommitted_intent=False, child_terminal=False) == "safe_retry"
    assert resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False,
        has_uncommitted_intent=False, child_terminal=True) == "done"
    assert resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=True,
        has_uncommitted_intent=False, child_terminal=False) == "honest_uncertain"
