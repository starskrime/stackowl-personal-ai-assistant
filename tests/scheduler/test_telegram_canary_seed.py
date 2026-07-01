"""PB-CANARY — the seed + registration wiring for ``telegram_canary``.

Mirrors ``test_check_in_seed.py``'s pattern: drives the REAL seed path through
``SchedulerAssembly.build`` (not a hand-seeded row) and asserts:

* a single resolvable telegram owner -> a deliverable ``telegram_canary`` row
  with durable ``target_channels``/``target_addresses`` populated, seeded
  exactly once (idempotent on rebuild).
* no resolvable owner -> NO row (never a permanently-undeliverable seed).
* the handler registers AND (when telegram is configured) the second
  ``ChannelLivenessContributor`` (``telegram_canary_send``) registers on the
  health aggregator, sharing the SAME store PB0b's receive contributor uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.channels.telegram.settings import TelegramSettings
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


async def _build(
    tmp_db: DbPool, settings: Settings, tmp_path: Path
) -> SchedulerComponents:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.services import StepServices
    from stackowl.skills.assembly import SkillsAssembly
    from stackowl.tools.registry import ToolRegistry

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


def _settings(*, bot_token: str, allowed_user_ids: frozenset[int]) -> Settings:
    base = Settings(memory=MemorySettings())
    return base.model_copy(
        update={
            "telegram_channel": TelegramSettings(
                bot_token=bot_token, allowed_user_ids=allowed_user_ids
            ),
        }
    )


async def test_telegram_canary_handler_always_registers(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """The handler registers regardless of telegram config (register != reachable
    is the antipattern this arc exists to kill — but a job that can never
    deliver is never SEEDED, covered below)."""
    settings = _settings(bot_token="", allowed_user_ids=frozenset())
    await _build(tmp_db, settings, tmp_path)
    assert HandlerRegistry.instance().get("telegram_canary") is not None


async def test_telegram_canary_seeded_when_resolvable(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    settings = _settings(bot_token="dummy-token", allowed_user_ids=frozenset({777}))
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule, target_channels, target_addresses "
        "FROM jobs WHERE handler_name = ?",
        ("telegram_canary",),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["schedule"] == "every 20m"
    assert "telegram" in (row["target_channels"] or "")
    assert "777" in (row["target_addresses"] or "")


async def test_telegram_canary_seed_is_idempotent(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    settings = _settings(bot_token="dummy-token", allowed_user_ids=frozenset({777}))
    await _build(tmp_db, settings, tmp_path)
    HandlerRegistry.reset()
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("telegram_canary",),
    )
    assert len(rows) == 1  # second build did not duplicate


async def test_telegram_canary_not_seeded_without_resolvable_owner(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """No single resolvable telegram owner -> NO dead row (honesty: never a
    permanently-undeliverable seed, same rule check_in already follows)."""
    settings = _settings(bot_token="dummy-token", allowed_user_ids=frozenset())
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("telegram_canary",),
    )
    assert rows == []


async def test_second_liveness_contributor_registers_when_telegram_configured(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    settings = _settings(bot_token="dummy-token", allowed_user_ids=frozenset({777}))
    components = await _build(tmp_db, settings, tmp_path)
    names = {
        c.contributor_name
        for c in components.health_sweep_handler._aggregator._contributors
    }
    assert "telegram_receive" in names
    assert "telegram_canary_send" in names


async def test_no_liveness_contributors_when_telegram_not_configured(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    settings = _settings(bot_token="", allowed_user_ids=frozenset())
    components = await _build(tmp_db, settings, tmp_path)
    names = {
        c.contributor_name
        for c in components.health_sweep_handler._aggregator._contributors
    }
    assert "telegram_receive" not in names
    assert "telegram_canary_send" not in names
