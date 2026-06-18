"""Recovery resumes roots only + reaps zombie children (Story D1 §9)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import recover_durable_tasks
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.registry import ToolRegistry

_FINAL = "done"


class _Finishing:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "fin"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(self, user_text, system_text, tool_schemas,  # noqa: ANN001
                                  tool_dispatcher, max_iterations=8, history=None,
                                  persistence_check=None, on_iteration_complete=None,
                                  resume_messages=None, resume_tool_calls=None):
        self.calls += 1
        if on_iteration_complete is not None:
            await on_iteration_complete(ReActIterationState(
                iteration=0, messages=[{"role": "assistant", "content": "done"}],
                tool_call_records=[]))
        return _FINAL, []

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(content="secretary", input_tokens=1, output_tokens=1,
                                model="", provider_name=self.name, duration_ms=0.0)

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _Reg:
    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> object:
        return self._p

    def get_with_cascade(self, tier: str) -> object:
        return self._p


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


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _backend(pool: DbPool, provider: object) -> AsyncioBackend:
    services = StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(), stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(), db_pool=pool,
    )
    return AsyncioBackend(services=services)


async def test_children_excluded_from_orphan_recovery(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    # A running ROOT orphan + a running CHILD orphan under a running parent.
    await store.create(DurableTask(task_id="root", owner_id=DEFAULT_PRINCIPAL_ID,
                                   goal="g", status="running", owl_name="secretary",
                                   channel="cli", created_at=now, updated_at=now))
    await store.create_child_task(child_task_id="kid", parent_task_id="root",
                                  parent_owl="secretary", delegate_key="dk",
                                  goal="sub", owl_name="scout", channel="cli")
    recoverer = await recover_durable_tasks(pool, _backend(pool, _Finishing()))
    await recoverer.drain()
    # Only the ROOT was launched — the child is resumed transitively, not directly.
    assert recoverer.launched == 1, (
        f"only roots should be launched, got {recoverer.launched}"
    )


async def test_zombie_child_under_terminal_parent_is_reaped(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    await store.create(DurableTask(task_id="P", owner_id=DEFAULT_PRINCIPAL_ID, goal="g",
                                   status="completed", created_at=now, updated_at=now))
    await store.create_child_task(child_task_id="zombie", parent_task_id="P",
                                  parent_owl="secretary", delegate_key="dk",
                                  goal="sub", owl_name="scout", channel="cli")
    recoverer = await recover_durable_tasks(pool, _backend(pool, _Finishing()))
    await recoverer.drain()
    rec = await store.get("zombie")
    assert rec.status == "failed", f"zombie child not reaped — status={rec.status!r}"


async def test_running_child_under_running_parent_is_not_reaped(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    # Parent still running — its child is reachable by transitive resolution, so
    # the reaper must NOT touch it (only terminal-parent children are zombies).
    await store.create(DurableTask(task_id="root", owner_id=DEFAULT_PRINCIPAL_ID,
                                   goal="g", status="running", owl_name="secretary",
                                   channel="cli", created_at=now, updated_at=now))
    await store.create_child_task(child_task_id="kid", parent_task_id="root",
                                  parent_owl="secretary", delegate_key="dk",
                                  goal="sub", owl_name="scout", channel="cli")
    recoverer = await recover_durable_tasks(pool, _backend(pool, _Finishing()))
    await recoverer.drain()
    rec = await store.get("kid")
    assert rec.status == "running", (
        f"live child wrongly reaped — status={rec.status!r}"
    )
