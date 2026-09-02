"""Tests for MemoryAssembly factory — Commit A wire-up of the consolidation pipeline."""

from __future__ import annotations

import dataclasses
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
    # Frozen dataclass — mutation raises. Tightened from a blind `Exception`,
    # which would also have passed if the assignment failed for some unrelated
    # reason and so proved nothing about frozenness.
    with pytest.raises(dataclasses.FrozenInstanceError):
        components.bridge = None  # type: ignore[misc]


async def test_build_wires_every_advertised_component(tmp_db: DbPool) -> None:
    """Every field MemoryComponents advertises is actually wired.

    Asserted against the DATACLASS rather than a hand-listed set, so the test
    cannot go stale the way it just did: it named promoter, pruner and detector,
    all three removed across D08.2 seam 3 passes 2-4, and had been failing since
    pass 2 without anyone noticing. A hand-maintained list of fields is a second
    copy of the dataclass; this asks the dataclass instead.

    `kuzu_adapter` is the one legitimate None (DUR-5/F069 — it degrades rather
    than crashing when Kuzu is unavailable), so it is exempted by name.
    """
    settings = Settings(memory=MemorySettings())
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
    )

    may_be_none = {"kuzu_adapter"}
    unwired = [
        f.name
        for f in dataclasses.fields(components)
        if f.name not in may_be_none and getattr(components, f.name) is None
    ]
    assert not unwired, f"MemoryComponents advertises fields that build() left None: {unwired}"
    assert dataclasses.fields(components), "MemoryComponents must advertise something"




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


# test_fact_extractor_receives_the_cascade_resolved_model REMOVED with the
# extractor (D08.1). It guarded that assembly threaded the cascade-resolved MODEL
# into FactExtractor rather than only the provider — a real bug once, and now a
# guard on a constructor that no longer exists.
