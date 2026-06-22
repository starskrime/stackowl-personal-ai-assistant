"""Progress emitter — emission order, vocabulary, and gating (byte-identical)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from stackowl.config.progress_settings import ProgressSettings
from stackowl.pipeline.progress.emitter import (
    PIPELINE_STEP_EVENT,
    emit_start,
    is_eligible,
    make_progress_callback,
)
from stackowl.pipeline.state import PipelineState
from stackowl.providers.react_callback import ReActIterationState


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, payload: Any = None) -> None:
        self.events.append((event, payload))


class _FakeManifest:
    def __init__(self, progress_key: str | None) -> None:
        self.progress_key = progress_key


class _FakeTool:
    def __init__(self, progress_key: str | None) -> None:
        self.manifest = _FakeManifest(progress_key)


class _FakeRegistry:
    def __init__(self, mapping: dict[str, str | None]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> _FakeTool | None:
        if name not in self._mapping:
            return None
        return _FakeTool(self._mapping[name])


class _FakeWriter:
    def __init__(self) -> None:
        self.written: list[Any] = []

    async def write(self, chunk: Any) -> None:
        self.written.append(chunk)


class _FakeStreamRegistry:
    def __init__(self, writer: _FakeWriter | None) -> None:
        self._writer = writer

    def get_writer(self, request_id: str) -> _FakeWriter | None:
        return self._writer


def _state(**over: Any) -> PipelineState:
    base: dict[str, Any] = dict(
        trace_id="t1",
        session_id="s1",
        input_text="hi",
        channel="telegram",
        owl_name="Athena",
        pipeline_step="execute",
        interactive=True,
        reply_target=12345,
        language="en",
    )
    base.update(over)
    return PipelineState(**base)


def _services(*, bus: _FakeBus | None = None, registry: _FakeRegistry | None = None,
             live: bool = True, stream: _FakeStreamRegistry | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(progress=ProgressSettings(live_progress=live)),
        event_bus=bus,
        tool_registry=registry,
        stream_registry=stream,
    )


def _step_names(bus: _FakeBus) -> list[str]:
    return [p["step_name"] for (e, p) in bus.events if e == PIPELINE_STEP_EVENT]


def test_emits_start_tool_recover_and_synth_in_order() -> None:
    bus = _FakeBus()
    registry = _FakeRegistry({"web_search": "SEARCH_WEB", "fetch": "READ_WEB"})
    cb = make_progress_callback(_state(), _services(bus=bus, registry=registry))
    assert cb is not None

    async def drive() -> None:
        await emit_start(cb)  # "Working on it…"
        # iteration 0: one successful web_search
        await cb(ReActIterationState(
            iteration=0,
            tool_call_records=[{"name": "web_search", "args": {}, "result": "ok", "failed": False}],
        ))
        # iteration 1: a second call that FAILED → recover
        await cb(ReActIterationState(
            iteration=1,
            tool_call_records=[
                {"name": "web_search", "args": {}, "result": "ok", "failed": False},
                {"name": "fetch", "args": {}, "result": "", "failed": True},
            ],
        ))
        # iteration 2: model produced text, no new tool calls → synthesizing
        await cb(ReActIterationState(iteration=2, tool_call_records=[
            {"name": "web_search", "args": {}, "result": "ok", "failed": False},
            {"name": "fetch", "args": {}, "result": "", "failed": True},
        ]))

    asyncio.run(drive())

    names = _step_names(bus)
    assert "Working on it" in names[0]
    assert "Searching the web" in names[1]
    assert "trying another way" in names[2]  # RECOVER copy
    assert "Writing your answer" in names[3]  # SYNTH copy


def test_unknown_tool_never_leaks_name() -> None:
    bus = _FakeBus()
    registry = _FakeRegistry({})  # nothing mapped
    cb = make_progress_callback(_state(), _services(bus=bus, registry=registry))
    assert cb is not None
    asyncio.run(cb(ReActIterationState(
        iteration=0,
        tool_call_records=[{"name": "mcp__plugin_x__secret_tool", "args": {}, "failed": False}],
    )))
    names = _step_names(bus)
    assert names, "expected a progress emit"
    assert "secret_tool" not in names[0]
    assert "Thinking" in names[0]


def test_progress_chunk_written_to_stream_with_kind_progress() -> None:
    writer = _FakeWriter()
    registry = _FakeRegistry({"web_search": "SEARCH_WEB"})
    svc = _services(
        bus=_FakeBus(), registry=registry, stream=_FakeStreamRegistry(writer)
    )
    cb = make_progress_callback(_state(reply_target=999), svc)
    assert cb is not None
    asyncio.run(cb(ReActIterationState(
        iteration=0,
        tool_call_records=[{"name": "web_search", "args": {}, "failed": False}],
    )))
    assert writer.written, "expected a progress chunk on the stream"
    chunk = writer.written[0]
    assert chunk.kind == "progress"
    assert chunk.is_final is False
    assert chunk.target == 999
    assert "Searching the web" in chunk.content


def test_callback_always_returns_none_observe_only() -> None:
    cb = make_progress_callback(_state(), _services(bus=_FakeBus(), registry=_FakeRegistry({})))
    assert cb is not None
    out = asyncio.run(cb(ReActIterationState(iteration=0, tool_call_records=[])))
    assert out is None


# --- gating: every disqualifier ⇒ no callback (byte-identical baseline) ------ #


def test_gated_when_flag_off() -> None:
    assert make_progress_callback(_state(), _services(live=False)) is None


def test_gated_for_delegated_child() -> None:
    assert make_progress_callback(_state(delegation_depth=1), _services()) is None


def test_gated_for_deferred_delivery() -> None:
    assert make_progress_callback(_state(defer_delivery=True), _services()) is None


def test_gated_for_non_interactive() -> None:
    assert make_progress_callback(_state(interactive=False), _services()) is None


def test_gated_when_no_target_and_not_cli() -> None:
    assert make_progress_callback(_state(reply_target=None, channel="telegram"), _services()) is None


def test_cli_eligible_without_target() -> None:
    s = _state(reply_target=None, channel="cli")
    assert is_eligible(s, _services())
    assert make_progress_callback(s, _services()) is not None


def test_emit_start_on_none_callback_is_noop() -> None:
    asyncio.run(emit_start(None))  # must not raise
