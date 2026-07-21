"""E5-D SMOKE — intent classification of a during-park reply (answer vs new-request).

A real backend turn parks on ``clarify``. A second message then goes through the
REAL ClarifyPump.resolve_or_rewrite wired with a REAL ClarifyIntentClassifier
(backed by a fake fast-tier provider returning ANSWER/NEW):

* NEW_REQUEST → the pump cancels the parked clarify (cancel_pending wakes the
  waiter with a DISTINCT CANCELLED outcome — the owl turn ends with a "set that
  question aside" result, NOT a timeout/assumption), and returns the message
  verbatim for a fresh turn — the user's pivot is NOT swallowed as the answer.
* ANSWER → the parked turn resumes in-place with the reply.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stackowl.gateway.clarify_pump import ClarifyPump
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.interaction.intent_classifier import ClarifyIntentClassifier
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.tools.interaction.clarify import ClarifyTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

SESSION = "42"


class _OwlProvider:
    """The owl turn: calls clarify (which parks), then echoes the tool result."""

    protocol = "anthropic"

    def __init__(self) -> None:
        self.script: list[tuple[str, dict]] = []
        self.results: list[str] = []

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher,
        history=None, persistence_check=None, **kwargs,
    ):
        name, args = self.script.pop(0)
        out = await tool_dispatcher(name, args)
        self.results.append(out)
        return (f"-> {out}", [{"name": name, "args": args, "result": out}])

    async def complete(self, *a, **k):  # pragma: no cover
        return SimpleNamespace(content="")

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""


class _OwlRegistry:
    def __init__(self, p: _OwlProvider) -> None:
        self._p = p

    def get(self, name: str) -> _OwlProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _OwlProvider:
        return self._p

    def get_with_cascade(self, tier: str) -> _OwlProvider:
        return self._p


class _FastProvider:
    """The classifier's fast-tier model — returns a settable one-word verdict."""

    def __init__(self) -> None:
        self.verdict = "ANSWER"

    async def complete(self, messages, model, **kwargs):  # noqa: ANN001
        return SimpleNamespace(content=self.verdict)


class _FastRegistry:
    def __init__(self, p: _FastProvider) -> None:
        self._p = p

    def get_by_tier(self, tier: str) -> tuple[_FastProvider, str]:
        return self._p, "fake-fast-model"


def _park_a_clarify_turn(owl: _OwlProvider, gateway: ClarifyGateway) -> tuple[AsyncioBackend, StreamRegistry]:
    registry = ToolRegistry.with_defaults()
    registry.register(ClarifyTool(timeout_s=30.0), replace=True)
    services = StepServices(
        provider_registry=_OwlRegistry(owl),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        stream_registry=StreamRegistry(),
        clarify_gateway=gateway,
    )
    return AsyncioBackend(services=services), services.stream_registry  # type: ignore[return-value]


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id=SESSION, input_text="help", channel="cli",
        owl_name="default", pipeline_step="start", interactive=True,
    )


async def _wait_until(predicate, *, tries: int = 300) -> bool:  # noqa: ANN001
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_new_request_during_park_cancels_clarify_and_runs_fresh() -> None:
    owl = _OwlProvider()
    gateway = ClarifyGateway()
    fast = _FastProvider()
    fast.verdict = "NEW"  # the reply is an unrelated new request
    classifier = ClarifyIntentClassifier(_FastRegistry(fast))  # type: ignore[arg-type]
    pump = ClarifyPump(gateway, StreamRegistry(), classifier)

    backend, stream_registry = _park_a_clarify_turn(owl, gateway)
    stream_registry.create("t")  # request_id == _state().trace_id (DELIBERATE §4.1 re-key)
    owl.script.append(("clarify", {"question": "Which colour?", "choices": ["red", "blue"]}))
    run_task = asyncio.create_task(backend.run(_state()))

    parked = await _wait_until(lambda: gateway.peek_for_session(SESSION, "cli") is not None)
    assert parked and not run_task.done(), "clarify should be parked"

    # The user pivots to an unrelated request instead of answering.
    consumed, text = await pump.resolve_or_rewrite(
        session_id=SESSION, channel="cli", route="owl", target="default",
        input_text="actually, what's the weather?",
    )
    assert consumed is False                              # not swallowed as the answer
    assert text == "actually, what's the weather?"        # runs as a fresh turn verbatim

    # The parked turn was cancelled gracefully and ends. The owl got the DISTINCT
    # CANCELLED ("set that question aside") result — NOT a timeout/assumption — and
    # never saw the unrelated message as the answer.
    await asyncio.wait_for(run_task, timeout=5.0)
    assert gateway.peek_for_session(SESSION, "cli") is None  # clarify cleared
    assert "what's the weather" not in owl.results[0]        # owl never saw it as the answer
    assert "setting that question aside" in owl.results[0]    # _CANCELLED, not _TIMED_OUT
    assert "did not reply in time" not in owl.results[0]      # NOT the timeout/assumption text


async def test_answer_during_park_resolves_turn() -> None:
    owl = _OwlProvider()
    gateway = ClarifyGateway()
    fast = _FastProvider()
    fast.verdict = "ANSWER"  # the reply answers the question
    classifier = ClarifyIntentClassifier(_FastRegistry(fast))  # type: ignore[arg-type]
    pump = ClarifyPump(gateway, StreamRegistry(), classifier)

    backend, stream_registry = _park_a_clarify_turn(owl, gateway)
    stream_registry.create("t")  # request_id == _state().trace_id (DELIBERATE §4.1 re-key)
    owl.script.append(("clarify", {"question": "Which colour?", "choices": ["red", "blue"]}))
    run_task = asyncio.create_task(backend.run(_state()))

    parked = await _wait_until(lambda: gateway.peek_for_session(SESSION, "cli") is not None)
    assert parked and not run_task.done()

    consumed, _text = await pump.resolve_or_rewrite(
        session_id=SESSION, channel="cli", route="owl", target="default", input_text="blue",
    )
    assert consumed is True  # blocking resume — the parked turn handles it

    await asyncio.wait_for(run_task, timeout=5.0)
    assert "blue" in owl.results[0]  # the answer reached the model in the same turn
