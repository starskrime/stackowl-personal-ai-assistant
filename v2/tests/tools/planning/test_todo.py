"""Tests for the ``todo`` tool — mutating the shared working checklist."""

from __future__ import annotations

from stackowl.tools.base import ToolResult
from stackowl.tools.planning.store import PlanStore
from stackowl.tools.planning.todo import TodoTool


async def test_add_items_rendered() -> None:
    tool = TodoTool(store=PlanStore())
    res = await tool.execute(
        action="add",
        items=[
            {"id": "1", "content": "research", "status": "in_progress"},
            {"id": "2", "content": "write", "status": "pending"},
        ],
    )
    assert res.success
    assert "research" in res.output
    assert "[>] 1. research (in_progress)" in res.output


async def test_replace_clears_prior() -> None:
    store = PlanStore()
    tool = TodoTool(store=store)
    await tool.execute(action="add", items=[{"id": "old", "content": "stale", "status": "pending"}])
    res = await tool.execute(
        action="replace", items=[{"id": "new", "content": "fresh", "status": "pending"}]
    )
    assert res.success
    assert "stale" not in res.output
    assert "fresh" in res.output


async def test_set_status_transition() -> None:
    store = PlanStore()
    tool = TodoTool(store=store)
    await tool.execute(action="add", items=[{"id": "1", "content": "task", "status": "in_progress"}])
    res = await tool.execute(action="set_status", id="1", status="completed")
    assert res.success
    # Completed item is no longer re-injected.
    assert "task" not in res.output or "(plan is empty)" in res.output
    assert store.read()[0].status == "completed"


async def test_set_status_unknown_id_structured() -> None:
    tool = TodoTool(store=PlanStore())
    res = await tool.execute(action="set_status", id="nope", status="completed")
    assert not res.success
    assert res.error is not None
    assert "nope" in res.error


async def test_dedup_on_repeated_id() -> None:
    tool = TodoTool(store=PlanStore())
    res = await tool.execute(
        action="replace",
        items=[
            {"id": "a", "content": "first", "status": "pending"},
            {"id": "a", "content": "second", "status": "pending"},
        ],
    )
    assert res.success
    assert res.output.count("a. ") == 1
    assert "second" in res.output


async def test_invalid_action_structured_not_raise() -> None:
    tool = TodoTool(store=PlanStore())
    res = await tool.execute(action="bogus")
    assert isinstance(res, ToolResult)
    assert not res.success
    assert res.error is not None
    assert "bogus" in res.error


async def test_missing_items_structured() -> None:
    tool = TodoTool(store=PlanStore())
    res = await tool.execute(action="add")
    assert not res.success
    assert res.error is not None
    assert "items" in res.error


async def test_list_action_reads_without_mutation() -> None:
    store = PlanStore()
    tool = TodoTool(store=store)
    await tool.execute(action="add", items=[{"id": "1", "content": "x", "status": "pending"}])
    res = await tool.execute(action="list")
    assert res.success
    assert "x" in res.output
    assert len(store.read()) == 1


async def test_single_in_progress_auto_correct_via_tool() -> None:
    store = PlanStore()
    tool = TodoTool(store=store)
    res = await tool.execute(
        action="replace",
        items=[
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "in_progress"},
        ],
    )
    # Not an error — auto-corrected.
    assert res.success
    in_progress = [i.id for i in store.read() if i.status == "in_progress"]
    assert in_progress == ["1"]


async def test_manifest_severity_and_group() -> None:
    m = TodoTool().manifest
    assert m.action_severity == "read"
    assert m.toolset_group == "planning"
    assert m.name == "todo"


async def test_bad_items_type_self_heals() -> None:
    tool = TodoTool(store=PlanStore())
    # 'items' is not a list -> coerced to empty -> structured "requires items".
    res = await tool.execute(action="add", items="not-a-list")
    assert not res.success
    assert res.error is not None
