"""Story 4.2 — SecretaryRouter, FuzzyMatcher, GatewayScanner registry validation, triage step."""

from __future__ import annotations

import pytest

from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.router import FuzzyMatcher, SecretaryRouter, _levenshtein
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import triage
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(name: str, role: str = "generic") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role=role,
        system_prompt="Be helpful.",
        model_tier="fast",
    )


def _make_registry(names: list[str]) -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    for name in names:
        registry.register(_manifest(name, role=f"{name}-role"))
    return registry


def _make_state(
    *,
    input_text: str = "do the thing",
    owl_name: str = "secretary",
    trace_id: str = "trace-test",
    session_id: str = "session-test",
) -> PipelineState:
    return PipelineState(
        trace_id=trace_id,
        session_id=session_id,
        input_text=input_text,
        channel="cli",
        owl_name=owl_name,
        pipeline_step="triage",
    )


def _make_ingress(text: str) -> IngressMessage:
    return IngressMessage(
        text=text,
        session_id="session-test",
        channel="cli",
        trace_id="trace-test",
    )


def _provider_registry_with_mock(mock: MockProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_mock(mock.name, mock, tier="fast")
    return registry


# ---------------------------------------------------------------------------
# FuzzyMatcher
# ---------------------------------------------------------------------------


class TestLevenshtein:
    def test_equal_strings_distance_zero(self) -> None:
        assert _levenshtein("amelia", "amelia") == 0

    def test_empty_strings(self) -> None:
        assert _levenshtein("", "amelia") == 6
        assert _levenshtein("amelia", "") == 6
        assert _levenshtein("", "") == 0

    def test_one_substitution(self) -> None:
        assert _levenshtein("amelia", "amelio") == 1

    def test_one_insertion(self) -> None:
        assert _levenshtein("amelia", "amelias") == 1

    def test_one_deletion(self) -> None:
        assert _levenshtein("amelia", "ameli") == 1

    def test_unicode_nfc_equivalence(self) -> None:
        # "café" with precomposed vs decomposed forms NFC-normalise to the same string.
        assert _levenshtein("café", "café") == 0


class TestFuzzyMatcher:
    def test_exact_match_returns_unity(self) -> None:
        matcher = FuzzyMatcher()
        result = matcher.find("amelia", ["amelia", "miko"])
        assert result is not None
        name, conf = result
        assert name == "amelia"
        assert conf == pytest.approx(1.0)

    def test_close_match_within_threshold(self) -> None:
        matcher = FuzzyMatcher()
        result = matcher.find("amilia", ["amelia", "miko"])
        assert result is not None
        name, conf = result
        assert name == "amelia"
        assert 0.8 <= conf < 1.0

    def test_poor_match_returns_none(self) -> None:
        matcher = FuzzyMatcher()
        assert matcher.find("xyzzy", ["amelia", "miko"]) is None

    def test_distance_exceeds_cap_returns_none(self) -> None:
        matcher = FuzzyMatcher()
        # Long edit distance — ratio could be borderline but distance > max_distance.
        assert matcher.find("amelia", ["abcdefg"]) is None

    def test_empty_query_returns_none(self) -> None:
        matcher = FuzzyMatcher()
        assert matcher.find("", ["amelia"]) is None

    def test_empty_candidates_returns_none(self) -> None:
        matcher = FuzzyMatcher()
        assert matcher.find("amelia", []) is None

    def test_picks_best_candidate(self) -> None:
        matcher = FuzzyMatcher()
        # "amilia" is closer to "amelia" than to "amelio".
        result = matcher.find("amilia", ["amelio", "amelia"])
        assert result is not None
        name, _ = result
        assert name == "amelia"


# ---------------------------------------------------------------------------
# GatewayScanner with OwlRegistry
# ---------------------------------------------------------------------------


class TestGatewayScannerWithRegistry:
    def test_exact_owl_name_strips_prefix(self) -> None:
        registry = _make_registry(["Amelia"])
        scanner = GatewayScanner(owl_registry=registry)
        decision = scanner.scan(_make_ingress("@Amelia please refactor this"))
        assert decision.route == "owl"
        assert decision.target == "Amelia"
        assert decision.suggestion is None
        assert decision.stripped_text == "please refactor this"

    def test_misspelled_owl_with_close_match_suggests(self) -> None:
        registry = _make_registry(["Amelia"])
        scanner = GatewayScanner(owl_registry=registry)
        decision = scanner.scan(_make_ingress("@Amilia run the tests"))
        assert decision.route == "owl"
        assert decision.target == "Amelia"
        assert decision.suggestion is not None
        assert "Amelia" in decision.suggestion
        assert "Did you mean" in decision.suggestion
        assert decision.stripped_text == "run the tests"

    def test_misspelled_owl_with_no_good_match_falls_back(self) -> None:
        registry = _make_registry(["Amelia"])
        scanner = GatewayScanner(owl_registry=registry)
        decision = scanner.scan(_make_ingress("@xyzzy123 hi"))
        assert decision.route == "owl"
        assert decision.target == "secretary"
        assert decision.suggestion is not None
        assert "secretary" in decision.suggestion
        assert decision.stripped_text == "hi"

    def test_without_registry_behaves_as_before(self) -> None:
        scanner = GatewayScanner()
        decision = scanner.scan(_make_ingress("@Whoever do the thing"))
        assert decision.route == "owl"
        assert decision.target == "Whoever"
        assert decision.suggestion is None
        assert decision.stripped_text == "do the thing"

    def test_panic_unchanged(self) -> None:
        scanner = GatewayScanner(owl_registry=_make_registry(["Amelia"]))
        decision = scanner.scan(_make_ingress("something /panic now"))
        assert decision.route == "panic"
        assert decision.target == "panic"

    def test_slash_command_unchanged(self) -> None:
        scanner = GatewayScanner(owl_registry=_make_registry(["Amelia"]))
        decision = scanner.scan(_make_ingress("/help me"))
        assert decision.route == "command"
        assert decision.target == "help"

    def test_default_secretary_route(self) -> None:
        scanner = GatewayScanner(owl_registry=_make_registry(["Amelia"]))
        decision = scanner.scan(_make_ingress("hello world"))
        assert decision.route == "owl"
        assert decision.target == "secretary"


# ---------------------------------------------------------------------------
# SecretaryRouter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSecretaryRouter:
    async def test_valid_owl_name_returned(self) -> None:
        registry = _make_registry(["ResearchOwl", "CodingOwl"])
        mock = MockProvider(name="mock-fast", canned_text="ResearchOwl")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        state = _make_state(input_text="find me papers on persistent memory")
        chosen = await router.route(state)
        assert chosen == "ResearchOwl"

    async def test_garbage_falls_back_to_secretary(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="!!! nonsense ???")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        chosen = await router.route(_make_state())
        assert chosen == "secretary"

    async def test_empty_reply_falls_back_to_secretary(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        assert await router.route(_make_state()) == "secretary"

    async def test_unknown_owl_name_falls_back(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="GhostOwl")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        assert await router.route(_make_state()) == "secretary"

    async def test_strips_quotes_and_whitespace(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text='  "ResearchOwl".  ')
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        assert await router.route(_make_state()) == "ResearchOwl"

    async def test_takes_first_line_of_multiline_reply(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="ResearchOwl\nbecause it fits")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        assert await router.route(_make_state()) == "ResearchOwl"

    async def test_secretary_always_acceptable_as_reply(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="secretary")
        providers = _provider_registry_with_mock(mock)
        router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
        assert await router.route(_make_state()) == "secretary"


# ---------------------------------------------------------------------------
# Triage step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTriageStep:
    async def test_invokes_router_for_secretary_input(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="ResearchOwl")
        providers = _provider_registry_with_mock(mock)
        services = StepServices(provider_registry=providers, owl_registry=registry)
        token = set_services(services)
        try:
            result = await triage.run(_make_state(owl_name="secretary"))
        finally:
            reset_services(token)
        assert result.owl_name == "ResearchOwl"
        # Mock provider should have been called exactly once for routing.
        assert mock.call_count == 1

    async def test_direct_address_passes_through_when_valid(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        # Provider should never be called when a valid direct-address is supplied.
        mock = MockProvider(name="mock-fast", canned_text="UNUSED")
        providers = _provider_registry_with_mock(mock)
        services = StepServices(provider_registry=providers, owl_registry=registry)
        token = set_services(services)
        try:
            result = await triage.run(_make_state(owl_name="ResearchOwl"))
        finally:
            reset_services(token)
        assert result.owl_name == "ResearchOwl"
        assert mock.call_count == 0

    async def test_unknown_direct_address_falls_back_to_secretary(self) -> None:
        registry = _make_registry(["ResearchOwl"])
        mock = MockProvider(name="mock-fast", canned_text="UNUSED")
        providers = _provider_registry_with_mock(mock)
        services = StepServices(provider_registry=providers, owl_registry=registry)
        token = set_services(services)
        try:
            result = await triage.run(_make_state(owl_name="GhostOwl"))
        finally:
            reset_services(token)
        assert result.owl_name == "secretary"
        assert mock.call_count == 0

    async def test_pass_through_when_registries_missing(self) -> None:
        # No services set in this context — get_services() yields empty StepServices.
        services = StepServices()
        token = set_services(services)
        try:
            result = await triage.run(_make_state(owl_name="secretary"))
        finally:
            reset_services(token)
        assert result.owl_name == "secretary"

    async def test_direct_address_without_owl_registry_is_accepted(self) -> None:
        mock = MockProvider(name="mock-fast", canned_text="UNUSED")
        providers = _provider_registry_with_mock(mock)
        services = StepServices(provider_registry=providers, owl_registry=None)
        token = set_services(services)
        try:
            result = await triage.run(_make_state(owl_name="DirectOwl"))
        finally:
            reset_services(token)
        assert result.owl_name == "DirectOwl"
        assert mock.call_count == 0
