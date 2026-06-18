"""Tests for the shared select_tool_provider helper (provider_select.py).

Fixture idiom mirrors tests/pipeline/test_provider_fallback_recovery.py:
- ProviderRegistry with a MockProvider registered on the "powerful" tier
- StepServices wiring the registry
- PipelineState with a generic owl/session
"""
from __future__ import annotations

import logging

import pytest

from stackowl.infra import recovery_context as rc
from stackowl.pipeline.provider_select import select_tool_provider
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


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
