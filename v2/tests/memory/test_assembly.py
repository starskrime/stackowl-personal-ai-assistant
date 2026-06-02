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
    return reg


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


async def test_build_kuzu_hard_fails_if_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool) -> None:
    """If KuzuAdapter raises, assembly propagates — no silent degradation."""
    from stackowl.memory import kuzu_adapter as kuzu_mod

    class _BoomKuzu:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated kuzu native lib missing")

    monkeypatch.setattr(kuzu_mod, "KuzuAdapter", _BoomKuzu)
    settings = Settings(memory=MemorySettings())
    with pytest.raises(RuntimeError, match="simulated kuzu"):
        await MemoryAssembly.build(
            db=tmp_db, settings=settings, provider_registry=_stub_provider_registry(),
        )
