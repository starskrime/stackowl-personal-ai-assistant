"""Task 10/11 (concurrent-msg §5.1/§5.2) — FULL-CHAIN steering fold.

A review found the END-TO-END steering chain was tested only in disconnected
halves: ``test_callback_splice.py`` proves a provider folds a callback's returned
messages; ``test_steering_fold.py`` proves ``make_steering_callback`` drains a
real mailbox into a ``[steering]`` message. Neither connects the two through the
REAL execute-layer callback the orchestrator builds.

This test wires the WHOLE chain with only the LLM mocked:
  * a turn REGISTERED in a real ``TurnRegistry``,
  * a steering message put on its REAL ``steering_mailbox`` BEFORE the loop,
  * the REAL ``make_steering_callback(registry, request_id)`` (the exact factory
    execute.py uses) — NO hand-rolled callback,
  * a REAL ``OpenAIProvider.complete_with_tools`` loop driving the callback at
    each iteration boundary,
  * assert the ``[steering]`` message appears in the NEXT LLM iteration's
    ``messages`` — i.e. the mailbox → callback → provider-fold → next-LLM-call
    chain is observed end-to-end.

Mocks ONLY the OpenAI client (scripted, recording). Mirrors the recording-client
pattern from ``tests/providers/test_callback_splice.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.pipeline.steps.execute import make_steering_callback
from stackowl.providers.openai_provider import OpenAIProvider

_STEER_TEXT = "also include the security angle"


def _contains_steering(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, str) and _STEER_TEXT in content:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and _STEER_TEXT in str(block.get("text", "")):
                    return True
                if isinstance(block, str) and _STEER_TEXT in block:
                    return True
    return False


# --- scripted recording OpenAI client (mirrors test_callback_splice.py) ------ #


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


class _RecordingCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.seen_messages.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[self._idx]
        self._idx += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _RecordingCompletions) -> None:
        self.completions = completions


class _FakeOAIClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
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


def _tool_response(tc_id: str, query: str) -> _FakeResponse:
    tc = _FakeToolCall(tc_id, "web_search", f'{{"query":"{query}"}}')
    return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))


def _final_response(text: str) -> _FakeResponse:
    return _FakeResponse(_FakeMessage(content=text, tool_calls=None))


@pytest.mark.asyncio
async def test_real_mailbox_steer_folds_into_next_iteration_full_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    request_id = "trace-e2e-1"

    # 1. REAL registry + REAL turn registration (request_id == state.trace_id).
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        request_id, session_id="s1", task=bg, target=None, original_input="research X"
    )

    # 2. A steer arrives on the REAL mailbox BEFORE the loop's first boundary.
    turn.steering_mailbox.put_nowait(_STEER_TEXT)

    # 3. The REAL execute-layer steering callback (the exact factory execute.py
    #    wires). Reaches THIS turn's mailbox via reg.get(request_id).
    steering_cb = make_steering_callback(reg, request_id)
    assert steering_cb is not None  # registry wired → real callback

    # 4. REAL provider loop: iter 0 calls a tool → boundary drains the mailbox →
    #    iter 1 LLM call must see the [steering] message folded into messages.
    client = _FakeOAIClient([
        _tool_response("c0", "first"),
        _final_response("Done."),
    ])
    provider = _make_openai_provider(client)

    text, _calls = await asyncio.wait_for(
        provider.complete_with_tools(
            user_text="research X",
            system_text="sys",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=steering_cb,
        ),
        timeout=5.0,
    )

    assert text == "Done."
    seen = client.chat.completions.seen_messages
    assert len(seen) == 2
    # First LLM call (iteration 0) did NOT yet see the steer.
    assert not _contains_steering(seen[0])
    # Second LLM call (iteration 1) DID — the real mailbox drained through the
    # real callback and the real provider fold landed it on the live messages.
    assert _contains_steering(seen[1])
    # mailbox fully drained by the real callback (single steer consumed exactly once)
    assert turn.steering_mailbox.empty()

    await bg


@pytest.mark.asyncio
async def test_no_steer_pending_full_chain_folds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    request_id = "trace-e2e-2"
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    await reg.register(
        request_id, session_id="s2", task=bg, target=None, original_input="research X"
    )
    steering_cb = make_steering_callback(reg, request_id)
    assert steering_cb is not None

    client = _FakeOAIClient([
        _tool_response("c0", "first"),
        _final_response("Done."),
    ])
    provider = _make_openai_provider(client)

    text, _calls = await asyncio.wait_for(
        provider.complete_with_tools(
            user_text="research X",
            system_text="sys",
            tool_schemas=_TOOL_SCHEMAS,
            tool_dispatcher=_dispatcher,
            on_iteration_complete=steering_cb,
        ),
        timeout=5.0,
    )

    assert text == "Done."
    seen = client.chat.completions.seen_messages
    assert len(seen) == 2
    # Empty mailbox → callback returns None at every boundary → nothing folded.
    assert not _contains_steering(seen[1])

    await bg
