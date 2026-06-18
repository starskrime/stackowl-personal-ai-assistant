"""Tests for the clarify verdict + line-3 question parse in SecretaryRouter.

Task 2 of the clarify-router-verdict plan:
- `_parse_intent_class` must return 'clarify' as a valid token.
- `_parse_clarify_question(raw, intent_class)` is a new pure helper.
- A 'clarify' verdict with no question text downgrades to 'standard'.
- 2-line (conversational / standard) paths stay byte-identical.
- A bare class token on line 3 must NOT be treated as a question (finding 1).
- route() must downgrade clarify→standard when question is absent (finding 2).
"""

from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.router import RouteResult, SecretaryRouter
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _r() -> SecretaryRouter:
    # parsing helpers are pure — no registries needed
    return SecretaryRouter.__new__(SecretaryRouter)


# ---------------------------------------------------------------------------
# Helpers for route()-level tests (finding 2)
# ---------------------------------------------------------------------------


def _manifest(name: str, role: str = "generic") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role=role,
        system_prompt="Be helpful.",
        model_tier="fast",
    )


def _make_router(canned_reply: str) -> tuple[SecretaryRouter, MockProvider]:
    mock = MockProvider(name="mock-fast", canned_text=canned_reply)
    registry = OwlRegistry.with_default_secretary()
    registry.register(_manifest("research_owl", role="research-role"))
    providers = ProviderRegistry()
    providers.register_mock(mock.name, mock, tier="fast")
    router = SecretaryRouter(provider_registry=providers, owl_registry=registry)
    return router, mock


def _state(input_text: str) -> PipelineState:
    return PipelineState(
        trace_id="trace-test",
        session_id="session-test",
        input_text=input_text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="triage",
    )


# ---------------------------------------------------------------------------
# Existing tests (unchanged)
# ---------------------------------------------------------------------------


def test_parse_clarify_with_question() -> None:
    raw = "secretary\nclarify\nDo you want me to create images, or find existing ones?"
    assert _r()._parse_intent_class(raw) == "clarify"
    assert _r()._parse_clarify_question(raw, "clarify") == \
        "Do you want me to create images, or find existing ones?"


def test_clarify_without_question_downgrades_to_standard() -> None:
    raw = "secretary\nclarify\n"
    assert _r()._parse_clarify_question(raw, "clarify") is None  # no question text


def test_standard_and_conversational_unchanged() -> None:
    assert _r()._parse_intent_class("scout\nstandard") == "standard"
    assert _r()._parse_intent_class("scout\nconversational") == "conversational"
    # no question for non-clarify classes
    assert _r()._parse_clarify_question("scout\nstandard", "standard") is None


# ---------------------------------------------------------------------------
# Finding 1 — bare class token on line 3 must NOT become the question
# ---------------------------------------------------------------------------


def test_bare_class_token_on_line3_yields_no_question() -> None:
    """A degenerate LLM reply whose line-3 is itself a class token must yield
    question=None so the caller can downgrade clarify→standard."""
    raw = "secretary\nclarify\nstandard"
    question = _r()._parse_clarify_question(raw, "clarify")
    assert question is None, f"Expected None but got {question!r}"


def test_bare_class_token_variants_all_yield_no_question() -> None:
    """Covers all three class tokens appearing as the sole line-3 content."""
    for token in ("conversational", "standard", "clarify"):
        raw = f"secretary\nclarify\n{token}"
        question = _r()._parse_clarify_question(raw, "clarify")
        assert question is None, f"Token {token!r} should not become a question, got {question!r}"


def test_class_token_inside_sentence_is_kept_as_question() -> None:
    """A line that CONTAINS a class word inside natural language must NOT be dropped."""
    raw = "secretary\nclarify\nShould I use standard or advanced mode?"
    question = _r()._parse_clarify_question(raw, "clarify")
    assert question == "Should I use standard or advanced mode?"


def test_class_token_with_punctuation_on_line3_yields_no_question() -> None:
    """Normalized token detection must strip surrounding punctuation."""
    raw = "secretary\nclarify\n'standard'."
    question = _r()._parse_clarify_question(raw, "clarify")
    assert question is None


# ---------------------------------------------------------------------------
# Finding 2 — route() downgrade: clarify with no question → standard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_clarify_no_question_downgrades_to_standard() -> None:
    """route() must set intent_class='standard' when the clarify token has no question."""
    router, _ = _make_router("secretary\nclarify\n")
    res = await router.route(_state("do something"))
    assert isinstance(res, RouteResult)
    assert res.intent_class == "standard"
    assert res.clarify_question is None


@pytest.mark.asyncio
async def test_route_clarify_bare_class_token_as_line3_downgrades_to_standard() -> None:
    """route() must downgrade when line-3 is a bare class token (finding 1 via route())."""
    router, _ = _make_router("secretary\nclarify\nstandard")
    res = await router.route(_state("do something"))
    assert isinstance(res, RouteResult)
    assert res.intent_class == "standard"
    assert res.clarify_question is None
