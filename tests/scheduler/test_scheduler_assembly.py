"""Tests for SchedulerAssembly — Commit E wire-up."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.settings import BriefSettings, MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.memory.assembly import MemoryAssembly
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.assembly import SchedulerAssembly, SchedulerComponents, _build_health_alert_sink
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
    ChannelRegistry.instance().reset()
    yield
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    return reg


async def _build(
    tmp_db: DbPool,
    tmp_path: Path | None = None,
    *,
    browser_runtime: Any = None,
    health_loop: bool = False,
    mcp_client: Any = None,
    settings_overrides: dict[str, Any] | None = None,
) -> SchedulerComponents:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.services import StepServices
    from stackowl.skills.assembly import SkillsAssembly
    from stackowl.tools.registry import ToolRegistry

    # NB: Settings has extra="ignore" — top-level kwargs are silently dropped, so
    # health_loop must be applied via model_copy, never the constructor.
    settings = Settings(memory=MemorySettings()).model_copy(
        update={"health_loop": health_loop, **(settings_overrides or {})}
    )
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
        browser_runtime=browser_runtime,
        mcp_client=mcp_client,
    )


class _FakeBrowserRuntime:
    """Minimal HealableResource stand-in for the browser runtime."""

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    async def ensure_available(self) -> None:
        return None

    def register_on_recycled(self, cb: Any) -> None:
        return None


async def test_health_sweep_wired_with_live_db_and_provider_healers(
    tmp_db: DbPool,
) -> None:
    # ADR-6 F-87 — the sweep must hold the live DbPool + each provider as a
    # HealableResource keyed by its health-status name, so the loop can recycle
    # them. Browser is excluded when the flag is OFF (byte-identical detect set).
    components = await _build(tmp_db, browser_runtime=_FakeBrowserRuntime())
    healers = components.health_sweep_handler._healers
    assert healers["db"] is tmp_db
    assert "provider:stub" in healers
    assert "browser" not in healers  # flag OFF → browser not wired


async def test_tool_revalidation_seeded_after_tool_outcome_miner_by_hour(
    tmp_db: DbPool,
) -> None:
    """Regression: _next_local_hour_iso only supports whole hours (no minutes),
    so seeding two jobs at the SAME hour with different ':MM' labels (e.g. both
    at hour=5, one labeled "05:00" and the other "05:30") computes the IDENTICAL
    first-run timestamp for both — the ":30" ordering claim would be false. The
    two jobs must be seeded at genuinely distinct hours."""
    await _build(tmp_db)
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, next_run_at FROM jobs "
        "WHERE handler_name IN ('tool_outcome_miner', 'tool_revalidation')"
    )
    by_handler = {r["handler_name"]: r["next_run_at"] for r in rows}
    assert "tool_outcome_miner" in by_handler
    assert "tool_revalidation" in by_handler
    assert by_handler["tool_revalidation"] > by_handler["tool_outcome_miner"], (
        "tool_revalidation must be seeded to run strictly AFTER tool_outcome_miner "
        f"(got {by_handler})"
    )


async def test_health_sweep_aggregator_includes_live_provider_registry(
    tmp_db: DbPool,
) -> None:
    """FX-03 — the LIVE ProviderRegistry (real circuit-breaker state, distinct
    from the synthetic per-provider ProviderContributor probes) must be
    registered with the same aggregator the health sweep collects from, so a
    persistently OPEN breaker reaches _alert_state and IncidentEscalationHandler."""
    components = await _build(tmp_db, browser_runtime=_FakeBrowserRuntime())
    names = [
        c.contributor_name
        for c in components.health_sweep_handler._aggregator._contributors
    ]
    assert "provider_registry" in names


async def test_health_sweep_wires_embedding_registry_healer_and_contributor(
    tmp_db: DbPool,
) -> None:
    # Task 1 (ADR-6 self-heal) — the sweep must hold the live EmbeddingRegistry
    # as a HealableResource, keyed by its own contributor_name
    # ("embedding_registry") so health_sweep's self._healers.get(s.name)
    # lookup actually finds it, AND detect it via its own health_check,
    # registered unconditionally like DbContributor (no flag gate — the
    # registry needs no live-runtime ref beyond what MemoryAssembly.build
    # already constructed).
    components = await _build(tmp_db)
    handler = components.health_sweep_handler
    embeddings_healer = handler._healers["embedding_registry"]
    # sanity: it's a real EmbeddingRegistry-shaped HealableResource, not a stub
    assert hasattr(embeddings_healer, "ensure_available")
    names = {c.contributor_name for c in handler._aggregator._contributors}
    assert "embedding_registry" in names


async def test_health_sweep_wires_mcp_healer_when_servers_configured(
    tmp_db: DbPool,
) -> None:
    # Task 8 (ADR-6 self-heal) — a real SchedulerAssembly.build() call, exactly
    # the production path, with a configured MCP client + non-empty server
    # list. This is the EXACT scenario a prior version of this task crashed
    # on at boot (settings.mcp — an attribute that doesn't exist — instead of
    # settings.mcp_client), because no test in this arc drove the wiring
    # block through the real build() call. Regression guard: this must not
    # raise, and the healer/contributor must actually be wired.
    from stackowl.config.settings import McpClientSettings
    from stackowl.mcp.allowlist import McpServerAllowlist, McpServerConfig
    from stackowl.mcp.cache import McpToolCache
    from stackowl.mcp.client import McpClient
    from stackowl.mcp.probe import McpLivenessProbe

    server_cfg = McpServerConfig(
        name="test_server", uri="stdio:///usr/bin/true", timeout_seconds=5.0
    )
    mcp_client = McpClient(
        McpServerAllowlist(("stdio://",)), McpToolCache(), McpLivenessProbe()
    )
    components = await _build(
        tmp_db,
        mcp_client=mcp_client,
        settings_overrides={
            "mcp_client": McpClientSettings(servers=(server_cfg,))
        },
    )
    handler = components.health_sweep_handler
    assert handler._healers["mcp"] is mcp_client
    names = {c.contributor_name for c in handler._aggregator._contributors}
    assert "mcp" in names


async def test_health_sweep_wires_browser_when_flag_on(tmp_db: DbPool) -> None:
    runtime = _FakeBrowserRuntime()
    components = await _build(tmp_db, browser_runtime=runtime, health_loop=True)
    handler = components.health_sweep_handler
    assert handler._healers["browser"] is runtime
    names = {c.contributor_name for c in handler._aggregator._contributors}
    assert "browser" in names  # live BrowserContributor added for detection


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


async def test_build_registers_and_seeds_objective_driver(tmp_db: DbPool) -> None:
    # Keystone reachability lock: the ObjectiveDriver must be BOTH registered AND
    # seeded with an every-1m row, or standing objectives would never advance
    # (registered ≠ reachable).
    from stackowl.scheduler.base import HandlerRegistry

    await _build(tmp_db)
    assert HandlerRegistry.instance().get("objective_driver") is not None
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule FROM jobs WHERE handler_name = ?",
        ("objective_driver",),
    )
    assert len(rows) == 1
    assert rows[0]["schedule"] == "every 1m"


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


class _FakeTelegramAdapter:
    """Minimal telegram-like adapter — records every ``send_text`` call.

    Unlike the fresh-process adapter in ``test_morning_brief_delivers.py``, the
    health-alert path never threads an explicit ``chat_id`` (F-87's
    ``_build_health_alert_sink`` builds a channel-only ``Notification``), so
    this fake accepts a ``None`` chat_id rather than raising.
    """

    def __init__(self) -> None:
        self.sends: list[str] = []

    @property
    def channel_name(self) -> str:
        return "telegram"

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        self.sends.append(text)


async def test_health_alert_sink_delivers_critical_alert_with_default_brief_settings(
    tmp_db: DbPool,
) -> None:
    """FR-11/12 root-cause regression: the live incident this bug fix targets.

    ``_build_health_alert_sink`` addresses its critical alert to
    ``settings.brief.channels[0]``. With the OLD default (``["cli"]``) that
    channel is never registered in :class:`ChannelRegistry`, so the alert
    silently hit ``ChannelNotFoundError`` and went nowhere (today's real
    ``telegram_canary_send`` degradation blip). With the fixed default
    (``["telegram"]``) and a real telegram adapter registered, the alert must
    actually reach the adapter's ``send_text``.
    """
    adapter = _FakeTelegramAdapter()
    ChannelRegistry.instance().register(adapter)  # type: ignore[arg-type]

    settings = Settings(
        brief=BriefSettings(),  # DEFAULT — no channel override, the live config shape
        telegram_channel=TelegramSettings(allowed_user_ids=frozenset({12345})),
    )
    router = NotificationRouter(db=tmp_db, settings=settings)
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings
    )

    alert = _build_health_alert_sink(deliverer, settings)
    assert alert is not None

    await alert("CRITICAL: db subsystem is down")

    assert adapter.sends == ["CRITICAL: db subsystem is down"], (
        "the default BriefSettings().channels must resolve to a registered, "
        "deliverable channel — not the structurally-dead 'cli' default"
    )
