"""Tests for the shared select_tool_provider helper (provider_select.py).

Fixture idiom mirrors tests/pipeline/test_provider_fallback_recovery.py:
- ProviderRegistry with a MockProvider registered on the "powerful" tier
- StepServices wiring the registry
- PipelineState with a generic owl/session
"""
from __future__ import annotations

import logging

import pytest

from stackowl.commands import tier_command
from stackowl.infra import recovery_context as rc
from stackowl.pipeline.provider_select import (
    answer_floor_for_intent,
    select_tool_provider,
    select_tool_provider_plan,
)
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


class _FakeManifest:
    def __init__(self, *, provider_name: str | None = None, model_tier: str | None = None) -> None:
        self.provider_name = provider_name
        self.model_tier = model_tier
        self.capability_profile: list[str] = []
        self.tools: list[str] = []
        self.skills: tuple[str, ...] = ()


class _FakeOwlReg:
    def __init__(self, manifest: _FakeManifest | None) -> None:
        self._m = manifest

    def get(self, name: str) -> _FakeManifest:
        if self._m is None:
            raise KeyError(name)
        return self._m


def _make_reg_services_state() -> tuple[ProviderRegistry, StepServices, PipelineState]:
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    services = StepServices(provider_registry=reg)
    state = PipelineState(
        trace_id="t",
        session_id="s1",
        input_text="hi",
        channel="cli",
        owl_name="some_owl",
        pipeline_step="execute",
    )
    return reg, services, state


def test_select_returns_a_provider() -> None:
    reg, services, state = _make_reg_services_state()
    token = rc.bind()
    try:
        provider = select_tool_provider(reg, services, state)
        assert provider is not None
    finally:
        rc.reset(token)


def test_log_selection_false_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    reg, services, state = _make_reg_services_state()
    token = rc.bind()
    try:
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            select_tool_provider(reg, services, state, log_selection=False)
        assert not any(
            "tool provider selected" in r.getMessage() for r in caplog.records
        )
    finally:
        rc.reset(token)


def test_log_selection_true_emits(caplog: pytest.LogCaptureFixture) -> None:
    reg, services, state = _make_reg_services_state()
    token = rc.bind()
    try:
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            select_tool_provider(reg, services, state, log_selection=True)
        assert any(
            "tool provider selected" in r.getMessage() for r in caplog.records
        )
    finally:
        rc.reset(token)


# -- escalation plan --------------------------------------------------------- #


def test_plan_default_is_not_pinned_ceiling_powerful() -> None:
    reg, services, state = _make_reg_services_state()  # owl_registry=None
    token = rc.bind()
    try:
        plan = select_tool_provider_plan(reg, services, state)
        assert plan.pinned is False
        assert plan.ceiling_tier == "powerful"
        assert plan.provider.name == "powerful_a"
        # The thin wrapper still returns exactly the plan's provider.
        assert select_tool_provider(reg, services, state) is plan.provider
    finally:
        rc.reset(token)


def test_plan_owl_named_provider_is_pinned() -> None:
    reg, services, state = _make_reg_services_state()
    reg.register_mock("some_owl", MockProvider(name="owl_bound"), tier="standard")
    token = rc.bind()
    try:
        plan = select_tool_provider_plan(reg, services, state)
        assert plan.pinned is True
        assert plan.provider.name == "owl_bound"
    finally:
        rc.reset(token)


def test_plan_manifest_provider_name_is_pinned() -> None:
    reg, services, state = _make_reg_services_state()
    reg.register_mock("pinned_prov", MockProvider(name="pinned_prov"), tier="standard")
    services = StepServices(
        provider_registry=reg,
        owl_registry=_FakeOwlReg(_FakeManifest(provider_name="pinned_prov")),
    )
    token = rc.bind()
    try:
        plan = select_tool_provider_plan(reg, services, state)
        assert plan.pinned is True
        assert plan.provider.name == "pinned_prov"
    finally:
        rc.reset(token)


def test_plan_session_tier_is_pinned_to_that_tier() -> None:
    reg, services, state = _make_reg_services_state()
    reg.register_mock("standard_a", MockProvider(name="standard_a"), tier="standard")
    tier_command.reset_session_tiers()
    tier_command._fallback_prefs[state.session_id] = "standard"
    token = rc.bind()
    try:
        plan = select_tool_provider_plan(reg, services, state)
        assert plan.pinned is True
        assert plan.ceiling_tier == "standard"
        assert plan.provider.name == "standard_a"
    finally:
        rc.reset(token)
        tier_command.reset_session_tiers()


def test_plan_manifest_model_tier_is_ceiling_not_pinned() -> None:
    reg, services, state = _make_reg_services_state()
    reg.register_mock("standard_a", MockProvider(name="standard_a"), tier="standard")
    services = StepServices(
        provider_registry=reg,
        owl_registry=_FakeOwlReg(_FakeManifest(model_tier="standard")),
    )
    token = rc.bind()
    try:
        plan = select_tool_provider_plan(reg, services, state)
        assert plan.pinned is False
        assert plan.ceiling_tier == "standard"
    finally:
        rc.reset(token)


# -- answer_floor_for_intent ------------------------------------------------- #


def test_answer_floor_disabled_is_always_fast():
    # Flag off => legacy behaviour: every turn starts at "fast".
    assert answer_floor_for_intent("standard", ceiling="powerful", enabled=False) == "fast"
    assert answer_floor_for_intent("conversational", ceiling="powerful", enabled=False) == "fast"


def test_answer_floor_conversational_is_fast():
    assert answer_floor_for_intent("conversational", ceiling="powerful", enabled=True) == "fast"


def test_answer_floor_standard_is_standard():
    assert answer_floor_for_intent("standard", ceiling="powerful", enabled=True) == "standard"


def test_answer_floor_unknown_intent_falls_back_to_fast():
    # clarify never reaches the tool loop, but the mapping must be total.
    assert answer_floor_for_intent("clarify", ceiling="powerful", enabled=True) == "fast"
    assert answer_floor_for_intent("garbage", ceiling="powerful", enabled=True) == "fast"


def test_answer_floor_clamped_to_ceiling():
    # A "standard" intent under a "fast" ceiling can never start above the ceiling.
    assert answer_floor_for_intent("standard", ceiling="fast", enabled=True) == "fast"


def test_answer_floor_unknown_ceiling_does_not_crash():
    # Unknown ceiling => no clamp lowering; the intent's own floor stands.
    assert answer_floor_for_intent("standard", ceiling="bogus", enabled=True) == "standard"
