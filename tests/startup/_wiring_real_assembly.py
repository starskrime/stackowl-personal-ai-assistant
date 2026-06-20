"""Shared helper — build the REAL scheduler registry for the wiring-audit regression.

Drives ``SchedulerAssembly.build`` with stub providers so the wiring audit runs
against the ACTUAL set of handlers the assembly registers + seeds (the same path
that masked the check_in producer gap). Mirrors ``tests/scheduler/test_check_in_seed.py``'s
``_build`` so the two stay in step.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.assembly import MemoryAssembly
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import HandlerRegistry


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


async def build_real_scheduler(tmp_db: DbPool, tmp_path: Path) -> HandlerRegistry:
    """Run the real ``SchedulerAssembly.build`` and return the populated registry."""
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.services import StepServices
    from stackowl.scheduler.assembly import SchedulerAssembly
    from stackowl.skills.assembly import SkillsAssembly
    from stackowl.tools.registry import ToolRegistry

    provider_registry = ProviderRegistry()
    provider_registry.register_mock("stub", _StubProvider(), tier="powerful")
    settings = Settings(memory=MemorySettings())

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
    await SchedulerAssembly.build(
        db=tmp_db,
        settings=settings,
        event_bus=EventBus(),
        provider_registry=provider_registry,
        owl_registry=owl_registry,
        memory_components=memory_components,
        backend=backend,
        skills_components=skills_components,
    )
    return HandlerRegistry.instance()
