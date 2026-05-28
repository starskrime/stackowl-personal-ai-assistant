"""Tests for Commit B — embedding registry + LanceDB wired into MemoryAssembly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.config.settings import MemorySettings, Settings
from stackowl.db.pool import DbPool
from stackowl.memory.assembly import MemoryAssembly
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
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


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    return reg


@pytest.fixture(autouse=True)
def _reset_handler_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


async def test_assembly_wires_embedding_registry(tmp_db: DbPool) -> None:
    components = await MemoryAssembly.build(
        db=tmp_db, settings=Settings(memory=MemorySettings()), provider_registry=_registry(),
    )
    assert components.embedding_registry is not None
    # Even if sentence-transformer fails, hash provider is the fallback —
    # the registry is always non-None.
    assert components.embedding_registry.get() is not None


async def test_assembly_wires_lancedb_adapter(tmp_db: DbPool) -> None:
    components = await MemoryAssembly.build(
        db=tmp_db, settings=Settings(memory=MemorySettings()), provider_registry=_registry(),
    )
    assert components.lancedb is not None
    # Sanity — the adapter knows its data dir.
    assert hasattr(components.lancedb, "_data_dir")


async def test_bridge_receives_embeddings_and_lancedb(tmp_db: DbPool) -> None:
    components = await MemoryAssembly.build(
        db=tmp_db, settings=Settings(memory=MemorySettings()), provider_registry=_registry(),
    )
    # Bridge internals expose the wired adapters — used by recall().
    assert components.bridge._embeddings is components.embedding_registry
    assert components.bridge._lancedb is components.lancedb
    assert components.bridge._semantic_enabled is True


async def test_semantic_search_disabled_when_setting_false(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    """When MemorySettings.semantic_search_enabled=False (via env), bridge skips semantic recall."""
    # pydantic-settings BaseSettings ignores nested model kwargs, so go via env.
    monkeypatch.setenv("STACKOWL_MEMORY__SEMANTIC_SEARCH_ENABLED", "false")
    settings = Settings()
    assert settings.memory.semantic_search_enabled is False
    components = await MemoryAssembly.build(
        db=tmp_db, settings=settings, provider_registry=_registry(),
    )
    # Adapters are still wired, but the bridge's switch is off — recall uses FTS5.
    assert components.embedding_registry is not None
    assert components.lancedb is not None
    assert components.bridge._semantic_enabled is False


async def test_lancedb_hard_fails_if_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    """LanceDB init failure aborts the assembly — no silent feature regression."""
    from stackowl.memory import lancedb_adapter as lance_mod

    class _BoomLanceDB:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated lancedb native lib missing")

    monkeypatch.setattr(lance_mod, "LanceDBAdapter", _BoomLanceDB)
    with pytest.raises(RuntimeError, match="simulated lancedb"):
        await MemoryAssembly.build(
            db=tmp_db, settings=Settings(memory=MemorySettings()),
            provider_registry=_registry(),
        )


async def test_fact_extractor_receives_embedding_registry(tmp_db: DbPool) -> None:
    """FactExtractor should get the embedding registry so extracted facts get vectors."""
    components = await MemoryAssembly.build(
        db=tmp_db, settings=Settings(memory=MemorySettings()), provider_registry=_registry(),
    )
    # FactExtractor's _embeddings attr was set from the assembly's registry.
    assert components.fact_extractor._embeddings is components.embedding_registry
