"""Self-Healing Turn Supervisor W4.T17 — give-up nudge must coexist safely with
the concurrent-message exit paths (live-steer, TurnStopped, BudgetBreach).

Three hazards (Winston / party-mode) at the iteration boundary where the model
produces a FINAL answer with NO tool call — the only place the persistence
give-up nudge (``_enforce`` → ``decide_nudge``) fires:

  (a) NO-NUDGE-ON-STOP/BREACH — if the iteration callback (steer/budget) raises a
      TurnStopped / BudgetBreach, that exit must be honored: the give-up nudge
      must NOT fire first and swallow the stop into another iteration.
  (b) STEER PRE-EMPTS NUDGE — if a live-steer message is pending at the give-up
      boundary, the steer is folded and the give-up nudge does NOT fire (the user
      is redirecting; re-nudging toward the OLD goal is wrong).
  (c) BUDGET-CHARGED NUDGE — a budget-exhausted turn (governor breach surfaced
      through the SAME callback) must NOT nudge (an extra LLM iteration that the
      budget already forbids).

ROOT CAUSE (recon): on the final-answer branch both providers ran ``_enforce``
FIRST and ``continue``d on a nudge, so ``on_iteration_complete`` (the steer/budget
callback) NEVER ran on a give-up round. The fix runs the iteration callback BEFORE
the give-up check: a raise (stop/breach) propagates, a folded steer pre-empts the
nudge.

Fake-client harness mirrors tests/providers/test_budget_breach_propagates.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import BudgetBreach
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.react_callback import ReActIterationState

# --------------------------------------------------------------------------- #
# Persistence check that ALWAYS says give-up (drives the nudge path).
# --------------------------------------------------------------------------- #


async def _giveup_check(draft: str, outcomes: list[str]) -> str | None:
    """Judge that always returns the persistence directive (a give-up)."""
    return PERSISTENCE_DIRECTIVE


_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return f"result_for_{name}"


# --------------------------------------------------------------------------- #
# OpenAI fake client
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tc_id: str, name: str, arguments: str) -> None:
        self.id = tc_id
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


class _ScriptedCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_calls.append(kwargs)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_ScriptedCompletions(responses))


def _make_openai_provider(client: _FakeOAIClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="test",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="test-model",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def _final_response_oai(text: str) -> _FakeResponse:
    return _FakeResponse(_FakeMessage(content=text, tool_calls=None))


# --------------------------------------------------------------------------- #
# Anthropic fake client
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(
        self,
        block_type: str,
        text: str = "",
        block_id: str = "",
        name: str = "",
        input: Any = None,
    ) -> None:
        self.type = block_type
        self.text = text
        self.id = block_id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _ScriptedAnthropicMessages:
    def __init__(self, responses: list[_AResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_calls.append(kwargs)
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


class _FakeAnthropicClient:
    def __init__(self, responses: list[_AResponse]) -> None:
        self.messages = _ScriptedAnthropicMessages(responses)


def _make_anthropic_provider(client: _FakeAnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


def _final_response_anthropic(text: str) -> _AResponse:
    return _AResponse("end_turn", [_ABlock("text", text=text)])


# --------------------------------------------------------------------------- #
# Callback factories
# --------------------------------------------------------------------------- #


def _raise_breach_cb() -> Any:
    async def _cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        raise BudgetBreach("steps", 1, 1)

    return _cb


def _steer_cb(message: str) -> Any:
    """Iteration callback that folds a [steering] message (live-steer)."""

    async def _cb(state: ReActIterationState) -> list[dict[str, Any]] | None:
        return [{"role": "user", "content": f"[steering] {message}"}]

    return _cb


def _giveup_persistence_injected(create_calls: list[dict[str, Any]]) -> bool:
    """True iff the persistence give-up directive was injected into ANY API call's
    messages (the nudge fired)."""
    for call in create_calls:
        for m in call.get("messages", []):
            content = m.get("content")
            if isinstance(content, str) and PERSISTENCE_DIRECTIVE in content:
                return True
    return False


def _steer_injected(create_calls: list[dict[str, Any]]) -> bool:
    for call in create_calls:
        for m in call.get("messages", []):
            content = m.get("content")
            if isinstance(content, str) and content.startswith("[steering]"):
                return True
    return False


# =========================================================================== #
# (a) NO-NUDGE-ON-STOP/BREACH — a raising callback pre-empts the give-up nudge.
# =========================================================================== #


@pytest.mark.asyncio
async def test_anthropic_breach_preempts_giveup_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """A give-up final answer whose iteration callback raises BudgetBreach must
    surface the breach — the give-up nudge must NOT fire first and swallow it."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    client = _FakeAnthropicClient([_final_response_anthropic("I cannot do this.")])
    provider = _make_anthropic_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do the task",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            persistence_check=_giveup_check,
            on_iteration_complete=_raise_breach_cb(),
        )

    # The nudge must NOT have been injected (the breach pre-empted it).
    assert not _giveup_persistence_injected(client.messages.create_calls)


@pytest.mark.asyncio
async def test_openai_breach_preempts_giveup_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    client = _FakeOAIClient([_final_response_oai("I cannot do this.")])
    provider = _make_openai_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do the task",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            persistence_check=_giveup_check,
            on_iteration_complete=_raise_breach_cb(),
        )

    assert not _giveup_persistence_injected(client.chat.completions.create_calls)


# =========================================================================== #
# (b) STEER PRE-EMPTS NUDGE — a pending live-steer wins over the give-up nudge.
# =========================================================================== #


@pytest.mark.asyncio
async def test_anthropic_steer_preempts_giveup_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending steer at the give-up boundary is folded; the give-up nudge does
    NOT fire (the user redirected — re-nudging toward the OLD goal is wrong)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Round 0: give-up final answer. The steer folds → round 1 sees [steering].
    # Round 1: another final answer (delivered) → exit.
    client = _FakeAnthropicClient([
        _final_response_anthropic("I cannot do this."),
        _final_response_anthropic("New direction handled."),
    ])
    provider = _make_anthropic_provider(client)

    text, _calls = await provider.complete_with_tools(
        user_text="do the task",
        system_text="",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        persistence_check=_giveup_check,
        on_iteration_complete=_steer_cb("actually do this other thing"),
    )

    assert _steer_injected(client.messages.create_calls), "steer must be folded"
    assert not _giveup_persistence_injected(
        client.messages.create_calls
    ), "give-up nudge must NOT fire when a steer is pending"


@pytest.mark.asyncio
async def test_openai_steer_preempts_giveup_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    client = _FakeOAIClient([
        _final_response_oai("I cannot do this."),
        _final_response_oai("New direction handled."),
    ])
    provider = _make_openai_provider(client)

    text, _calls = await provider.complete_with_tools(
        user_text="do the task",
        system_text="",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        persistence_check=_giveup_check,
        on_iteration_complete=_steer_cb("actually do this other thing"),
    )

    assert _steer_injected(client.chat.completions.create_calls), "steer must be folded"
    assert not _giveup_persistence_injected(
        client.chat.completions.create_calls
    ), "give-up nudge must NOT fire when a steer is pending"


# =========================================================================== #
# (c) BUDGET-CHARGED NUDGE — a governor breach surfaced through the iteration
# callback stops the turn instead of nudging (same callback as (a); this is the
# budget-governor semantics regression guard).
# =========================================================================== #


@pytest.mark.asyncio
async def test_anthropic_budget_exhausted_no_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the budget governor signals exhaustion (BudgetBreach via the iteration
    callback) at a give-up boundary, the turn finalizes on the breach — no extra
    nudge iteration is charged against the exhausted budget."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    client = _FakeAnthropicClient([_final_response_anthropic("I give up.")])
    provider = _make_anthropic_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do the task",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            persistence_check=_giveup_check,
            on_iteration_complete=_raise_breach_cb(),
        )

    # Exactly ONE API round — no extra nudge iteration was charged.
    assert len(client.messages.create_calls) == 1
    assert not _giveup_persistence_injected(client.messages.create_calls)


@pytest.mark.asyncio
async def test_openai_budget_exhausted_no_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    client = _FakeOAIClient([_final_response_oai("I give up.")])
    provider = _make_openai_provider(client)

    with pytest.raises(BudgetBreach):
        await provider.complete_with_tools(
            user_text="do the task",
            system_text="",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            persistence_check=_giveup_check,
            on_iteration_complete=_raise_breach_cb(),
        )

    assert len(client.chat.completions.create_calls) == 1
    assert not _giveup_persistence_injected(client.chat.completions.create_calls)
