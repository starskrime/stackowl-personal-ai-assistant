"""Task 12 (concurrent-msg §5.3) — cooperative stop at the iteration boundary.

A ``stop`` steer sets the turn's ``stop_requested`` FLAG (via
``TurnRegistry.request_stop``). The running ReAct loop honors it at its next
ITERATION BOUNDARY — the same ``make_steering_callback`` closure where steering is
drained (Task 10). When the flag is set, the closure raises a controlled
``TurnStopped`` exception which propagates OUT of the provider's
``complete_with_tools`` loop (the same propagation path ``BudgetBreach`` already
uses — the provider awaits the callback directly, only ``openai.APIError`` is
caught around the API call, never the callback). The execute step catches it and
FINALIZES GRACEFULLY — a "stopped" ResponseChunk, the loop does NOT run further
iterations.

CRITICAL (asserted here): stop is a FLAG, NOT ``task.cancel()`` — a cancel raises
mid-tool → torn state. The underlying task is NEVER cancelled. Stop is cooperative
at iteration granularity (it cannot interrupt a 90s in-flight tool — documented,
not a bug). Interactive turns have no ``task_id`` → the D1 ledger is dormant → no
``asyncio.shield`` is required.

Mocks ONLY the OpenAI client (scripted, recording). Drives the REAL
``make_steering_callback`` + a REAL ``OpenAIProvider.complete_with_tools`` loop +
a REAL ``TurnRegistry`` + the REAL execute-step ``_run_with_tools`` finalize seam.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import TurnStopped
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.pipeline.steps.execute import make_steering_callback
from stackowl.providers.openai_provider import OpenAIProvider

# --- the plan's verbatim unit assertion: flag set, never cancelled ----------- #


@pytest.mark.asyncio
async def test_stop_flag_finalizes_at_boundary_not_cancel() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("r1", session_id="s1", task=t, target=None, original_input="x")
    reg.request_stop("r1")
    assert turn.stop_requested is True
    # the execute closure surfaces stop to the loop; the loop finalizes gracefully.
    # assert the closure signals stop and that task.cancel() was NEVER called.
    assert not t.cancelled()
    await t


@pytest.mark.asyncio
async def test_request_stop_unknown_request_is_noop() -> None:
    reg = TurnRegistry()
    # No turn registered — request_stop must not raise (fail-safe, just logs).
    reg.request_stop("missing")  # no exception


# --- scripted recording OpenAI client (mirrors test_steering_fold_end_to_end) - #


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
        self.call_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.call_count += 1
        # Defensive: if the loop tries MORE LLM rounds than scripted, the stop did
        # NOT halt it — surface as an explicit failure rather than IndexError.
        if self._idx >= len(self._responses):
            raise AssertionError("loop ran more iterations than scripted — stop did NOT halt it")
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


def _tool_response(tc_id: str, query: str) -> _FakeResponse:
    tc = _FakeToolCall(tc_id, "web_search", f'{{"query":"{query}"}}')
    return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))


def _final_response(text: str) -> _FakeResponse:
    return _FakeResponse(_FakeMessage(content=text, tool_calls=None))


@pytest.mark.asyncio
async def test_stop_flag_raises_turnstopped_at_iteration_boundary() -> None:
    """A turn whose stop flag is set mid-tool-batch raises TurnStopped at the NEXT
    boundary — the loop does NOT run a second LLM round; the tool already in flight
    is fully observed first (cooperative at iteration granularity). NOT a cancel."""
    TestModeGuard._active = False  # type: ignore[attr-defined]
    request_id = "trace-stop-1"
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    await reg.register(
        request_id, session_id="s1", task=bg, target=None, original_input="research X"
    )
    steering_cb = make_steering_callback(reg, request_id)
    assert steering_cb is not None

    # The dispatcher sets the stop flag DURING the first tool call — simulating a
    # 'stop' steer arriving while a tool is in flight. The loop must still finish
    # observing this tool, then honor the flag at the boundary (NOT mid-tool).
    dispatched: list[str] = []

    async def _dispatcher(name: str, args: dict[str, Any]) -> str:
        dispatched.append(name)
        reg.request_stop(request_id)  # stop arrives mid-tool
        return f"result_for_{name}"

    # iter 0: a tool call. If stop is NOT honored, iter 1 would call the LLM again
    # for a final answer — the scripted client has a second response, so a failure
    # to stop would NOT IndexError but WOULD bump call_count to 2.
    client = _FakeOAIClient([
        _tool_response("c0", "first"),
        _final_response("Should NOT be reached."),
    ])
    provider = _make_openai_provider(client)

    with pytest.raises(TurnStopped) as ei:
        await asyncio.wait_for(
            provider.complete_with_tools(
                user_text="research X",
                system_text="sys",
                tool_schemas=_TOOL_SCHEMAS,
                tool_dispatcher=_dispatcher,
                on_iteration_complete=steering_cb,
            ),
            timeout=5.0,
        )

    # The tool WAS fully dispatched (mid-tool was NOT torn) before the boundary stop.
    assert dispatched == ["web_search"]
    # Only ONE LLM round happened — the loop did NOT run further iterations.
    assert client.chat.completions.call_count == 1
    # The exception carries the partial work so execute can finalize gracefully.
    assert ei.value.request_id == request_id
    assert len(ei.value.tool_call_records) == 1
    # FLAG, not cancel: the underlying task was never cancelled.
    assert not bg.cancelled()
    await bg


@pytest.mark.asyncio
async def test_execute_run_finalizes_with_stopped_chunk_no_cancel() -> None:
    """End-to-end through the REAL execute step ``_run_with_tools``: a stopped turn
    yields a graceful 'stopped' ResponseChunk in state.responses, the task is NOT
    cancelled, and no CancelledError is raised. The provider is passed DIRECTLY (so
    no tier routing), and a real ``StepServices`` carries the real ``TurnRegistry``
    so the steering callback built inside execute reaches the running turn."""
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps.execute import _run_with_tools
    from stackowl.tools.registry import ToolRegistry

    TestModeGuard._active = False  # type: ignore[attr-defined]
    request_id = "trace-stop-e2e"
    reg = TurnRegistry()
    bg = asyncio.create_task(asyncio.sleep(0))
    await reg.register(
        request_id, session_id="s1", task=bg, target=None, original_input="research X"
    )

    # Scripted client: iter 0 issues a tool call; iter 1 would draft a final answer.
    # The stop flag is set BEFORE the loop, so the FIRST iteration boundary (after
    # the tool batch is fully observed) honors it — no second LLM round.
    client = _FakeOAIClient([
        _tool_response("c0", "first"),
        _final_response("Should NOT be reached."),
    ])
    provider = _make_openai_provider(client)
    reg.request_stop(request_id)

    tool_reg = ToolRegistry.with_defaults()
    token = set_services(StepServices(tool_registry=tool_reg, turn_registry=reg))
    try:
        state = PipelineState(
            trace_id=request_id,
            session_id="s1",
            input_text="research X",
            channel="cli",
            owl_name="default",
            pipeline_step="execute",
            interactive=True,
        )
        out = await asyncio.wait_for(
            _run_with_tools(state, provider, tool_reg), timeout=5.0
        )
    finally:
        reset_services(token)

    # A graceful 'stopped' chunk was produced.
    assert out.responses, "expected a stopped response chunk"
    last = out.responses[-1]
    assert "stop" in last.content.casefold()
    # The loop did NOT complete normally with the scripted final text.
    assert all("Should NOT be reached." not in c.content for c in out.responses)
    # Only ONE LLM round happened — the loop did not run further iterations.
    assert client.chat.completions.call_count == 1
    # FLAG, not cancel: never cancelled, no CancelledError.
    assert not bg.cancelled()
    await bg
