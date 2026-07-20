"""FX-03 — ProviderRegistry.health_check() reports "degraded" when a real
circuit breaker is OPEN. This logic already existed (contributor_name +
health_check are the HealthContributor shape) but was never registered with
any HealthAggregator anywhere in the codebase — a confirmed "defined but
unreachable" gap. These tests pin the logic itself; the wiring is covered
separately in tests/scheduler/test_scheduler_assembly.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio


class _StubProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="", input_tokens=0, output_tokens=0,
            model="stub", provider_name="stub", duration_ms=0.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        if False:  # pragma: no cover
            yield ""
        return


async def test_contributor_name_is_stable() -> None:
    reg = ProviderRegistry()
    assert reg.contributor_name == "provider_registry"


async def test_health_check_ok_when_no_breakers_open() -> None:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    status = await reg.health_check()
    assert status.status == "ok"
    assert status.name == "provider_registry"


async def test_health_check_degraded_when_a_breaker_is_open() -> None:
    reg = ProviderRegistry()
    reg.register_mock("stub", _StubProvider(), tier="powerful")
    breaker = reg.get_circuit_breaker("stub")
    assert breaker is not None
    for _ in range(3):  # default failure_threshold
        await breaker.record(ok=False)
    assert breaker.state is CircuitState.OPEN

    status = await reg.health_check()
    assert status.status == "degraded"
    assert "stub" in (status.message or "")


async def test_health_check_degraded_when_no_providers_registered() -> None:
    reg = ProviderRegistry()
    status = await reg.health_check()
    assert status.status == "degraded"
    assert status.message == "no providers"
