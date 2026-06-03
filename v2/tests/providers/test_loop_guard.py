"""Tests for LoopGuard (Part 1) and the provider loop integration (Part 2).

TDD — written BEFORE the implementation.  All tests in this file must fail
until LoopGuard is created in src/stackowl/providers/_react.py and wired into
both providers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
# Part 1 — LoopGuard unit tests
# --------------------------------------------------------------------------- #


def test_loop_guard_import_works() -> None:
    """LoopGuard must be importable from _react."""
    from stackowl.providers._react import LoopGuard  # noqa: F401


def test_observe_below_warn_at_returns_none() -> None:
    """Calls below warn_at always return None."""
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=3, break_at=4)
    for _ in range(2):
        result = g.observe("web_search", {"query": "x"})
        assert result is None


def test_observe_at_warn_at_returns_directive_exactly_once() -> None:
    """The warn_at-th identical call returns the directive string; the (warn_at+1)-th
    returns None again (one-shot)."""
    from stackowl.providers._react import LoopGuard
    from stackowl.providers._wrapup import LOOP_REPEAT_DIRECTIVE

    g = LoopGuard(warn_at=3, break_at=4)
    for _ in range(2):
        assert g.observe("web_search", {"query": "x"}) is None
    # 3rd call == warn_at — must return the directive
    result = g.observe("web_search", {"query": "x"})
    assert result == LOOP_REPEAT_DIRECTIVE
    # 4th call — already warned, returns None (one-shot)
    result2 = g.observe("web_search", {"query": "x"})
    assert result2 is None


def test_different_args_do_not_accumulate() -> None:
    """Distinct args count separately; below warn_at each returns None."""
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=3, break_at=4)
    for i in range(2):
        assert g.observe("web_search", {"query": f"q{i}"}) is None
    # Even if same name, different args = different signature
    assert g.observe("web_search", {"query": "different"}) is None


def test_tripped_false_before_break_at() -> None:
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=3, break_at=4)
    for _ in range(3):
        g.observe("tool", {"k": "v"})
    assert g.tripped() is False


def test_tripped_true_at_break_at() -> None:
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=3, break_at=4)
    for _ in range(4):
        g.observe("tool", {"k": "v"})
    assert g.tripped() is True


def test_tripped_true_after_break_at() -> None:
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=3, break_at=4)
    for _ in range(10):
        g.observe("tool", {"k": "v"})
    assert g.tripped() is True


def test_observe_never_raises_on_set_args() -> None:
    """observe must silently fall back when args contain non-JSON-serializable objects."""
    from stackowl.providers._react import LoopGuard

    g = LoopGuard()
    # set is not JSON serializable
    result = g.observe("tool", {"data": {1, 2, 3}})  # type: ignore[arg-type]
    assert result is None or isinstance(result, str)


def test_observe_never_raises_on_none_args() -> None:
    from stackowl.providers._react import LoopGuard

    g = LoopGuard()
    result = g.observe("tool", None)  # type: ignore[arg-type]
    # Must not raise; return is None or a string directive
    assert result is None or isinstance(result, str)


def test_observe_never_raises_on_deeply_weird_args() -> None:
    """Cyclic objects, callables, etc. — observe must never raise."""
    from stackowl.providers._react import LoopGuard

    g = LoopGuard()

    class Unserializable:
        pass

    result = g.observe("tool", {"obj": Unserializable()})  # type: ignore[arg-type]
    assert result is None or isinstance(result, str)


def test_loop_repeat_directive_in_wrapup() -> None:
    """LOOP_REPEAT_DIRECTIVE must exist in _wrapup and be a non-empty string."""
    from stackowl.providers._wrapup import LOOP_REPEAT_DIRECTIVE

    assert isinstance(LOOP_REPEAT_DIRECTIVE, str)
    assert len(LOOP_REPEAT_DIRECTIVE) > 20


def test_sort_keys_canonical_form() -> None:
    """Same args in different insertion order produce the same signature (sort_keys)."""
    from stackowl.providers._react import LoopGuard

    g = LoopGuard(warn_at=2, break_at=3)
    # First call with arg order a,b
    g.observe("tool", {"a": 1, "b": 2})
    # Second call with reversed order — must count as the same signature
    result = g.observe("tool", {"b": 2, "a": 1})
    assert result is not None  # 2nd call == warn_at=2 → directive


# --------------------------------------------------------------------------- #
# Part 2 — Provider loop integration: loop guard trips early
# --------------------------------------------------------------------------- #


# Reuse the fake client infrastructure from test_phaseF_max_out


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
        self.model = "test-model"


class _RepeatingSameToolCompletions:
    """Always emits the SAME tool call (identical name + args).

    On a tool-free (wrap-up) call, returns a non-empty text so the wrap-up path
    can be exercised.
    """

    def __init__(self) -> None:
        self.create_count = 0
        self.tools_seen: list[bool] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_count += 1
        has_tools = bool(kwargs.get("tools"))
        self.tools_seen.append(has_tools)
        if not has_tools:
            return _FakeResponse(_FakeMessage(content="GUARD-WRAPUP-ANSWER", tool_calls=None))
        tc = _FakeToolCall(
            id=f"call_{self.create_count}",
            name="web_search",
            arguments='{"query":"same"}',
        )
        return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))


class _FakeChat:
    def __init__(self, completions: _RepeatingSameToolCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _RepeatingSameToolCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_openai_provider(client: _FakeClient) -> Any:
    from stackowl.config.provider import ProviderConfig
    from stackowl.providers.openai_provider import OpenAIProvider

    config = ProviderConfig(
        name="openai-test",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="test-model",
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


@pytest.mark.asyncio
async def test_openai_loop_guard_trips_before_max_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the model repeats the same tool call, guard trips early — fewer than
    resolved_iterations (30) total tool-bearing API calls before the break."""
    from stackowl.config.test_mode import TestModeGuard

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _RepeatingSameToolCompletions()
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    tool_iters = sum(1 for t in completions.tools_seen if t)
    # Must break well before 30 (the max budget).
    assert tool_iters < 30, f"guard should trip early, got {tool_iters} tool iterations"
    # Still returns a non-empty answer (wrap-up path triggered).
    assert text.strip(), "must return non-empty answer after guard trips"


@pytest.mark.asyncio
async def test_openai_loop_guard_result_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer returned after guard trips must be non-empty (wrap-up fires)."""
    from stackowl.config.test_mode import TestModeGuard

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _RepeatingSameToolCompletions()
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text != ""
    assert text == "GUARD-WRAPUP-ANSWER"


# --------------------------------------------------------------------------- #
# Anthropic provider loop guard integration
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(
        self,
        type: str,
        text: str = "",
        id: str = "",
        name: str = "",
        input: Any = None,
    ) -> None:
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _ARepeatingSameToolMessages:
    """Returns tool_use on every tool-bearing call with the SAME name+input.

    Returns non-empty text on tool-free (wrap-up) call.
    """

    def __init__(self) -> None:
        self.create_count = 0
        self.tools_seen: list[bool] = []

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_count += 1
        has_tools = bool(kwargs.get("tools"))
        self.tools_seen.append(has_tools)
        if not has_tools:
            return _AResponse("end_turn", [_ABlock("text", text="A-GUARD-WRAPUP")])
        return _AResponse(
            "tool_use",
            [_ABlock("tool_use", id=f"tu_{self.create_count}", name="web_search", input={"query": "same"})],
        )


class _AClient:
    def __init__(self, messages: _ARepeatingSameToolMessages) -> None:
        self.messages = messages


def _make_anthropic_provider(client: _AClient) -> Any:
    from stackowl.config.provider import ProviderConfig
    from stackowl.providers.anthropic_provider import AnthropicProvider

    config = ProviderConfig(
        name="claude-test",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_anthropic_loop_guard_trips_before_max_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic provider: identical tool calls trip guard early."""
    from stackowl.config.test_mode import TestModeGuard

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages_client = _ARepeatingSameToolMessages()
    provider = _make_anthropic_provider(_AClient(messages_client))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    tool_iters = sum(1 for t in messages_client.tools_seen if t)
    assert tool_iters < 30, f"guard should trip early, got {tool_iters} tool iterations"
    assert text.strip(), "must return non-empty answer after guard trips"


@pytest.mark.asyncio
async def test_anthropic_loop_guard_result_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic: non-empty wrap-up returned after guard trips."""
    from stackowl.config.test_mode import TestModeGuard

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages_client = _ARepeatingSameToolMessages()
    provider = _make_anthropic_provider(_AClient(messages_client))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "A-GUARD-WRAPUP"
