"""Tests for the ``update_plan`` tool — replacing the whole shared plan."""

from __future__ import annotations

from stackowl.tools.base import ToolResult
from stackowl.tools.planning.store import PlanStore
from stackowl.tools.planning.todo import TodoTool
from stackowl.tools.planning.update_plan import UpdatePlanTool


async def test_replaces_whole_plan_with_explanation() -> None:
    store = PlanStore()
    tool = UpdatePlanTool(store=store)
    res = await tool.execute(
        explanation="Initial plan for the task.",
        plan=[
            {"id": "1", "content": "research", "status": "in_progress"},
            {"id": "2", "content": "write", "status": "pending"},
        ],
    )
    assert res.success
    assert "Initial plan for the task." in res.output
    assert "research" in res.output
    assert [i.id for i in store.read()] == ["1", "2"]


async def test_replace_swaps_prior_plan() -> None:
    store = PlanStore()
    tool = UpdatePlanTool(store=store)
    await tool.execute(plan=[{"id": "old", "content": "stale", "status": "pending"}])
    res = await tool.execute(plan=[{"id": "new", "content": "fresh", "status": "pending"}])
    assert res.success
    assert [i.id for i in store.read()] == ["new"]
    assert "stale" not in res.output


async def test_two_in_progress_auto_corrected_not_error() -> None:
    store = PlanStore()
    tool = UpdatePlanTool(store=store)
    res = await tool.execute(
        explanation="two active",
        plan=[
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "in_progress"},
        ],
    )
    # Auto-corrected, NOT rejected.
    assert res.success
    in_progress = [i.id for i in store.read() if i.status == "in_progress"]
    assert in_progress == ["1"]


async def test_empty_plan_clears_slot_structured() -> None:
    store = PlanStore()
    tool = UpdatePlanTool(store=store)
    await tool.execute(plan=[{"id": "1", "content": "a", "status": "pending"}])
    res = await tool.execute(explanation="done", plan=[])
    assert res.success  # structured, not an error
    assert store.read() == []
    assert "cleared" in res.output.lower()


async def test_missing_plan_structured_not_raise() -> None:
    tool = UpdatePlanTool(store=PlanStore())
    res = await tool.execute(explanation="oops")
    assert isinstance(res, ToolResult)
    assert not res.success
    assert res.error is not None
    assert "plan" in res.error


async def test_non_list_plan_structured() -> None:
    tool = UpdatePlanTool(store=PlanStore())
    res = await tool.execute(plan="not-a-list")
    assert not res.success
    assert res.error is not None


async def test_junk_items_self_heal() -> None:
    store = PlanStore()
    tool = UpdatePlanTool(store=store)
    res = await tool.execute(
        plan=[{"id": "1", "content": "ok", "status": "pending"}, "junk", 42]
    )
    assert res.success
    assert [i.id for i in store.read()] == ["1"]


async def test_manifest_severity_and_group() -> None:
    m = UpdatePlanTool().manifest
    assert m.action_severity == "read"
    assert m.toolset_group == "planning"
    assert m.name == "update_plan"


async def test_shared_slot_update_plan_then_todo() -> None:
    """Prove todo + update_plan write ONE shared slot."""
    store = PlanStore()
    update_plan = UpdatePlanTool(store=store)
    todo = TodoTool(store=store)

    # update_plan lays out the whole plan...
    await update_plan.execute(
        explanation="plan",
        plan=[
            {"id": "1", "content": "research", "status": "in_progress"},
            {"id": "2", "content": "write", "status": "pending"},
        ],
    )
    # ...then todo mutates ONE item in the SAME slot.
    res = await todo.execute(action="set_status", id="1", status="completed")
    assert res.success

    state = {i.id: i.status for i in store.read()}
    assert state == {"1": "completed", "2": "pending"}

    # And a todo add lands in the same plan update_plan replaced.
    await todo.execute(action="add", items=[{"id": "3", "content": "review", "status": "pending"}])
    assert [i.id for i in store.read()] == ["1", "2", "3"]


async def test_both_registered_in_with_defaults_sharing_one_store() -> None:
    from stackowl.tools.registry import ToolRegistry

    registry = ToolRegistry.with_defaults()
    todo = registry.get("todo")
    update_plan = registry.get("update_plan")
    assert todo is not None
    assert update_plan is not None

    # Prove the shared slot end-to-end through the registry-wired instances:
    # update_plan writes, todo reads the SAME plan back.
    await update_plan.execute(plan=[{"id": "x", "content": "via registry", "status": "pending"}])
    res = await todo.execute(action="list")
    assert res.success
    assert "via registry" in res.output
