from __future__ import annotations

import pytest

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.provider_select import select_tool_provider as _select_tool_provider
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as exe
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _open_breaker(reg, name):
    for _ in range(3):
        reg._breakers[name]._record_failure()


def _state(owl="someowl", session="s1"):
    return PipelineState(trace_id="t", session_id=session, input_text="hi",
                         channel="cli", owl_name=owl, pipeline_step="execute")


def test_record_recovery_false_suppresses_event_on_open_circuit():
    # assemble's quiet window-probe selection must NOT record the provider_fallback
    # (else execute's real selection records the SAME event → duplicate user line).
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    reg.register_mock("fast_b", MockProvider(name="fast_b"), tier="fast")
    _open_breaker(reg, "powerful_a")
    services = StepServices(provider_registry=reg)
    token = rc.bind()
    try:
        provider = _select_tool_provider(reg, services, _state(), record_recovery=False)
        assert provider.name == "fast_b"          # still degrades to the fallback
        assert rc.get_recovery() == ()            # but records NOTHING
    finally:
        rc.reset(token)


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


@pytest.mark.asyncio
async def test_run_floors_when_all_providers_open():
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    _open_breaker(reg, "powerful_a")
    services = StepServices(provider_registry=reg)   # tool_registry None → plain-stream path
    stoken = set_services(services)
    token = rc.bind()
    try:
        out = await exe.run(_state())
        assert any("AllProvidersUnavailableError" in e for e in out.errors)
        assert rc.get_recovery() == ()   # selection raised before any fallback recorded
    finally:
        rc.reset(token)
        reset_services(stoken)


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
