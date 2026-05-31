"""Phase F — raise tool-iteration budget (8 -> 30) + graceful max-out.

Diagnosed from a live turn: the agent did real multi-step work, hit
``max_iterations reached`` at the old budget of 8, and the loop returned ``""``
(empty) — the user got silence. Phase F:

  F1. ``ProviderConfig().tool_max_iterations == 30`` and the loop runs ~30 tool
      iterations (not 8) before max-out when every iteration returns a tool call.

  F2. On max-out the loop makes ONE final model call WITHOUT ``tools=`` (a global,
      language-agnostic wrap-up) and returns the NON-EMPTY wrap-up text — never "".
      Fail-open: if the wrap-up call raises, it falls back to the last assistant
      text already in context (and still does not raise / does not return empty).

Covered for OpenAI end-to-end; mirrored for Anthropic (same flow / same constant).
Reuses the fake-client pattern from test_react_protocol / test_phaseE_context_budget.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers._wrapup import WRAPUP_DIRECTIVE
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider

# --------------------------------------------------------------------------- #
# F1 — config value
# --------------------------------------------------------------------------- #


def test_tool_max_iterations_default_is_30() -> None:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        default_model="gemma4:e4b",
        tier="local",
    )
    assert config.tool_max_iterations == 30


# --------------------------------------------------------------------------- #
# OpenAI fake client — records whether `tools` was passed on each create() call.
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _ToolEveryTimeCompletions:
    """Returns a tool_call on EVERY tool-bearing call so the loop never finalizes.

    On the tool-FREE call (the wrap-up, no ``tools`` kwarg) it returns a distinctive
    non-empty string so the test can prove the wrap-up text is delivered.
    """

    def __init__(self, *, tool_call: bool, raise_on_wrapup: bool = False) -> None:
        self._tool_call = tool_call
        self._raise_on_wrapup = raise_on_wrapup
        self.tools_seen: list[bool] = []  # per-call: was `tools` passed?
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        if not has_tools:
            # This is the wrap-up call.
            if self._raise_on_wrapup:
                raise RuntimeError("simulated provider failure on wrap-up call")
            return _FakeResponse(_FakeMessage(content="WRAPUP-ANSWER-PHASEF", tool_calls=None))
        if self._tool_call:
            tc = _FakeToolCall(id=f"call_{self.create_count}", name="web_search", arguments='{"query":"x"}')
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        # ReAct text action (no native tool_calls).
        return _FakeResponse(
            _FakeMessage(content='ACTION: web_search\n```json\n{"query":"x"}\n```', tool_calls=None)
        )


class _FakeChat:
    def __init__(self, completions: _ToolEveryTimeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _ToolEveryTimeCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_openai_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return "some observation"


# --------------------------------------------------------------------------- #
# F1 — loop runs ~30 iterations before max-out.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_runs_about_thirty_iterations_before_maxout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    # tool-bearing create() calls == iterations; the extra one is the wrap-up.
    tool_iterations = sum(1 for t in completions.tools_seen if t)
    wrapup_calls = sum(1 for t in completions.tools_seen if not t)
    assert wrapup_calls == 1, "exactly one tool-free wrap-up call expected at max-out"
    # Loose/robust: clearly more than the old budget of 8, around 30.
    assert tool_iterations >= 25, f"expected ~30 tool iterations, got {tool_iterations}"
    assert tool_iterations <= 31


# --------------------------------------------------------------------------- #
# F2 — graceful max-out: final tool-free call, non-empty wrap-up text.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_call", [True, False])
async def test_maxout_makes_toolfree_call_and_returns_nonempty(
    monkeypatch: pytest.MonkeyPatch, tool_call: bool
) -> None:
    """The KEY test: at max-out a final call is made with NO tools and the
    non-empty wrap-up text is returned — NOT "". Fails if F2 is reverted to
    ``return "", all_calls``. Covers both native tool_calls and ReAct text."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=tool_call)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    # Returned text is the non-empty wrap-up answer, not empty.
    assert text == "WRAPUP-ANSWER-PHASEF"
    assert text != ""

    # The final create() call carried NO tools (the wrap-up call).
    assert completions.tools_seen[-1] is False, "final wrap-up call must omit tools="
    # All prior calls carried tools.
    assert all(completions.tools_seen[:-1]), "only the last call may be tool-free"


@pytest.mark.asyncio
async def test_maxout_failopen_falls_back_to_last_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the wrap-up call raises, fail-open to the last non-empty assistant text
    already in context — do not raise, do not return empty."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True, raise_on_wrapup=True)
    provider = _make_openai_provider(_FakeClient(completions))

    # Seed a prior assistant text in history so a fallback target exists. The loop
    # appends assistant turns with tool_calls; their `content` is the seeded text.
    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return "obs"

    # Make the model emit assistant content alongside the tool call so a non-empty
    # assistant text lands in `messages` for the fallback to find.
    original_create = completions.create

    async def create_with_content(**kwargs: Any) -> _FakeResponse:
        resp = await original_create(**kwargs)
        if resp.choices[0].message.tool_calls:
            resp.choices[0].message.content = "PROGRESS-SO-FAR"
        return resp

    completions.create = create_with_content  # type: ignore[assignment]

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=dispatcher,
    )

    # Did not raise, did not return empty; fell back to the last assistant text.
    assert text == "PROGRESS-SO-FAR"
    assert text != ""


# --------------------------------------------------------------------------- #
# Anthropic mirror — same wrap-up flow / constant.
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(self, type: str, text: str = "", id: str = "", name: str = "", input: Any = None) -> None:
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _AnthropicMessages:
    """Returns a tool_use stop on every tool-bearing call; non-empty text on the
    tool-free wrap-up call."""

    def __init__(self) -> None:
        self.tools_seen: list[bool] = []
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        if not has_tools:
            return _AResponse("end_turn", [_ABlock("text", text="WRAPUP-ANSWER-ANTHROPIC")])
        return _AResponse(
            "tool_use",
            [_ABlock("tool_use", id=f"tu_{self.create_count}", name="web_search", input={"query": "x"})],
        )


class _AnthropicClient:
    def __init__(self, messages: _AnthropicMessages) -> None:
        self.messages = messages


def _make_anthropic_provider(client: _AnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_anthropic_maxout_makes_toolfree_call_and_returns_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicMessages()
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "WRAPUP-ANSWER-ANTHROPIC"
    assert text != ""
    assert messages.tools_seen[-1] is False, "final wrap-up call must omit tools="
    assert all(messages.tools_seen[:-1]), "only the last call may be tool-free"
    # Sanity: ran more than the old budget of 8.
    tool_iterations = sum(1 for t in messages.tools_seen if t)
    assert tool_iterations >= 25


def test_wrapup_directive_is_nonempty_and_global() -> None:
    # The directive carries no case specifics and instructs a tool-free answer.
    assert WRAPUP_DIRECTIVE
    assert "Do not call any tool" in WRAPUP_DIRECTIVE
