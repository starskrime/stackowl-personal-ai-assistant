"""Tests for SecretaryRouter RouteResult + intent_class (line-2 classification).

The router must emit a RouteResult with:
  - owl_name: exactly the same as before (line-1-only parse; byte-identical)
  - intent_class: optional line-2 token — 'conversational' | 'standard'; fail-safe to 'standard'

No English keyword classification is performed client-side; the class comes
entirely from the LLM's second line.
"""

from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.router import (
    _ROUTING_MAX_TOKENS,
    RouteResult,
    SecretaryRouter,
)
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Fixture helpers — mirror test_story_4_2.py exactly so fakes are reused
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


def _provider_registry_with_mock(mock: MockProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_mock(mock.name, mock, tier="fast")
    return registry


class _RouterEnv:
    """Thin wrapper so tests can call env.set_reply() and env.router / env.state()."""

    def __init__(self) -> None:
        self._mock = MockProvider(name="mock-fast", canned_text="secretary")
        registry = _make_registry(["research_owl"])
        providers = _provider_registry_with_mock(self._mock)
        self.router = SecretaryRouter(provider_registry=providers, owl_registry=registry)

    def set_reply(self, text: str) -> None:
        # MockProvider stores canned text; replace the private attr directly.
        self._mock._canned_text = text  # noqa: SLF001

    def state(self, input_text: str) -> PipelineState:
        return PipelineState(
            trace_id="trace-test",
            session_id="session-test",
            input_text=input_text,
            channel="cli",
            owl_name="secretary",
            pipeline_step="triage",
        )


@pytest.fixture()
def router_env() -> _RouterEnv:
    return _RouterEnv()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owl_line1_class_line2_conversational(router_env: _RouterEnv) -> None:
    router_env.set_reply("secretary\nconversational")
    res = await router_env.router.route(router_env.state("hi"))
    assert isinstance(res, RouteResult)
    assert res.owl_name == "secretary"
    assert res.intent_class == "conversational"


@pytest.mark.asyncio
async def test_class_defaults_standard_when_absent(router_env: _RouterEnv) -> None:
    router_env.set_reply("secretary")
    res = await router_env.router.route(router_env.state("do a task"))
    assert res.owl_name == "secretary"
    assert res.intent_class == "standard"


@pytest.mark.asyncio
async def test_garbled_class_falls_back_to_standard(router_env: _RouterEnv) -> None:
    router_env.set_reply("secretary\nbanana")
    res = await router_env.router.route(router_env.state("x"))
    assert res.owl_name == "secretary"
    assert res.intent_class == "standard"


@pytest.mark.asyncio
async def test_owl_selection_unchanged_with_class_line(router_env: _RouterEnv) -> None:
    router_env.set_reply("research_owl\nstandard")
    res = await router_env.router.route(router_env.state("research X"))
    assert res.owl_name == "research_owl"


# ---------------------------------------------------------------------------
# Direct-parse unit tests — no provider/registry needed (parse-only)
# ---------------------------------------------------------------------------


def _r() -> SecretaryRouter:
    return SecretaryRouter(provider_registry=None, owl_registry=None)  # type: ignore[arg-type]


def test_token_cap_raised() -> None:
    assert _ROUTING_MAX_TOKENS == 64


def test_class_on_line_2() -> None:
    assert _r()._parse_intent_class("secretary\nconversational") == "conversational"


def test_class_on_later_line_is_scanned() -> None:
    assert _r()._parse_intent_class("secretary\n\nconversational") == "conversational"
    assert _r()._parse_intent_class("secretary\nlet me think\nconversational") == "conversational"


def test_class_token_with_punctuation() -> None:
    assert _r()._parse_intent_class("secretary\n'conversational'.") == "conversational"


def test_standard_when_explicit() -> None:
    assert _r()._parse_intent_class("secretary\nstandard") == "standard"


def test_failsafe_standard_when_no_class_token() -> None:
    assert _r()._parse_intent_class("secretary") == "standard"
    assert _r()._parse_intent_class("secretary\nblah blah") == "standard"


def test_failsafe_conversational_when_reply_totally_empty() -> None:
    # A provider that returns nothing (empty completion) carries zero signal —
    # fail safe to no-tool-loop, not the full agentic path.
    assert _r()._parse_intent_class("") == "conversational"
    assert _r()._parse_intent_class("   \n  ") == "conversational"


def test_owl_name_line_not_treated_as_class() -> None:
    assert _r()._parse_intent_class("standard\nconversational") == "conversational"


def test_prompt_distinguishes_direct_answer_from_action_required() -> None:
    # The conversational class is no longer social-ONLY: it now covers any request
    # answerable directly from the model's own knowledge (greeting/opinion/chit-chat
    # AND a definition/how-to/explanation/advice/mnemonic). 'standard' is reserved for
    # requests that require an external action. (L2 routing fix.)
    p = _r()._build_prompt([("secretary", "general")], "i liked your style")
    low = p.lower()
    assert "conversational" in low and "standard" in low
    # Still covers the social cases…
    assert "chit-chat" in low or "greeting" in low
    # …and now keys on the real discriminator: needing an external action.
    assert "external action" in low


def test_prompt_biases_act_first_on_reversible_ambiguity() -> None:
    # The act-vs-ask threshold must explicitly reserve 'clarify' for IRREVERSIBLE
    # ambiguity and instruct the router to act + state an assumption when the
    # likely action is reversible — so the assistant stops deferring on every
    # recoverable ambiguity.
    p = _r()._build_prompt([("secretary", "general")], "do something")
    low = p.lower()
    assert "irreversible" in low
    assert "most likely" in low
    assert "state your assumption" in low


def test_prompt_forbids_self_denial_classification() -> None:
    # A network/hardware/device request must not fall into 'conversational' just
    # because the router LLM reflexively reasons "I'm a cloud chatbot with no
    # local access" — the assistant may have live local tools this turn. The
    # prompt must explicitly forbid that self-denial reflex and route such
    # requests to 'standard'.
    p = _r()._build_prompt([("secretary", "general")], "scan my local network")
    low = p.lower()
    assert "self-deny" in low or "assuming a generic cloud chatbot" in low
    assert "scan" in low and "device" in low and "network" in low


def test_prompt_treats_addressing_a_named_owl_as_standard() -> None:
    # Live incident (2026-07-16): "He Brain" (a vocative greeting addressed to
    # the "Brain" owl by name) was classified 'conversational' — the wording
    # alone reads like a plain greeting, so no tool was ever offered and the
    # user's actual delegation request could never be fulfilled (it could only
    # be floored/apologized, never dispatched). The prompt must state that
    # addressing/delegating to a named owl IS the action, regardless of how
    # greeting-shaped the wording looks.
    p = _r()._build_prompt([("Brain", "general reasoning"), ("secretary", "general")], "He Brain")
    low = p.lower()
    assert "delegat" in low
    assert "named owl" in low or "owls above" in low
    assert "greeting" in low  # explicitly covers the greeting-shaped wording case


def test_prompt_attempts_resolution_before_clarify() -> None:
    # F-56: 'clarify' is a last resort. Before choosing it, the router must be
    # instructed to first try to resolve the ambiguity itself — recall what it
    # already knows and run a cheap, reversible probe — and only clarify when the
    # ambiguity remains AND a wrong guess would be irreversible or expensive.
    p = _r()._build_prompt([("secretary", "general")], "do something")
    low = p.lower()
    assert "before" in low  # resolution happens BEFORE clarifying
    assert "recall" in low  # recall what you already know first
    assert "probe" in low or "check" in low  # cheap reversible probe
