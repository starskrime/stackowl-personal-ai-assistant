"""B1 — resume_messages seam on both provider loops.

Verifies three scenarios for each provider (OpenAI + Anthropic):

  (a) resume_messages=None  ⟹ behavior identical to pre-B1 (regression guard).
  (b) resume_messages=<transcript>  ⟹ loop seeds from the transcript, NOT from
      user_text; the first LLM call sees the restored messages; on_iteration_complete
      reports the continued state.
  (c) OpenAI: no double system-prompt when resume_messages already has role=system
      at index 0.

Uses the same fake-client harness as test_iteration_callback.py — no real network.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ResumeTranscriptError
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.react_callback import ReActIterationState

# ---------------------------------------------------------------------------
# Shared tool schemas + dispatcher
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "Run a calculation.",
            "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return f"result_for_{name}"


# ---------------------------------------------------------------------------
# OpenAI fake client helpers (reused from test_iteration_callback.py pattern)
# ---------------------------------------------------------------------------


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
    def __init__(
        self, content: str | None, tool_calls: list[_FakeToolCall] | None = None
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeOAIResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "test-model"


class _RecordingCompletions:
    """Replays scripted responses and records every messages list passed in."""

    def __init__(self, responses: list[_FakeOAIResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.calls: list[list[dict[str, Any]]] = []  # captured messages arg per call

    async def create(self, **kwargs: Any) -> _FakeOAIResponse:
        self.calls.append(list(kwargs.get("messages", [])))
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _RecordingCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeOAIResponse]) -> None:
        self._rec = _RecordingCompletions(responses)
        self.chat = _FakeChat(self._rec)

    @property
    def calls(self) -> list[list[dict[str, Any]]]:
        return self._rec.calls


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


def _oai_tool_resp(tc_id: str, expr: str) -> _FakeOAIResponse:
    tc = _FakeToolCall(tc_id, "calc", f'{{"expr":"{expr}"}}')
    return _FakeOAIResponse(_FakeMessage(content=None, tool_calls=[tc]))


def _oai_final_resp(text: str) -> _FakeOAIResponse:
    return _FakeOAIResponse(_FakeMessage(content=text, tool_calls=None))


# ---------------------------------------------------------------------------
# Anthropic fake client helpers
# ---------------------------------------------------------------------------


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


class _RecordingAnthropicMessages:
    """Replays scripted responses and records every messages list passed in."""

    def __init__(self, responses: list[_AResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _AResponse:
        self.calls.append(list(kwargs.get("messages", [])))
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeAnthropicClient:
    def __init__(self, responses: list[_AResponse]) -> None:
        self._rec = _RecordingAnthropicMessages(responses)
        self.messages = self._rec

    @property
    def calls(self) -> list[list[dict[str, Any]]]:
        return self._rec.calls


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


def _anthropic_tool_resp(tc_id: str, expr: str) -> _AResponse:
    return _AResponse(
        "tool_use",
        [_ABlock("tool_use", block_id=tc_id, name="calc", input={"expr": expr})],
    )


def _anthropic_final_resp(text: str) -> _AResponse:
    return _AResponse("end_turn", [_ABlock("text", text=text)])


# ===========================================================================
# OpenAI tests
# ===========================================================================


@pytest.mark.asyncio
async def test_oai_none_resume_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) OpenAI — resume_messages=None behaves identically to pre-B1."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeOAIClient([
        _oai_tool_resp("c0", "1+1"),
        _oai_final_resp("answer is 2"),
    ])
    provider = _make_openai_provider(client)

    text, calls = await provider.complete_with_tools(
        user_text="compute 1+1",
        system_text="you are a calculator",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        resume_messages=None,
    )

    assert text == "answer is 2"
    assert len(calls) == 1

    # Default fresh path: system at [0], user at [1] in first call
    first_call_msgs = client.calls[0]
    assert first_call_msgs[0]["role"] == "system"
    assert first_call_msgs[-1]["role"] == "user"
    assert "compute 1+1" in first_call_msgs[-1]["content"]


@pytest.mark.asyncio
async def test_oai_resume_seeds_from_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) OpenAI — resume_messages seeds the loop; first LLM call sees the transcript."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Simulate a mid-loop transcript: system + 2 prior turns already in it
    prior_transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "you are a calculator"},
        {"role": "user", "content": "compute 1+1"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c0", "name": "calc", "input": {"expr": "1+1"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": "2"},
    ]

    client = _FakeOAIClient([_oai_final_resp("resumed: answer is 2")])
    provider = _make_openai_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="THIS SHOULD NOT APPEAR",
        system_text="THIS SHOULD NOT APPEAR",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
        resume_messages=prior_transcript,
    )

    assert text == "resumed: answer is 2"

    # First LLM call must see the restored transcript, not a fresh build
    first_call_msgs = client.calls[0]
    assert first_call_msgs == prior_transcript

    # user_text / system_text NOT re-injected — no double system message
    system_turns = [m for m in first_call_msgs if m.get("role") == "system"]
    assert len(system_turns) == 1, "exactly one system turn (no double-injection)"
    assert "THIS SHOULD NOT APPEAR" not in first_call_msgs[0]["content"]

    # on_iteration_complete fires and reports the continued state
    assert len(captured) == 1
    assert captured[0].iteration == 0


@pytest.mark.asyncio
async def test_oai_no_double_system_on_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) OpenAI — double system-prompt injection is prevented on resume."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    prior_transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "original system prompt"},
        {"role": "user", "content": "hello"},
    ]

    client = _FakeOAIClient([_oai_final_resp("hi")])
    provider = _make_openai_provider(client)

    await provider.complete_with_tools(
        user_text="new user text",
        system_text="second system — must not appear",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        resume_messages=prior_transcript,
    )

    first_call_msgs = client.calls[0]
    system_turns = [m for m in first_call_msgs if m.get("role") == "system"]
    # Only the one from prior_transcript — system_text not injected again
    assert len(system_turns) == 1
    assert system_turns[0]["content"] == "original system prompt"


@pytest.mark.asyncio
async def test_oai_resume_with_more_tool_iterations(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) extended — resume mid-loop and the loop continues dispatching tools."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    prior_transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "calc sys"},
        {"role": "user", "content": "original ask"},
    ]

    # After resume: one more tool call then a final answer
    client = _FakeOAIClient([
        _oai_tool_resp("c1", "2+2"),
        _oai_final_resp("final from resume"),
    ])
    provider = _make_openai_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="ignored",
        system_text=None,
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
        resume_messages=prior_transcript,
    )

    assert text == "final from resume"
    assert len(calls) == 1  # one new tool call dispatched post-resume

    # Two callback fires: iter 0 (tool call) + iter 1 (final)
    assert len(captured) == 2
    assert [s.iteration for s in captured] == [0, 1]

    # The running messages list grew from prior_transcript after the tool call
    assert len(captured[0].messages) > len(prior_transcript)


# ===========================================================================
# Anthropic tests
# ===========================================================================


@pytest.mark.asyncio
async def test_anthropic_none_resume_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) Anthropic — resume_messages=None behaves identically to pre-B1."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    client = _FakeAnthropicClient([
        _anthropic_tool_resp("tu0", "3*4"),
        _anthropic_final_resp("answer is 12"),
    ])
    provider = _make_anthropic_provider(client)

    text, calls = await provider.complete_with_tools(
        user_text="compute 3*4",
        system_text="you are a calculator",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        resume_messages=None,
    )

    assert text == "answer is 12"
    assert len(calls) == 1

    # Default fresh path: first message is user (Anthropic has system separate)
    first_call_msgs = client.calls[0]
    assert first_call_msgs[0]["role"] == "user"
    assert first_call_msgs[0]["content"] == "compute 3*4"


@pytest.mark.asyncio
async def test_anthropic_resume_seeds_from_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) Anthropic — resume_messages seeds the loop; first LLM call sees transcript."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Anthropic transcript: no system key (system stays in system_kwargs).
    # Two prior turns already processed.
    prior_transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "compute 3*4"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu0", "name": "calc", "input": {"expr": "3*4"}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu0", "content": "12"}]},
    ]

    client = _FakeAnthropicClient([_anthropic_final_resp("resumed: answer is 12")])
    provider = _make_anthropic_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="THIS SHOULD NOT APPEAR",
        system_text="you are a calculator",  # passed via system_kwargs, not messages
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
        resume_messages=prior_transcript,
    )

    assert text == "resumed: answer is 12"

    # First LLM call must see the restored transcript
    first_call_msgs = client.calls[0]
    assert first_call_msgs == prior_transcript

    # No system role in the messages list — Anthropic keeps it separate
    system_turns = [m for m in first_call_msgs if m.get("role") == "system"]
    assert len(system_turns) == 0, "Anthropic messages list has no system turn"

    # user_text not re-injected
    user_contents = [m["content"] for m in first_call_msgs if m.get("role") == "user"]
    assert all("THIS SHOULD NOT APPEAR" not in str(c) for c in user_contents)

    # on_iteration_complete fires and reports the continued state
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_anthropic_resume_with_more_tool_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) extended — Anthropic resume mid-loop; loop continues dispatching tools."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    prior_transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "original ask"},
    ]

    # After resume: one more tool_use then end_turn
    client = _FakeAnthropicClient([
        _anthropic_tool_resp("tu1", "5+5"),
        _anthropic_final_resp("final from anthropic resume"),
    ])
    provider = _make_anthropic_provider(client)

    captured: list[ReActIterationState] = []

    async def callback(state: ReActIterationState) -> None:
        captured.append(state)

    text, calls = await provider.complete_with_tools(
        user_text="ignored",
        system_text="sys",
        tool_schemas=_TOOL_SCHEMAS,
        tool_dispatcher=_dispatcher,
        on_iteration_complete=callback,
        resume_messages=prior_transcript,
    )

    assert text == "final from anthropic resume"
    assert len(calls) == 1

    # Two callbacks: iter 0 (tool call) + iter 1 (final)
    assert len(captured) == 2
    assert [s.iteration for s in captured] == [0, 1]

    # Running messages grew from prior_transcript
    assert len(captured[0].messages) > len(prior_transcript)


# ---------------------------------------------------------------------------
# D01.5 — the business requirement, on the production resume branch.
#
# "If my turn is interrupted mid-tool-call and I then send another message, the
#  assistant must answer my NEW message — not continue my old one."
#
# The production chain is durable/recovery.py -> execute.py -> the provider's
# resume branch -> validate_resume_transcript. The unit tests in
# test_resume_validation.py prove the RULE; these prove it is actually WIRED —
# that a bad checkpoint is refused inside the real provider call, BEFORE any API
# request goes out. A rule that is correct but unreached is the failure shape
# this program has hit repeatedly.
#
# Refused rather than repaired, and refused EARLY: the whole point of the
# validator's contract is a typed ResumeTranscriptError instead of an opaque 400
# — or worse, a 200 in which the model continues the user's sentence.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_refuses_a_resume_with_a_user_turn_after_a_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    interrupted: list[dict[str, Any]] = [
        {"role": "user", "content": "compute 3*4"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu0", "name": "calc",
                         "input": {"expr": "3*4"}}],
        },
        {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": "tu0", "content": "12"}]},
        # The interrupt: the user's NEXT message lands on the unclosed sequence.
        {"role": "user", "content": "actually, what's the weather?"},
    ]

    client = _FakeAnthropicClient([_anthropic_final_resp("should never be reached")])
    provider = _make_anthropic_provider(client)

    with pytest.raises(ResumeTranscriptError):
        await provider.complete_with_tools(
            user_text="",
            system_text="you are a calculator",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            resume_messages=interrupted,
        )

    assert client.calls == [], (
        "the malformed transcript reached the API — the refusal must happen "
        "BEFORE the request, or the model answers the wrong thing at full price"
    )


@pytest.mark.asyncio
async def test_anthropic_still_resumes_a_transcript_ending_on_a_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The jaw that keeps the fix honest.

    This is the NORMAL interrupted checkpoint — written after tool results are
    appended — and it must still resume. My first Rule 5 rejected exactly this
    and would have broken every legitimate resume.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    normal: list[dict[str, Any]] = [
        {"role": "user", "content": "compute 3*4"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu0", "name": "calc",
                         "input": {"expr": "3*4"}}],
        },
        {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": "tu0", "content": "12"}]},
    ]

    client = _FakeAnthropicClient([_anthropic_final_resp("resumed: answer is 12")])
    provider = _make_anthropic_provider(client)

    text, _ = await provider.complete_with_tools(
        user_text="", system_text="you are a calculator",
        tool_schemas=_TOOL_SCHEMAS, tool_dispatcher=_dispatcher,
        resume_messages=normal,
    )
    assert text == "resumed: answer is 12"
    assert client.calls, "a valid resume must still reach the provider"
