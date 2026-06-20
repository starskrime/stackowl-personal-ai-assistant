"""WS-C — the PRODUCER path for the ``check_in`` proactive job.

``CheckInHandler`` is fully built, registered, and delivers honestly via
``ProactiveJobDeliverer`` — but until WS-C there was NO ``_seed_*`` call for it
in ``scheduler/assembly.py``, so no ``jobs`` row was ever created and the
scheduler never dispatched it. A user promised periodic check-ins got none.

The existing journey ``test_check_in_delivers.py`` hand-seeds the job, masking
this producer gap. These tests drive the REAL seed path through
``SchedulerAssembly.build`` and assert:

* enabled + a single resolvable telegram owner → a deliverable ``check_in`` row
  with durable ``target_channels`` / ``target_addresses`` populated.
* disabled → NO ``check_in`` row.
* enabled but no resolvable owner (0 or >1 allowed users) → NO dead row + a
  warning logged (honesty: never seed an undeliverable row).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.settings import CheckInSettings, MemorySettings, Settings
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
    # Isolate the skills workspace so a stray learned skill in the real home
    # (~/.stackowl/workspace/skills) can't break the assembly under test.
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


def _settings(
    *, enabled: bool, allowed_user_ids: frozenset[int],
    schedule: str = "daily@18:00",
) -> Settings:
    """Build test settings with an explicit check_in toggle + telegram allowlist.

    NOTE (WS-A landmine): ``Settings(...)`` loads the real env/YAML config, whose
    ``_YamlSource`` outranks init kwargs — so passing sub-models to the
    constructor gets clobbered for any key present in ~/.stackowl/stackowl.yaml
    (e.g. ``telegram_channel``). Force the sub-models under test via
    ``model_copy(update=...)`` AFTER construction so the env can't override them.
    """
    base = Settings(memory=MemorySettings())
    return base.model_copy(
        update={
            "check_in": CheckInSettings(
                enabled=enabled, channels=["telegram"], schedule=schedule
            ),
            "telegram_channel": TelegramSettings(allowed_user_ids=allowed_user_ids),
        }
    )


async def test_check_in_seeded_when_enabled_with_resolvable_owner(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """enabled + single telegram owner → a deliverable check_in row is seeded."""
    settings = _settings(enabled=True, allowed_user_ids=frozenset({777}))
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule, target_channels, target_addresses "
        "FROM jobs WHERE handler_name = ?",
        ("check_in",),
    )
    assert len(rows) == 1, "exactly one check_in row must be seeded"
    row = rows[0]
    assert row["schedule"] == "daily@18:00"
    # Durable recipient stamped on the row (round-trips via row_to_job).
    assert "telegram" in (row["target_channels"] or "")
    assert "777" in (row["target_addresses"] or "")


@pytest.mark.parametrize("bad", ["every 5m", "daily@25:00", "daily@9", "daily@", "18:00"])
def test_check_in_schedule_rejects_malformed_values(bad: str) -> None:
    """A malformed schedule fails loud at config-load, never silently at seed time
    (and never an out-of-range hour that would crash assembly)."""
    with pytest.raises(ValueError):
        CheckInSettings(schedule=bad)


async def test_check_in_first_run_hour_follows_configured_schedule(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A custom daily@HH:MM schedule drives BOTH the stored schedule and the
    first-run hour — they can never diverge from a hardcoded next_hour."""
    from datetime import datetime

    settings = _settings(
        enabled=True, allowed_user_ids=frozenset({777}), schedule="daily@09:00",
    )
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT schedule, next_run_at FROM jobs WHERE handler_name = ?",
        ("check_in",),
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "daily@09:00"
    # next_run_at is an ISO UTC instant for the next local 09:00 — assert the
    # local wall-clock hour it maps back to is 9, not the old hardcoded 18.
    next_run = datetime.fromisoformat(rows[0]["next_run_at"]).astimezone()
    assert next_run.hour == 9


async def test_check_in_not_seeded_when_disabled(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """disabled → NO check_in row, even with a resolvable owner."""
    settings = _settings(enabled=False, allowed_user_ids=frozenset({777}))
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("check_in",),
    )
    assert rows == [], "no check_in row when the feature is disabled"


async def test_check_in_not_seeded_without_resolvable_owner(
    tmp_db: DbPool, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """enabled but no single resolvable owner → NO dead row + a warning logged.

    Honesty: a target-less row would be a permanent undeliverable no-op, so we
    seed nothing and warn loudly.
    """
    settings = _settings(enabled=True, allowed_user_ids=frozenset())
    with caplog.at_level(logging.WARNING):
        await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("check_in",),
    )
    assert rows == [], "no dead check_in row when the recipient is unresolved"
    assert any(
        "check_in" in rec.getMessage() and "no resolvable recipient" in rec.getMessage()
        for rec in caplog.records
    ), "a warning must explain check_in is enabled but unschedulable"


async def test_check_in_not_seeded_with_multiple_allowed_users(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """>1 allowed user has no single proactive recipient → NO row seeded."""
    settings = _settings(enabled=True, allowed_user_ids=frozenset({1, 2}))
    await _build(tmp_db, settings, tmp_path)

    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?", ("check_in",),
    )
    assert rows == [], "no row when there is no unambiguous single owner"
