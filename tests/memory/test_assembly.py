"""Tests for MemoryAssembly factory — Commit A wire-up of the consolidation pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.memory.assembly import MemoryAssembly, MemoryComponents
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.base import HandlerRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point StackowlHome at a temp dir for every test in this module.

    MemoryAssembly.build opens the knowledge graph at StackowlHome.kuzu_dir().
    Without this, these tests opened the operator's REAL ~/.stackowl graph — and
    Kuzu takes an exclusive file lock, so the build failed with "Could not set
    lock on file" and degraded kuzu_adapter to None WHENEVER THE PLATFORM WAS
    RUNNING. Since the standing rule here is to restart and leave StackOwl
    running after every fix, that meant this test was red essentially always,
    for a reason that had nothing to do with assembly wiring.

    A test that reads live state is not a slow test, it is a wrong one.
    """
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "stackowl-home"))


class _StubProvider(ModelProvider):
    """Cheapest possible ModelProvider for assembly tests — never called."""

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


def _stub_provider_registry() -> ProviderRegistry:
    """Return a ProviderRegistry populated with one stub provider on all tiers."""
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    reg.register_mock("stub-std", _StubProvider(), tier="standard")
    return reg


class _SpyProviderRegistry(ProviderRegistry):
    """ProviderRegistry that records every tier passed to get_with_cascade.

    Task 16 — assembly.py's fact_extractor/entity_extractor wiring now calls
    get_with_cascade() (not get_with_cascade()) to also thread the
    resolved model, so the spy overrides that method instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cascade_tiers: list[str] = []
        self.register_mock("stub", _StubProvider(), tier="powerful")
        self.register_mock("stub-std", _StubProvider(), tier="standard")
        self.register_mock("stub-fast", _StubProvider(), tier="fast")

    def get_with_cascade(self, preferred_tier: str) -> Any:  # type: ignore[override]
        self.cascade_tiers.append(preferred_tier)
        return super().get_with_cascade(preferred_tier)


@pytest.fixture(autouse=True)
def _reset_handler_registry() -> Any:
    """Each test gets a fresh HandlerRegistry — registrations don't leak across tests."""
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


async def test_build_returns_frozen_components(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    assert isinstance(components, MemoryComponents)
    # Frozen dataclass — mutation raises.
    with pytest.raises(Exception):
        components.bridge = None  # type: ignore[misc]


async def test_build_wires_all_ten_components(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    # Every advertised attribute is non-None.
    assert components.bridge is not None
    assert components.preference_store is not None
    assert components.kuzu_adapter is not None
    assert components.promoter is not None
    assert components.pruner is not None
    assert components.detector is not None
    assert components.entity_extractor is not None
    assert components.kuzu_sync_handler is not None
    assert components.dream_worker is not None
    # fact_extractor was the eleventh. It went with the extraction pipeline
    # (D08.1) — 88,631 facts, 37.1% of them the platform's own telemetry.
    assert components.rollover_summary_handler is not None


async def test_build_registers_dream_worker_with_scheduler(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    handler = HandlerRegistry.instance().get("dream_worker")
    assert handler is not None
    assert handler.handler_name == "dream_worker"


async def test_build_registers_rollover_summary_handler(tmp_db: DbPool) -> None:
    """Was test_build_registers_fact_extraction_handler.

    The handler it pinned was deleted in D01.7 part 5b: it duplicated
    conversation_miner and nothing had ever enqueued it, so this test asserted
    that an unreachable component was reachable. Re-pointed at the handler that
    replaced it — which IS enqueued, by the session.rollover consumer.
    """
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    handler = HandlerRegistry.instance().get("rollover_summary")
    assert handler is not None
    assert handler.handler_name == "rollover_summary"


async def test_build_seeds_dream_worker_schedule(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    rows = await tmp_db.fetch_all(
        "SELECT handler_name, schedule FROM jobs WHERE handler_name = ?",
        ("dream_worker",),
    )
    assert len(rows) == 1
    # Cadence is config-driven (MemorySettings.dream_worker_interval_minutes,
    # default 30) — the legacy daily@03:00 literal is gone.
    assert rows[0]["schedule"] == "every 30m"


async def test_build_is_idempotent_on_schedule_seed(tmp_db: DbPool) -> None:
    """A second build call must not duplicate the seeded dream_worker job row."""
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    HandlerRegistry.reset()  # second call would otherwise re-register
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    rows = await tmp_db.fetch_all(
        "SELECT job_id FROM jobs WHERE handler_name = ?",
        ("dream_worker",),
    )
    assert len(rows) == 1  # NOT 2


async def test_build_bridge_uses_db_pool(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    # Bridge can persist + recall — basic round-trip via existing methods.
    await components.bridge.store("User: hi\n\nAssistant: hello", "sess-asm")
    turns = await components.bridge.recent_conversation_turns("sess-asm", limit=5)
    assert len(turns) == 1


async def test_build_kuzu_degrades_to_none_if_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    """DUR-5 / F069 — if KuzuAdapter raises, assembly DEGRADES to a None adapter
    (consistent with LanceDB/embeddings policy) with a LOUD ERROR + 'down' graph
    health, rather than aborting startup (the prior hard-fail policy)."""
    import logging

    from stackowl.memory import kuzu_adapter as kuzu_mod

    class _BoomKuzu:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated kuzu native lib missing")

    monkeypatch.setattr(kuzu_mod, "KuzuAdapter", _BoomKuzu)
    settings = Settings(memory=MemorySettings())
    with caplog.at_level(logging.ERROR):
        components = await MemoryAssembly.build(
            db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
        )
    assert components.kuzu_adapter is None
    assert (await components.graph_health.health_check()).status == "down"
    assert any(
        r.levelno >= logging.ERROR and "kuzu" in r.getMessage().lower()
        for r in caplog.records
    )


async def test_extractors_use_standard_tier(tmp_db: DbPool) -> None:
    """The entity extractor must resolve 'standard', not 'powerful'.

    A hybrid-routing COST guard: running these helpers on the 122b (powerful)
    model is expensive and standard is capable enough. The fact-extractor half of
    this guard went with the extractor itself (D08.1); the guard still matters
    for what remains, so it is narrowed rather than deleted.
    """
    spy = _SpyProviderRegistry()
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=spy,
    )

    assert "powerful" not in spy.cascade_tiers, (
        f"No extractor should request 'powerful'; got {spy.cascade_tiers!r}"
    )

    # Entity extractor: _preferred_tier must be "standard" at the live construction site.
    assert components.entity_extractor._preferred_tier == "standard", (  # type: ignore[union-attr]
        f"EntityExtractor._preferred_tier is {components.entity_extractor._preferred_tier!r}, expected 'standard'"  # type: ignore[union-attr]
    )
# test_fact_extractor_receives_the_cascade_resolved_model REMOVED with the
# extractor (D08.1). It guarded that assembly threaded the cascade-resolved MODEL
# into FactExtractor rather than only the provider — a real bug once, and now a
# guard on a constructor that no longer exists.
