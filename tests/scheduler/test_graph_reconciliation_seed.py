"""SchedulerAssembly seeds the ``graph_reconciliation`` sweep job.

The ``graph_reconciliation`` handler exists and reconciles the Kuzu graph
against SQLite/LanceDB source-of-truth state, but it only runs if a ``jobs``
row seeds it. Mirrors ``test_digest_seed.py`` (F-77, ``notification_digest``)
to prove the same claim for ``graph_reconciliation``: registered is not the
same as reachable, so this asserts the actual ``jobs`` table row exists after
``SchedulerAssembly.build(...)`` runs, and that a second boot does not
duplicate it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.assembly import MemoryAssembly
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.assembly import SchedulerAssembly, SchedulerComponents
from stackowl.scheduler.base import HandlerRegistry

pytestmark = pytest.mark.asyncio


class _StubProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:  # noqa: ARG002
        return CompletionResult(
            content="", input_tokens=0, output_tokens=0,
            model="stub", provider_name="stub", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:  # noqa: ARG002
        if False:  # pragma: no cover
            yield ""
        return


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    return reg


async def _build(tmp_db: DbPool, tmp_path: Path) -> SchedulerComponents:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.services import StepServices
    from stackowl.skills.assembly import SkillsAssembly
    from stackowl.tools.registry import ToolRegistry

    settings = Settings(memory=MemorySettings())
    provider_registry = _registry()
    memory_components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=provider_registry,
    )
    owl_registry = OwlRegistry()
    backend = AsyncioBackend(services=StepServices())
    skills_root = tmp_path / "skills_ws"
    skills_root.mkdir(parents=True, exist_ok=True)
    skills_components = await SkillsAssembly.build(
        db=tmp_db,
        tool_registry=ToolRegistry(),
        owl_registry=owl_registry,
        skills_root=skills_root,
        builtin_seed_dir=None,
    )
    return await SchedulerAssembly.build(
        db=tmp_db,
        settings=settings,
        event_bus=EventBus(),
        provider_registry=provider_registry,
        owl_registry=owl_registry,
        memory_components=memory_components,
        backend=backend,
        skills_components=skills_components,
    )


async def test_graph_reconciliation_job_seeded_by_scheduler_assembly(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A ``graph_reconciliation`` jobs row is seeded so the sweep actually fires."""
    await _build(tmp_db, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule, next_run_at FROM jobs WHERE handler_name = ?",
        ("graph_reconciliation",),
    )
    assert len(rows) == 1, "exactly one graph_reconciliation row must be seeded"
    assert rows[0]["schedule"].startswith("every")
    assert rows[0]["next_run_at"] is not None


async def test_graph_reconciliation_seed_is_idempotent(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A second build must not duplicate the seeded graph_reconciliation row."""
    await _build(tmp_db, tmp_path)
    await _build(tmp_db, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?",
        ("graph_reconciliation",),
    )
    assert len(rows) == 1, "graph_reconciliation seed must be idempotent by handler_name"
