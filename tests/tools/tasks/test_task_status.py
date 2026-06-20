"""Unit tests for TaskStatusTool — owner-scoped read-only durable task lookup."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Iterator

import pytest

from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import ToolManifest

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool


async def _seed_task(
    db: DbPool,
    *,
    task_id: str,
    status: str,
    goal: str,
    current_step: int = 2,
) -> None:
    now = datetime.now(tz=UTC)
    task = DurableTask(
        task_id=task_id,
        owner_id=DEFAULT_PRINCIPAL_ID,
        goal=goal,
        status=status,  # type: ignore[arg-type]
        current_step=current_step,
        created_at=now,
        updated_at=now,
    )
    await DurableTaskStore(db, DEFAULT_PRINCIPAL_ID).create(task)


@pytest.fixture()
def task_env(tmp_db: DbPool) -> Iterator[DbPool]:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        yield tmp_db
    finally:
        reset_services(token)


@pytest.fixture()
def no_db_env() -> Iterator[None]:
    token = set_services(StepServices(db_pool=None))
    try:
        yield
    finally:
        reset_services(token)


def test_manifest_is_read_tasks() -> None:
    from stackowl.tools.tasks.task_status import TaskStatusTool

    m = TaskStatusTool().manifest
    assert isinstance(m, ToolManifest)
    assert m.action_severity == "read"
    assert m.toolset_group == "tasks"


async def test_returns_status_for_known_task(task_env: DbPool) -> None:
    from stackowl.tools.tasks.task_status import TaskStatusTool

    await _seed_task(task_env, task_id="t1", status="running", goal="deploy app")
    res = await TaskStatusTool().execute(task_id="t1")
    assert res.success is True
    assert "running" in res.output and "t1" in res.output
    # A pure read must never report a committed side effect.
    assert res.side_effect_committed is False


async def test_unknown_task_is_honest_not_found(task_env: DbPool) -> None:
    from stackowl.tools.tasks.task_status import TaskStatusTool

    res = await TaskStatusTool().execute(task_id="nope")
    assert res.success is False
    assert res.error is not None and "not found" in res.error.lower()
    assert res.output == ""


async def test_missing_db_degrades_structured(no_db_env: None) -> None:
    from stackowl.tools.tasks.task_status import TaskStatusTool

    res = await TaskStatusTool().execute(task_id="t1")
    assert res.success is False
    assert res.error is not None and "unavailable" in res.error.lower()


async def test_missing_task_id_is_rejected(task_env: DbPool) -> None:
    from stackowl.tools.tasks.task_status import TaskStatusTool

    res = await TaskStatusTool().execute()
    assert res.success is False


def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry
    from stackowl.tools.tasks.task_status import TaskStatusTool

    tool = ToolRegistry.with_defaults().get("task_status")
    assert isinstance(tool, TaskStatusTool)
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "tasks"
