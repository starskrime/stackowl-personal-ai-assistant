"""Tests for MemoryAssembly factory — Commit A wire-up of the consolidation pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.memory.assembly import MemoryAssembly, MemoryComponents
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ModelRoute, ProviderRegistry
from stackowl.scheduler.base import HandlerRegistry

pytestmark = pytest.mark.asyncio


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


async def test_build_wires_all_eleven_components(tmp_db: DbPool) -> None:
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
    assert components.fact_extractor is not None
    assert components.fact_extraction_handler is not None


async def test_build_registers_dream_worker_with_scheduler(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    handler = HandlerRegistry.instance().get("dream_worker")
    assert handler is not None
    assert handler.handler_name == "dream_worker"


async def test_build_registers_fact_extraction_handler(tmp_db: DbPool) -> None:
    settings = Settings(memory=MemorySettings())
    await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )
    handler = HandlerRegistry.instance().get("fact_extraction")
    assert handler is not None
    assert handler.handler_name == "fact_extraction"


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
    """Fact extractor and entity extractor must resolve 'standard', not 'powerful'.

    This is a hybrid-routing cost guard: running these helpers on the 122b (powerful)
    model is expensive; standard is capable enough for extraction tasks.
    """
    spy = _SpyProviderRegistry()
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=spy,
    )

    # Fact extractor: get_with_cascade must have been called with "standard".
    assert "standard" in spy.cascade_tiers, (
        f"Expected 'standard' in cascade tier calls; got {spy.cascade_tiers!r}"
    )
    assert "powerful" not in spy.cascade_tiers, (
        f"No extractor should request 'powerful'; got {spy.cascade_tiers!r}"
    )

    # Entity extractor: _preferred_tier must be "standard" at the live construction site.
    assert components.entity_extractor._preferred_tier == "standard", (  # type: ignore[union-attr]
        f"EntityExtractor._preferred_tier is {components.entity_extractor._preferred_tier!r}, expected 'standard'"  # type: ignore[union-attr]
    )


async def test_fact_extractor_receives_the_cascade_resolved_model(tmp_db: DbPool) -> None:
    """Task 16 — assembly.py must resolve (provider, model) via
    get_with_cascade("standard") and thread the model into
    FactExtractor(model=...), not just the provider.

    Genuinely discriminating: if assembly.py kept calling get_with_cascade()
    (provider only, dropping the model), fact_extractor._model would stay ""
    even though the registry's standard-tier route carries a distinct model.
    """
    registry = ProviderRegistry()
    registry.register_mock("stub", _StubProvider(), tier="powerful")
    registry.register_mock(
        "stub-std", _StubProvider(),
        models=(ModelRoute(model="assembly-fact-extractor-model", tiers=("standard",)),),
    )
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=registry,
    )
    assert components.fact_extractor._model == "assembly-fact-extractor-model", (  # type: ignore[union-attr]
        f"fact_extractor._model is {components.fact_extractor._model!r}, "  # type: ignore[union-attr]
        f"expected 'assembly-fact-extractor-model'"
    )
