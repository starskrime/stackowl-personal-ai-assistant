"""Tests for MixtureOfAgentsTool (E8-S2) + ProviderRegistry.healthy_distinct.

Network-free: FAKE ModelProviders return canned positions (or raise / hang). A
REAL ProviderRegistry holds them via register_mock so breaker state is genuine.
TestModeGuard is disabled per-test so synthesize_positions/complete may run.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.agents.mixture_of_agents import MixtureOfAgentsTool


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeProvider:
    """Canned provider — answers, raises, hangs, or returns empty per mode.

    E8-S0cost: like a real ModelProvider, it records each ``complete`` call's cost
    to an attached CostTracker (set via ``set_cost_tracker``) — the single recording
    site is the provider, so MoA's proposer spend lands here, not in moa_runner.
    """

    protocol = "openai"

    def __init__(self, label: str, *, mode: str = "ok") -> None:
        self._label = label
        self._mode = mode
        self.calls = 0
        self._cost_tracker: object | None = None

    @property
    def name(self) -> str:
        return self._label

    def set_cost_tracker(self, cost_tracker: object | None) -> None:
        self._cost_tracker = cost_tracker

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.calls += 1
        if self._mode == "raise":
            raise RuntimeError(f"{self._label} boom")
        if self._mode == "hang":
            await asyncio.sleep(60)
        if self._mode == "empty":
            content = "   "
        elif self._mode == "synth":
            content = (
                "CONSENSUS: the models broadly agree.\n"
                "RECOMMENDATION: proceed with option A.\n◆"
            )
        else:
            content = f"{self._label} says: pick A."
        result = CompletionResult(
            content=content,
            input_tokens=7,
            output_tokens=11,
            model=f"{self._label}-model",
            provider_name=self._label,
            duration_ms=1.0,
        )
        if self._cost_tracker is not None:
            await self._cost_tracker.record(  # type: ignore[attr-defined]
                provider_name=self._label,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                duration_ms=result.duration_ms,
                trace_id="",
            )
        return result

    def stream(self, *a: object, **k: object):  # pragma: no cover
        raise NotImplementedError


def _registry(*providers: tuple[str, _FakeProvider, str]) -> ProviderRegistry:
    """Build a registry; each tuple is (name, provider, tier)."""
    reg = ProviderRegistry()
    for name, provider, tier in providers:
        reg.register_mock(name, provider, tier=tier)  # type: ignore[arg-type]
    return reg


class _RecordingCostTracker:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def _record(output: str) -> dict[str, object]:
    return json.loads(output)["record"]


# ----------------------------------------------------------- healthy_distinct


def test_healthy_distinct_skips_open_breakers() -> None:
    p1, p2, p3 = _FakeProvider("a"), _FakeProvider("b"), _FakeProvider("c")
    reg = _registry(("a", p1, "fast"), ("b", p2, "standard"), ("c", p3, "powerful"))
    breaker = reg.get_circuit_breaker("b")
    assert breaker is not None
    breaker._state = CircuitState.OPEN  # type: ignore[attr-defined]

    roster = reg.healthy_distinct()
    names = {p.name for p in roster}
    assert names == {"a", "c"}, names


def test_healthy_distinct_dedups_same_instance() -> None:
    shared = _FakeProvider("shared")
    reg = _registry(("x", shared, "fast"), ("y", shared, "standard"))
    assert len(reg.healthy_distinct()) == 1


def test_healthy_distinct_limit() -> None:
    reg = _registry(
        ("a", _FakeProvider("a"), "fast"),
        ("b", _FakeProvider("b"), "standard"),
        ("c", _FakeProvider("c"), "powerful"),
    )
    assert len(reg.healthy_distinct(limit=2)) == 2


# --------------------------------------------------------------- the tool


@pytest.mark.asyncio
async def test_three_healthy_synthesized() -> None:
    a, b, c = _FakeProvider("a"), _FakeProvider("b"), _FakeProvider("c")
    synth = _FakeProvider("synth", mode="synth")
    reg = _registry(
        ("a", a, "fast"), ("b", b, "standard"), ("c", c, "powerful"), ("synth", synth, "powerful")
    )
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="A or B?")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert res.success
    assert rec["status"] == "ok", rec
    assert rec["ensemble_size"] == 4, rec
    assert rec["degraded_ensemble"] is False, rec
    assert rec["failed"] == [], rec
    assert "proceed with option A" in str(rec["answer"]) or rec["recommendation"]


@pytest.mark.asyncio
async def test_synthesis_provider_failure_surfaces_not_hidden() -> None:
    """No-hidden-errors: when the layer-2 synthesis provider fails, the tool must
    report status='synthesis_failed' — NOT ship a placeholder dressed as 'ok'."""
    a, b = _FakeProvider("a"), _FakeProvider("b")
    bad_synth = _FakeProvider("bad_synth", mode="raise")  # the only powerful-tier
    reg = _registry(("a", a, "fast"), ("b", b, "standard"), ("bad_synth", bad_synth, "powerful"))
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="A or B?")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "synthesis_failed", rec  # surfaced, not faked-ok
    assert rec["status"] != "ok"
    # The fake placeholder must NOT be presented as an answer to the user.
    assert "synthesis unavailable" not in str(rec.get("answer", "")).lower()


@pytest.mark.asyncio
async def test_one_of_three_errors_succeeds_with_two() -> None:
    a = _FakeProvider("a")
    bad = _FakeProvider("bad", mode="raise")
    c = _FakeProvider("c", mode="synth")  # also used as synth (powerful)
    reg = _registry(("a", a, "fast"), ("bad", bad, "standard"), ("c", c, "powerful"))
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="hard one")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "ok", rec
    assert rec["consulted"] == 2, rec
    assert rec["degraded_ensemble"] is True, rec
    assert rec["failed"] == ["bad"], rec


@pytest.mark.asyncio
async def test_proposer_timeout_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    import stackowl.tools.agents.moa_runner as runner

    monkeypatch.setattr(runner, "_PER_PROPOSER_TIMEOUT_S", 0.05)
    a = _FakeProvider("a")
    slow = _FakeProvider("slow", mode="hang")
    synth = _FakeProvider("synth", mode="synth")
    reg = _registry(
        ("a", a, "fast"), ("slow", slow, "standard"), ("synth", synth, "powerful")
    )
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "ok", rec
    assert "slow" in rec["failed"], rec
    assert rec["degraded_ensemble"] is True, rec


@pytest.mark.asyncio
async def test_all_fail_structured() -> None:
    bad1 = _FakeProvider("bad1", mode="raise")
    bad2 = _FakeProvider("bad2", mode="raise")
    reg = _registry(("bad1", bad1, "fast"), ("bad2", bad2, "standard"))
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert res.success  # structured, not a crash
    assert rec["status"] == "all_proposers_failed", rec
    assert set(rec["failed"]) == {"bad1", "bad2"}, rec


@pytest.mark.asyncio
async def test_thin_roster_refusal() -> None:
    only = _FakeProvider("only")
    reg = _registry(("only", only, "fast"))
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "insufficient_roster", rec
    assert rec["available"] == 1, rec
    # the single provider was NEVER consulted (no fake one-model consensus)
    assert only.calls == 0


@pytest.mark.asyncio
async def test_thin_roster_when_breaker_open() -> None:
    a, b = _FakeProvider("a"), _FakeProvider("b")
    reg = _registry(("a", a, "fast"), ("b", b, "standard"))
    breaker = reg.get_circuit_breaker("b")
    assert breaker is not None
    breaker._state = CircuitState.OPEN  # type: ignore[attr-defined]
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "insufficient_roster", rec
    assert rec["available"] == 1, rec


@pytest.mark.asyncio
async def test_per_proposer_cost_recorded() -> None:
    a, b = _FakeProvider("a"), _FakeProvider("b")
    synth = _FakeProvider("synth", mode="synth")
    reg = _registry(("a", a, "fast"), ("b", b, "standard"), ("synth", synth, "powerful"))
    tracker = _RecordingCostTracker()
    # E8-S0cost — the PROVIDER is the single recording site: attach the shared
    # tracker via the registry so every provider.complete records (proposers AND
    # the synthesizer's aggregation call), exactly as the real pipeline does.
    reg.set_cost_tracker(tracker)  # type: ignore[arg-type]
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=reg))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)

    rec = _record(res.output)
    assert rec["status"] == "ok", rec
    # Every real LLM call records (single recording site = the provider): the three
    # proposers (a, b, synth) PLUS the synth provider's powerful-tier aggregation
    # call → synth is recorded twice. This proves proposer spend lands via the
    # provider, not via moa_runner.
    recorded_providers = sorted(str(r["provider_name"]) for r in tracker.records)
    assert recorded_providers == ["a", "b", "synth", "synth"], tracker.records
    assert len(tracker.records) == 4, tracker.records


@pytest.mark.asyncio
async def test_no_registry_refuses() -> None:
    tool = MixtureOfAgentsTool()
    token = set_services(StepServices(provider_registry=None))
    try:
        res = await tool.execute(question="q")
    finally:
        reset_services(token)
    rec = _record(res.output)
    assert rec["status"] == "insufficient_roster", rec
