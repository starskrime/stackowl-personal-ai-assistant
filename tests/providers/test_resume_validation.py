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
from stackowl.providers.resume_validation import (
    CLOSING_TURN_TEXT,
    close_interrupted_tool_sequence,
    infer_provider_kind,
    validate_resume_transcript,
)

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


# ---------------------------------------------------------------------------
# D01.5 — a transcript ending ON a tool_result.
#
# THE GAP. Rule 3 catches a transcript ending on an assistant turn with an
# UNANSWERED tool call. Nothing catches the opposite tail: a transcript whose
# last turn IS the tool result. Rule 4 passes it (every call is answered) and
# Rule 3 passes it (the last turn declares no call), so it validates clean.
#
# WHY THAT MATTERS. anthropic_provider states checkpoints are "written after tool
# results are appended", so that tail is the NORMAL shape of an interrupted
# checkpoint — every /stop or budget-kill mid-tool-loop produces one. The
# reference platform hit this as a real incident: resuming from it and then
# appending the user's next message yields a `tool -> user` alternation which
# "strict providers (Gemini, Claude) reject, causing them to hallucinate a
# continuation of the user's message on the next turn"
# (the reference platform agent/turn_finalizer.py:278).
#
# StackOwl's PERSISTED history cannot produce this — record_turn has no role
# parameter and only ever writes user/assistant — so the exposure is the
# durable-resume path alone. Which is exactly what these cover.
# ---------------------------------------------------------------------------


def test_anthropic_user_message_after_a_tool_result_is_rejected() -> None:
    """The real defect: the ADJACENCY, not the tail.

    An Anthropic tool_result rides IN a user turn, so this is a user turn
    carrying results followed by an ordinary user turn — which is what an
    interrupted turn produces when the next user message lands on the unclosed
    tool sequence.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "what is 2+2?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "name": "calc", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "4"}],
        },
        {"role": "user", "content": "actually, never mind"},
    ]
    with pytest.raises(ResumeTranscriptError):
        validate_resume_transcript(messages, provider_kind="anthropic")


def test_openai_user_message_after_a_tool_message_is_ACCEPTED() -> None:
    """INVERTED 2026-08-30. This test used to assert the opposite, and was wrong.

    On the OpenAI wire a tool result is its own ``{"role": "tool"}`` message and
    ``tool -> user`` is an ordinary, legal sequence — it is precisely what this
    platform sends on every round that folds a directive in. Rejecting it killed
    a live task (trace ``recover-4e6044f0cde9``, 2026-08-30 03:20:22) and left 12
    more checkpoints in the live tasks table permanently unresumable, every one
    of them ``status='failed'``.

    The rule is right for Anthropic and wrong here; see the sibling test below.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "what is 2+2?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "calc", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "4"},
        {"role": "user", "content": "actually, never mind"},
    ]
    validate_resume_transcript(messages, provider_kind="openai")


def test_the_platforms_OWN_directive_shape_resumes() -> None:
    """The exact shape taken from a refused live checkpoint must resume.

    Read off ``task-a19fc46a563d`` in the live tasks table: a tool result, the
    spliced ``[steering]``/persistence directive as a user turn, and the model's
    answer to it. The loop built this, sent it, and the model answered — so the
    resume validator refusing it was the guard failing its own traffic. This is
    the regression test for that, driven by production data rather than an
    invented fixture.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "find the film"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "browse", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "HTTP 404"},
        {"role": "user", "content": "You have not yet delivered the requested outcome"},
        {"role": "assistant", "content": "You're right — let me take a different approach"},
    ]
    validate_resume_transcript(messages, provider_kind="openai")


def test_a_transcript_ENDING_on_a_tool_result_is_still_valid() -> None:
    """The jaw that caught my first formulation.

    A transcript ending on a tool result is the NORMAL resume seed — the model is
    called next and answers it. My first Rule 5 rejected this and broke three
    existing tests, which were right. Pinned here so the mistake is not repeated.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "what is 2+2?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "name": "calc", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "4"}],
        },
    ]
    validate_resume_transcript(messages, provider_kind="anthropic")  # must not raise


def test_a_tool_result_followed_by_an_assistant_turn_is_fine() -> None:
    """The other jaw: only the TAIL is the problem.

    A tool result mid-transcript with the assistant's answer after it is the
    normal, correct shape — rejecting that would break every legitimate resume.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "what is 2+2?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "name": "calc", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "4"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "It is 4."}]},
    ]
    validate_resume_transcript(messages, provider_kind="anthropic")  # must not raise


# ---------------------------------------------------------------------------
# D01.5's closer — the half that was scoped in brainstorm and never built.
# ---------------------------------------------------------------------------


def _anthropic_interrupted() -> list[dict[str, Any]]:
    """An Anthropic transcript whose user turn lands on an unclosed tool run."""
    return [
        {"role": "user", "content": "what is 2+2?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "calc", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "4"}]},
        {"role": "user", "content": "actually, never mind"},
    ]


def test_the_closer_makes_a_refused_transcript_resumable() -> None:
    """The whole point: repair, then the validator that used to kill it passes.

    Asserting both halves together is deliberate. A closer that runs but leaves
    the transcript still invalid, or a validator relaxed so far that it accepts
    the genuine defect, would each pass a narrower test.
    """
    messages = _anthropic_interrupted()
    with pytest.raises(ResumeTranscriptError):
        validate_resume_transcript(messages, provider_kind="anthropic")

    inserted = close_interrupted_tool_sequence(messages, provider_kind="anthropic")

    assert inserted == 1
    assert messages[3] == {"role": "assistant", "content": CLOSING_TURN_TEXT}
    validate_resume_transcript(messages, provider_kind="anthropic")


def test_the_closer_leaves_a_sound_transcript_alone() -> None:
    """No insertion, and the list is untouched — a repair that always fires is a bug."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "what is 2+2?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "calc", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "4"}]},
        {"role": "assistant", "content": "It is 4."},
    ]
    before = [dict(m) for m in messages]
    assert close_interrupted_tool_sequence(messages, provider_kind="anthropic") == 0
    assert messages == before


def test_the_closer_never_touches_an_openai_transcript() -> None:
    """`tool -> user` is legal on the OpenAI wire, so there is nothing to close."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "calc", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "4"},
        {"role": "user", "content": "a directive"},
    ]
    before = [dict(m) for m in messages]
    assert close_interrupted_tool_sequence(messages, provider_kind="openai") == 0
    assert messages == before


def test_the_closer_repairs_every_break_not_just_the_first() -> None:
    """Two breaks in one transcript. Walking forwards while inserting would skip one."""
    messages = _anthropic_interrupted() + [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu2", "name": "calc", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu2", "content": "9"}]},
        {"role": "user", "content": "and again"},
    ]
    assert close_interrupted_tool_sequence(messages, provider_kind="anthropic") == 2
    validate_resume_transcript(messages, provider_kind="anthropic")


def test_infer_provider_kind_reads_the_shape() -> None:
    """The load point has no provider yet; the transcript itself carries the answer."""
    assert infer_provider_kind([
        {"role": "tool", "tool_call_id": "c1", "content": "4"}]) == "openai"
    assert infer_provider_kind([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "4"}]}]) == "anthropic"
    # No tool results at all: nothing to repair, so the no-op kind is correct.
    assert infer_provider_kind([{"role": "user", "content": "hello"}]) == "openai"
