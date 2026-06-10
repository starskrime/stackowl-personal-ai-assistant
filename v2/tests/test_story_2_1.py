"""Story 2.1 tests — Supervisor, Pipeline Skeleton & asyncio Backend."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.exceptions import A2ATimeoutError
from stackowl.infra.clock import Clock
from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry
from stackowl.supervisor.supervisor import SupervisedTask, Supervisor, make_supervised_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """Monotonic clock that advances on sleep; async_sleep yields once then returns."""

    def __init__(self) -> None:
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:
        self._t += seconds
        await asyncio.sleep(0)


def _make_state(**kwargs: Any) -> PipelineState:
    defaults: dict[str, Any] = {
        "trace_id": "trace-001",
        "session_id": "sess-001",
        "input_text": "hello",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "",
    }
    defaults.update(kwargs)
    return PipelineState(**defaults)


# ---------------------------------------------------------------------------
# PipelineState
# ---------------------------------------------------------------------------


def test_pipeline_state_evolve_creates_new_instance() -> None:
    s = _make_state()
    s2 = s.evolve(input_text="updated")
    assert s2.input_text == "updated"
    assert s.input_text == "hello"


def test_pipeline_state_evolve_is_immutable() -> None:
    s = _make_state()
    s2 = s.evolve(errors=("oops",))
    assert s.errors == ()
    assert s2.errors == ("oops",)


def test_pipeline_state_tuple_fields_default_empty() -> None:
    s = _make_state()
    assert s.responses == ()
    assert s.tool_calls == ()
    assert s.errors == ()
    assert s.memory_context is None


def test_tool_call_frozen() -> None:
    tc = ToolCall(tool_name="shell", args={"cmd": "ls"}, result="file.txt", error=None, duration_ms=42.0)
    assert tc.tool_name == "shell"
    with pytest.raises(Exception):
        tc.tool_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def test_stream_writer_reader_roundtrip() -> None:
    registry = StreamRegistry()
    writer, reader = registry.create("sess-1")

    chunk = ResponseChunk(
        content="hello", is_final=False, chunk_index=0, trace_id="t1", owl_name="secretary"
    )
    await writer.write(chunk)
    await writer.close()

    received = []
    async for c in reader:
        received.append(c)

    assert len(received) == 1
    assert received[0].content == "hello"


async def test_stream_registry_get_writer() -> None:
    registry = StreamRegistry()
    registry.create("sess-x")
    assert registry.get_writer("sess-x") is not None
    assert registry.get_writer("sess-missing") is None


async def test_stream_registry_remove() -> None:
    registry = StreamRegistry()
    registry.create("sess-y")
    registry.remove("sess-y")
    assert registry.get_writer("sess-y") is None


# ---------------------------------------------------------------------------
# A2AQueue
# ---------------------------------------------------------------------------


async def test_a2a_queue_send_and_receive() -> None:
    queue = A2AQueue()
    msg = A2AMessage.now(
        from_owl="secretary",
        to_owl="research",
        content="look this up",
        message_type="request",
        trace_id="t1",
    )
    queue.send(msg)
    received = await queue.receive("research", timeout=1.0)
    assert received.content == "look this up"


async def test_a2a_queue_timeout_raises() -> None:
    queue = A2AQueue()
    with pytest.raises(A2ATimeoutError) as exc_info:
        await queue.receive("nobody", timeout=0.05)
    assert exc_info.value.owl_name == "nobody"


async def test_a2a_queue_depth() -> None:
    queue = A2AQueue()
    assert queue.queue_depth("owl1") == 0
    for i in range(3):
        queue.send(A2AMessage.now(from_owl="a", to_owl="owl1", content=str(i), message_type="event", trace_id="t"))
    assert queue.queue_depth("owl1") == 3


async def test_a2a_message_now_sets_timestamp() -> None:
    msg = A2AMessage.now(from_owl="a", to_owl="b", content="hi", message_type="request", trace_id="t")
    assert msg.timestamp.endswith("+00:00")


# ---------------------------------------------------------------------------
# AsyncioBackend
# ---------------------------------------------------------------------------


async def test_asyncio_backend_is_orchestrator_backend() -> None:
    backend = AsyncioBackend()
    assert isinstance(backend, OrchestratorBackend)


async def test_asyncio_backend_runs_all_steps() -> None:
    backend = AsyncioBackend()
    state = _make_state()
    result = await backend.run(state)
    assert result.pipeline_step == "deliver"
    assert result.errors == ()


async def test_asyncio_backend_propagates_trace_id() -> None:
    backend = AsyncioBackend()
    state = _make_state(trace_id="my-trace")
    result = await backend.run(state)
    assert result.trace_id == "my-trace"


async def test_asyncio_backend_step_error_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a step raises, the error is captured in state.errors; pipeline continues."""
    from stackowl.pipeline import registry

    async def boom(state: PipelineState) -> PipelineState:
        raise RuntimeError("classify exploded")

    import stackowl.pipeline.backends.asyncio_backend as be_mod

    patched = [(name, boom if name == "classify" else fn) for name, fn in registry.PIPELINE_STEPS]
    monkeypatch.setattr(be_mod, "PIPELINE_STEPS", patched)

    backend = AsyncioBackend()
    result = await backend.run(_make_state())
    assert any("classify" in e for e in result.errors)
    assert result.pipeline_step == "deliver"


async def test_asyncio_backend_deliver_writes_to_registry() -> None:
    registry = StreamRegistry()
    # DELIBERATE re-key (§4.1): deliver resolves the writer by request_id
    # (state.trace_id), not session_id. Register under the turn's trace_id and tag
    # the chunk with the same request_id so it is delivered (not hard-dropped).
    request_id = "trace-001"  # == _make_state default trace_id
    writer, reader = registry.create(request_id)

    chunk = ResponseChunk(
        content="world", is_final=False, chunk_index=0, trace_id=request_id, owl_name="secretary"
    )
    state = _make_state(session_id="sess-deliver", responses=(chunk,))

    backend = AsyncioBackend(services=StepServices(stream_registry=registry))
    await backend.run(state)

    received: list[ResponseChunk] = []
    async for c in reader:
        received.append(c)
    assert received[0].content == "world"


async def test_asyncio_backend_deliver_no_writer_logs_warning(capture_logs: list[dict]) -> None:
    registry = StreamRegistry()
    state = _make_state(session_id="sess-no-writer")
    backend = AsyncioBackend(services=StepServices(stream_registry=registry))
    result = await backend.run(state)
    assert result.errors == ()
    warning_msgs = [r["msg"] for r in capture_logs if "no writer" in r.get("msg", "")]
    assert warning_msgs


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


async def test_supervisor_health_initial_stopped() -> None:
    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("t1", AsyncMock(return_value=None)))
    assert sup.health() == {"t1": "stopped"}


async def test_supervisor_starts_task() -> None:
    ran = asyncio.Event()

    async def _work() -> None:
        ran.set()
        await asyncio.sleep(1000)

    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("worker", _work))
    await sup.start()
    await asyncio.wait_for(ran.wait(), timeout=1.0)
    assert sup.health()["worker"] == "running"
    await sup.stop()


async def test_supervisor_marks_failed_after_5_failures() -> None:
    call_count = 0

    async def _always_fail() -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("failing", _always_fail))
    await sup.start()

    async def _wait_failed() -> None:
        while sup.health().get("failing") != "failed":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_failed(), timeout=5.0)
    assert sup.health()["failing"] == "failed"
    assert call_count >= 5


async def test_supervisor_stop_marks_stopped() -> None:
    async def _long_running() -> None:
        await asyncio.sleep(1000)

    sup = Supervisor(clock=FakeClock())
    sup.register(make_supervised_task("long", _long_running))
    await sup.start()
    await sup.stop()
    assert sup.health()["long"] == "stopped"
