"""DUR-5 / F069 — consistent memory store-failure policy.

A Kuzu init failure must degrade to a ``None`` adapter (matching LanceDB /
embedding's degrade-don't-crash policy) with a LOUD ERROR and a health-surfaced
``down`` graph status — NOT abort the entire memory assembly / startup. classify
already tolerates a None adapter; the kuzu_sync handler and teardown must too.
"""

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


def _stub_registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    return reg


@pytest.fixture(autouse=True)
def _reset_handler_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


async def test_kuzu_init_failure_degrades_to_none(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A raising KuzuAdapter ctor degrades the build to kuzu_adapter=None and a
    'down' graph health — the build still succeeds (startup survives)."""
    import logging

    import stackowl.memory.kuzu_adapter as kuzu_mod

    class _BoomKuzu:
        def __init__(self, *a: object, **k: object) -> None:
            raise RuntimeError("kuzu native lib missing (simulated ARM wheel gap)")

    # build() does a deferred `from stackowl.memory.kuzu_adapter import KuzuAdapter`,
    # so patch the source module attribute.
    monkeypatch.setattr(kuzu_mod, "KuzuAdapter", _BoomKuzu)

    with caplog.at_level(logging.ERROR):
        components = await MemoryAssembly.build(
            db=tmp_db, settings=Settings(memory=MemorySettings()),
            provider_registry=_stub_registry(),
        )

    assert components.kuzu_adapter is None
    # LOUD: an ERROR-level record mentions the degraded graph.
    assert any(
        r.levelno >= logging.ERROR and "kuzu" in r.getMessage().lower()
        for r in caplog.records
    ), "no loud ERROR on kuzu degradation"
    # Health reflects the degraded graph.
    health = await components.graph_health.health_check()
    assert health.status == "down"


async def test_healthy_kuzu_reports_ok(tmp_db: DbPool) -> None:
    """When Kuzu builds, graph_health reports ok and the adapter is present."""
    components = await MemoryAssembly.build(
        db=tmp_db, settings=Settings(memory=MemorySettings()),
        provider_registry=_stub_registry(),
    )
    assert components.kuzu_adapter is not None
    health = await components.graph_health.health_check()
    assert health.status == "ok"


async def test_kuzu_sync_handler_noops_when_adapter_none() -> None:
    """The kuzu_sync handler must tolerate a None adapter (graph degraded) —
    return a successful no-op rather than raising AttributeError."""
    from datetime import UTC, datetime

    from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler
    from stackowl.scheduler.job import Job

    handler = KuzuSyncJobHandler(kuzu_adapter=None, entity_extractor=None, db=None)
    job = Job(
        job_id="j1", handler_name="kuzu_sync", schedule="manual",
        idempotency_key="k", last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(), status="pending",
    )
    result = await handler.execute(job)
    assert result.success is True
    assert "degraded" in (result.output or "").lower() or "skip" in (result.output or "").lower()
