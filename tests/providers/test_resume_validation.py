"""B1 hardening — resume-transcript validator + all_calls rehydration.

Covers:
  * validate_resume_transcript raises ResumeTranscriptError for: empty list;
    an anthropic transcript containing a system-role message; a transcript
    ending in a dangling assistant tool_use/tool_calls with no result; a
    transcript with an unmatched tool_use.
  * validate_resume_transcript PASSES for a well-formed transcript (matched
    pairs, proper last turn) on both provider kinds.
  * A resumed run with resume_tool_calls provided returns all_calls that
    INCLUDES the prior calls (proving the persistence give-up judge sees full
    history, not just post-resume work).

The provider-loop assertion reuses the fake-client harness style from
test_resume_messages.py — no real network.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import ResumeTranscriptError
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.resume_validation import validate_resume_transcript

# ---------------------------------------------------------------------------
# Validator — failure cases
# ---------------------------------------------------------------------------


def test_empty_transcript_raises() -> None:
    with pytest.raises(ResumeTranscriptError, match="empty"):
        validate_resume_transcript([], provider_kind="openai")
    with pytest.raises(ResumeTranscriptError, match="empty"):
        validate_resume_transcript([], provider_kind="anthropic")


def test_anthropic_system_role_raises() -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "you are a calculator"},
        {"role": "user", "content": "hi"},
    ]
    with pytest.raises(ResumeTranscriptError, match="system-role"):
        validate_resume_transcript(transcript, provider_kind="anthropic")


def test_openai_dangling_last_assistant_tool_call_raises() -> None:
    """Last turn is an OpenAI assistant tool_call with no following tool result."""
    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "compute"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call_9", "type": "function", "function": {"name": "calc", "arguments": "{}"}}
            ],
        },
    ]
    with pytest.raises(ResumeTranscriptError) as exc_info:
        validate_resume_transcript(transcript, provider_kind="openai")
    assert "call_9" in str(exc_info.value)
    assert exc_info.value.dangling_ids == ["call_9"]


def test_anthropic_dangling_last_assistant_tool_use_raises() -> None:
    """Last turn is an Anthropic assistant tool_use with no following tool_result."""
    transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "compute"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu7", "name": "calc", "input": {}}],
        },
    ]
    with pytest.raises(ResumeTranscriptError) as exc_info:
        validate_resume_transcript(transcript, provider_kind="anthropic")
    assert "tu7" in str(exc_info.value)
    assert exc_info.value.dangling_ids == ["tu7"]


def test_unmatched_tool_use_midstream_raises() -> None:
    """A tool_use in the middle whose id is never answered, even though the
    last turn is well-formed (a different matched pair)."""
    transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "compute"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_unmatched", "name": "calc", "input": {}}],
        },
        # answers a DIFFERENT id, leaving tu_unmatched dangling
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_other", "content": "x"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_other", "name": "calc", "input": {}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_other", "content": "y"}]},
    ]
    with pytest.raises(ResumeTranscriptError) as exc_info:
        validate_resume_transcript(transcript, provider_kind="anthropic")
    assert "tu_unmatched" in exc_info.value.dangling_ids


def test_openai_unmatched_tool_call_raises() -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call_x", "type": "function", "function": {"name": "calc", "arguments": "{}"}}
            ],
        },
        # answered with the WRONG id, and a well-formed final user turn follows
        {"role": "tool", "tool_call_id": "call_y", "content": "z"},
        {"role": "user", "content": "and now?"},
    ]
    with pytest.raises(ResumeTranscriptError) as exc_info:
        validate_resume_transcript(transcript, provider_kind="openai")
    assert "call_x" in exc_info.value.dangling_ids


# ---------------------------------------------------------------------------
# Validator — passing cases
# ---------------------------------------------------------------------------


def test_well_formed_anthropic_transcript_passes() -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "user", "content": "compute 3*4"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu0", "name": "calc", "input": {"expr": "3*4"}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu0", "content": "12"}]},
    ]
    # last turn is a tool_result user turn — proper, no dangling call
    validate_resume_transcript(transcript, provider_kind="anthropic")  # must not raise


def test_well_formed_openai_transcript_passes() -> None:
    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "compute"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "call_0", "type": "function", "function": {"name": "calc", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "2"},
    ]
    validate_resume_transcript(transcript, provider_kind="openai")  # must not raise


def test_plain_user_only_transcript_passes() -> None:
    """A bare user turn (no tool calls at all) is a valid resume seed."""
    validate_resume_transcript(
        [{"role": "user", "content": "hi"}], provider_kind="openai"
    )
    validate_resume_transcript(
        [{"role": "user", "content": "hi"}], provider_kind="anthropic"
    )


# ---------------------------------------------------------------------------
# all_calls rehydration — provider-level proof
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
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
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
    def __init__(self, responses: list[_FakeOAIResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.persistence_args: list[list[str]] = []

    async def create(self, **kwargs: Any) -> _FakeOAIResponse:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _RecordingCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeOAIResponse]) -> None:
        self.chat = _FakeChat(_RecordingCompletions(responses))


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


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return f"result_for_{name}"


@pytest.mark.asyncio
async def test_resume_tool_calls_rehydrates_all_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed run returns all_calls = prior + new (the persistence judge sees
    the FULL history, not just post-resume calls)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    prior_transcript: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "ask"},
    ]
    prior_tool_calls: list[dict[str, Any]] = [
        {"id": "old_1", "name": "calc", "args": {"expr": "1+1"}, "result": "2", "failed": False},
        {"id": "old_2", "name": "calc", "args": {"expr": "2+2"}, "result": "4", "failed": False},
    ]

    # Post-resume: one NEW tool call then a final answer.
    tc = _FakeToolCall("new_1", "calc", '{"expr":"9+9"}')
    client = _FakeOAIClient([
        _FakeOAIResponse(_FakeMessage(content=None, tool_calls=[tc])),
        _FakeOAIResponse(_FakeMessage(content="done", tool_calls=None)),
    ])
    provider = _make_openai_provider(client)

    # Capture what the persistence judge sees (summarized prior+new outcomes).
    judge_saw: list[list[str]] = []

    async def persistence_check(draft: str, outcomes: list[str]) -> str | None:
        judge_saw.append(list(outcomes))
        return None  # accept

    text, calls = await provider.complete_with_tools(
        user_text="ignored",
        system_text=None,
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "calc",
                    "description": "calc",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_dispatcher=_dispatcher,
        persistence_check=persistence_check,
        resume_messages=prior_transcript,
        resume_tool_calls=prior_tool_calls,
    )

    assert text == "done"

    # Returned all_calls includes BOTH prior calls AND the new one.
    returned_ids = [c["id"] for c in calls]
    assert returned_ids == ["old_1", "old_2", "new_1"], returned_ids
    assert len(calls) == 3

    # The persistence give-up judge was handed the FULL history (>= 3 outcomes),
    # proving it cannot wrongly nudge give-up for lack of prior work.
    assert judge_saw, "persistence_check should have been invoked"
    assert len(judge_saw[-1]) == 3


@pytest.mark.asyncio
async def test_no_resume_tool_calls_starts_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (resume_tool_calls=None) => all_calls starts empty (unchanged)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    tc = _FakeToolCall("only_1", "calc", "{}")
    client = _FakeOAIClient([
        _FakeOAIResponse(_FakeMessage(content=None, tool_calls=[tc])),
        _FakeOAIResponse(_FakeMessage(content="done", tool_calls=None)),
    ])
    provider = _make_openai_provider(client)

    _text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "calc",
                    "description": "calc",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_dispatcher=_dispatcher,
    )

    assert [c["id"] for c in calls] == ["only_1"]
