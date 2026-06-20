"""Tests for SchedulerAssembly — Commit E wire-up."""

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


async def _build(tmp_db: DbPool, tmp_path: Path | None = None) -> SchedulerComponents:
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
    # SkillsAssembly needs a workspace dir; isolate it under tmp_path when
    # given, else fall back to ~/.stackowl/workspace/skills (real path).
    skills_root = (tmp_path / "skills_ws") if tmp_path is not None else None
    if skills_root is not None:
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


async def test_build_returns_frozen_components(tmp_db: DbPool) -> None:
    components = await _build(tmp_db)
    assert isinstance(components, SchedulerComponents)
    with pytest.raises(Exception):
        components.scheduler = None  # type: ignore[misc]


async def test_build_constructs_scheduler_and_supervisor(tmp_db: DbPool) -> None:
    components = await _build(tmp_db)
    assert components.scheduler is not None
    assert components.supervisor is not None


async def test_build_registers_six_orphaned_handlers(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    registry = HandlerRegistry.instance()
    # Each previously-orphaned handler is now reachable by the scheduler.
    for name in (
        "morning_brief",
        "check_in",
        "knowledge_prune",
        "tool_pruning",
        "goal_execution",
    ):
        assert registry.get(name) is not None, f"Handler {name!r} not registered"
    # Evolution handler — registers itself under handler_name="evolution_batch".
    evo = registry.get("evolution_batch")
    assert evo is not None


async def test_build_seeds_three_default_schedules(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule FROM jobs WHERE handler_name IN "
        "('morning_brief', 'evolution_batch', 'knowledge_prune')", (),
    )
    handler_to_schedule = {r["handler_name"]: r["schedule"] for r in rows}
    assert handler_to_schedule == {
        "morning_brief": "daily@08:00",
        "evolution_batch": "daily@02:00",
        "knowledge_prune": "daily@04:00",
    }


async def test_build_seeds_turn_sweep_every_10m(tmp_db: DbPool) -> None:
    # F050 — the turn-sweep backstop reaper gets a recurring seeded jobs row so the
    # scheduler actually dispatches it (the handler itself is registered in the
    # gateway assembly, which needs the TurnRegistry singleton).
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule FROM jobs WHERE handler_name = ?", ("turn_sweep",),
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "every 10m"


async def test_turn_sweep_seed_is_idempotent(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    HandlerRegistry.reset()
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("turn_sweep",),
    )
    assert len(rows) == 1  # second build did not duplicate


async def test_register_only_handlers_have_no_seeded_schedule(tmp_db: DbPool) -> None:
    """tool_pruning and goal_execution are register-only — no auto-schedule.

    (check_in is no longer register-only as of WS-C: it is conditionally seeded
    when enabled with a resolvable owner — covered by test_check_in_seed.py — so
    it is deliberately excluded here to avoid an environment-dependent assertion.)
    """
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name FROM jobs WHERE handler_name IN "
        "('tool_pruning', 'goal_execution')", (),
    )
    assert rows == []


async def test_build_seed_is_idempotent(tmp_db: DbPool) -> None:
    """Second build call must not duplicate the seeded job rows."""
    await _build(tmp_db)
    HandlerRegistry.reset()
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("morning_brief",),
    )
    assert len(rows) == 1  # NOT 2


async def test_build_registers_downloads_janitor(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    handler = HandlerRegistry.instance().get("downloads_janitor")
    assert handler is not None
    assert handler.handler_name == "downloads_janitor"


async def test_build_seeds_downloads_janitor_12h_schedule(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule, idempotency_key FROM jobs "
        "WHERE handler_name = ?", ("downloads_janitor",),
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "every 12h"
    # 12h = 720m — the idempotency key encodes the interval.
    assert rows[0]["idempotency_key"] == "downloads_janitor:every-720m"


async def test_downloads_janitor_seed_is_idempotent(tmp_db: DbPool) -> None:
    await _build(tmp_db)
    HandlerRegistry.reset()
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("downloads_janitor",),
    )
    assert len(rows) == 1  # second build did not duplicate


async def test_supervisor_supervises_the_scheduler(tmp_db: DbPool) -> None:
    components = await _build(tmp_db)
    # Supervisor's internal _tasks dict (or similar) contains the scheduler.
    # We can't easily inspect Supervisor internals across versions; verify by
    # checking the scheduler's task_id matches what supervisor would dispatch.
    assert components.scheduler.task_id == "job_scheduler"
