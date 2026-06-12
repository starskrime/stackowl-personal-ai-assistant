from __future__ import annotations

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _select_tool_provider
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _open_breaker(reg, name):
    for _ in range(3):
        reg._breakers[name]._record_failure()


def _state(owl="someowl", session="s1"):
    return PipelineState(trace_id="t", session_id=session, input_text="hi",
                         channel="cli", owl_name=owl, pipeline_step="execute")


def test_tier_fallback_records_provider_recovery():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state())
        assert provider.name == "fast_b"
        evs = rc.get_recovery()
        assert len(evs) == 1
        assert evs[0].kind == "provider_fallback"
        assert evs[0].failed == "powerful_a"
        assert evs[0].recovered_via == "fast_b"
        assert evs[0].user_visible is True
    finally:
        rc.reset(token)


def test_healthy_tier_records_nothing():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state())
        assert provider.name == "powerful_a"
        assert rc.get_recovery() == ()
    finally:
        rc.reset(token)


def test_owl_named_pin_with_open_circuit_is_honored_no_fallback():
    # FR4: an EXPLICIT pin (owl-named provider) is honored even with an OPEN circuit
    # — Step 0 uses registry.get(owl_name), never resolve_tier_with_fallback.
    reg = ProviderRegistry()
    reg.register_mock("pinned_owl", MockProvider(name="pinned_owl"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "pinned_owl")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state(owl="pinned_owl"))
        assert provider.name == "pinned_owl"   # pin honored despite open circuit
        assert rc.get_recovery() == ()
    finally:
        rc.reset(token)
