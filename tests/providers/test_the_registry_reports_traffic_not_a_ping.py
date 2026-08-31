"""The registry's health check ran a live inference, and a busy model read as DOWN.

MEASURED 2026-08-31. ``provider_registry`` timed out 18 times across the retained
logs — 7 of them today, each producing "UNHEALTHY subsystems detected" and a
CRITICAL Telegram page, and each followed by a recovery page when the next sweep
found ok=11/11.

WHAT IT ACTUALLY DOES. ``ProviderRegistry.health_check`` gathers
``p.health_check()`` over every provider, and ``ModelProvider.health_check``
(base.py:670) calls ``self.complete([Message(role="user", content="ping")], ...)``
— a REAL generation request to the model. So the platform's answer to "is the
provider registry healthy" is a live inference, whose latency depends on what the
model host is doing at that instant.

THE 19:17 CASE, END TO END. An ``incident-4ebd60e0d442`` turn was running at
19:16:38. At 19:17:37 the sweep began; the bounded connectivity probe answered
``provider NeraAiRaw [openai]: ok (631ms)``; the registry's ping then queued behind
the live turn and did not return within 5s, nor within 10s on the re-probe. The
provider was BUSY. It was reported DOWN.

A HYPOTHESIS THIS REFUTED, recorded because the correlation was strong and wrong.
17 of the 18 timeouts land within 0.05s of a ``[config] Loaded providers`` line,
which looked like config-reload contention blocking the loop. It is not: those
lines carry the SAME trace_id as the timeout, so they are the sweep's own
continuation logging AFTER the cancellation. And a ``Settings()`` construction was
measured at 19ms, which cannot starve a 5,000ms window.

THE DIVISION OF LABOUR IS ALREADY WRITTEN DOWN, one line above the registration::

    # FX-03 — the live circuit-breaker signal (real traffic health),
    # complementary to the synthetic per-provider probes just registered.
    agg.register(provider_registry)

The synthetic probe belongs to ``ProviderContributor``, which is registered per
enabled provider immediately above and calls ``probe_provider`` — an httpx GET with
its own timeout, measured over 1,248 samples at p50 715ms, p90 1,154ms, max 5,746ms.
The registry was running a SECOND, worse copy of that job: unbounded, load-sensitive
and paid for in inference.

SO NO COVERAGE IS LOST. A genuinely unreachable provider is still caught by
``provider:<name>``, which reports into the same aggregator on the same sweep — the
``ok (631ms)`` line above is that contributor running, once per sweep, 1,248 times
in four days.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio


class _CountingProvider(ModelProvider):
    """Records whether anything asked it to generate."""

    def __init__(self) -> None:
        self.completions = 0

    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.completions += 1
        return CompletionResult(
            content="pong", input_tokens=0, output_tokens=0,
            model="stub", provider_name="stub", duration_ms=0.0,
        )

    async def stream(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        if False:  # pragma: no cover
            yield ""
        return


class _BusyProvider(_CountingProvider):
    """A model that is working on something else — the live 19:17 case."""

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.completions += 1
        await asyncio.sleep(30)
        raise AssertionError("unreachable")


async def test_the_health_check_does_NOT_generate() -> None:
    """The whole item. A health check that costs an inference reports on the
    model's queue depth, not on the registry."""
    provider = _CountingProvider()
    reg = ProviderRegistry()
    reg.register_mock("stub", provider, tier="powerful")

    status = await reg.health_check()

    assert status.status == "ok"
    assert provider.completions == 0, (
        "the registry asked the model to generate — that is what turned a busy "
        "provider into 7 critical operator pages on 2026-08-31"
    )


async def test_a_BUSY_provider_does_not_stall_the_sweep() -> None:
    """The measured failure, reproduced: a provider mid-generation must not make
    the registry's health check hang. It returned in 5s+ only because the
    aggregator cancelled it."""
    reg = ProviderRegistry()
    reg.register_mock("stub", _BusyProvider(), tier="powerful")

    status = await asyncio.wait_for(reg.health_check(), timeout=2.0)

    assert status.status == "ok"


async def test_an_OPEN_breaker_is_still_degraded() -> None:
    """The signal the registry is actually FOR — real traffic health. This is the
    behaviour that must survive, and it is the reason the contributor exists."""
    reg = ProviderRegistry()
    reg.register_mock("stub", _CountingProvider(), tier="powerful")
    breaker = reg._breakers["stub"]  # noqa: SLF001
    for _ in range(20):
        await breaker.record(ok=False)

    status = await reg.health_check()

    assert breaker.state is CircuitState.OPEN, "fixture did not open the breaker"
    assert status.status == "degraded"
    assert status.message is not None and "stub" in status.message


async def test_NO_providers_is_still_degraded() -> None:
    reg = ProviderRegistry()
    status = await reg.health_check()
    assert status.status == "degraded"
    assert status.message == "no providers"


async def test_the_name_is_unchanged() -> None:
    assert ProviderRegistry().contributor_name == "provider_registry"


async def test_the_synthetic_probe_still_exists_and_is_BOUNDED() -> None:
    """No coverage is lost: connectivity is ProviderContributor's job, it is
    registered per enabled provider in the same aggregator, and its probe carries
    its own timeout rather than borrowing the aggregator's."""
    import inspect

    from stackowl.health.contributors import ProviderContributor
    from stackowl.startup import provider_probe

    assert "probe_provider" in inspect.getsource(ProviderContributor.health_check)
    assert "_TIMEOUT" in inspect.getsource(provider_probe.probe_provider), (
        "the connectivity probe must bound itself"
    )
