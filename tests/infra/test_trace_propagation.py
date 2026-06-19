"""Tests for trace_id propagation: channel → PipelineState → TraceContext → logs.

Covers the bug where every log record carried `trace_id: null` because no one
called TraceContext.start() inside the pipeline backend.
"""

from __future__ import annotations

import json
import logging

import pytest

from stackowl.infra.observability import JsonlFormatter
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


def test_trace_context_start_accepts_explicit_trace_id() -> None:
    token = TraceContext.start("sess-1", trace_id="known-trace-id")
    try:
        ctx = TraceContext.get()
        assert ctx["trace_id"] == "known-trace-id"
        assert ctx["session_id"] == "sess-1"
        assert ctx["span_id"] is not None  # still minted
    finally:
        TraceContext.reset(token)
    # After reset, context is back to None default.
    assert TraceContext.get()["trace_id"] is None


def test_trace_context_start_mints_when_no_id_given() -> None:
    token = TraceContext.start("sess-2")
    try:
        ctx = TraceContext.get()
        assert ctx["trace_id"] is not None
        assert ctx["session_id"] == "sess-2"
    finally:
        TraceContext.reset(token)


@pytest.mark.asyncio
async def test_asyncio_backend_sets_trace_context_during_run() -> None:
    seen: dict[str, str | None] = {}

    async def _capturing_step(state: PipelineState) -> PipelineState:
        ctx = TraceContext.get()
        seen["trace_id"] = ctx["trace_id"]
        seen["session_id"] = ctx["session_id"]
        return state

    # In-place mutate PIPELINE_STEPS so `from ... import PIPELINE_STEPS`
    # bindings still see the patched value (assignment to module attr
    # wouldn't affect already-imported names).
    from stackowl.pipeline import registry as reg_module

    orig_steps = list(reg_module.PIPELINE_STEPS)
    reg_module.PIPELINE_STEPS[:] = [("capture", _capturing_step)]
    from stackowl.pipeline.steps import deliver as deliver_module

    orig_deliver_run = deliver_module.run

    async def _noop_deliver(s: PipelineState) -> PipelineState:
        return s

    deliver_module.run = _noop_deliver  # type: ignore[assignment]

    try:
        backend = AsyncioBackend(services=StepServices())
        state = PipelineState(
            trace_id="trace-from-channel",
            session_id="session-from-channel",
            input_text="hello",
            channel="cli",
            owl_name="secretary",
            pipeline_step="start",
        )
        await backend.run(state)
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver_run  # type: ignore[assignment]

    assert seen["trace_id"] == "trace-from-channel"
    assert seen["session_id"] == "session-from-channel"
    # After run() returns, context is reset.
    assert TraceContext.get()["trace_id"] is None


def test_trace_context_propagates_reply_target_int() -> None:
    """WS-A — start(reply_target=<chat_id>) surfaces in get() (log-safe primitive)."""
    token = TraceContext.start("sess-rt", reply_target=12345)
    try:
        assert TraceContext.get()["reply_target"] == 12345
    finally:
        TraceContext.reset(token)
    # Resets cleanly back to the None default.
    assert TraceContext.get()["reply_target"] is None


def test_trace_context_propagates_reply_target_str() -> None:
    """WS-A — a str native target (slack channel/thread id) round-trips too."""
    token = TraceContext.start("sess-rt", reply_target="C0ABC")
    try:
        assert TraceContext.get()["reply_target"] == "C0ABC"
    finally:
        TraceContext.reset(token)


def test_trace_context_reply_target_defaults_none() -> None:
    """WS-A — omitting reply_target leaves it None (byte-identical default)."""
    token = TraceContext.start("sess-rt")
    try:
        assert TraceContext.get()["reply_target"] is None
    finally:
        TraceContext.reset(token)


@pytest.mark.asyncio
async def test_asyncio_backend_surfaces_reply_target_during_run() -> None:
    """WS-A — a PipelineState.reply_target surfaces in TraceContext.get() in-run."""
    seen: dict[str, str | int | None] = {}

    async def _capturing_step(state: PipelineState) -> PipelineState:
        seen["reply_target"] = TraceContext.get()["reply_target"]
        return state

    from stackowl.pipeline import registry as reg_module

    orig_steps = list(reg_module.PIPELINE_STEPS)
    reg_module.PIPELINE_STEPS[:] = [("capture", _capturing_step)]
    from stackowl.pipeline.steps import deliver as deliver_module

    orig_deliver_run = deliver_module.run

    async def _noop_deliver(s: PipelineState) -> PipelineState:
        return s

    deliver_module.run = _noop_deliver  # type: ignore[assignment]

    try:
        backend = AsyncioBackend(services=StepServices())
        state = PipelineState(
            trace_id="trace-rt",
            session_id="session-rt",
            input_text="hello",
            channel="telegram",
            owl_name="secretary",
            pipeline_step="start",
            reply_target=98765,
        )
        await backend.run(state)
    finally:
        reg_module.PIPELINE_STEPS[:] = orig_steps
        deliver_module.run = orig_deliver_run  # type: ignore[assignment]

    assert seen["reply_target"] == 98765
    # After run() returns, context is reset.
    assert TraceContext.get()["reply_target"] is None


@pytest.mark.asyncio
async def test_jsonl_formatter_writes_trace_id_when_context_is_set() -> None:
    token = TraceContext.start("sess-fmt", trace_id="trace-fmt-test")
    try:
        rec = logging.LogRecord(
            name="stackowl.test", level=logging.INFO, pathname="",
            lineno=0, msg="hello %s", args=("world",),
            exc_info=None,
        )
        formatter = JsonlFormatter()
        line = formatter.format(rec)
        obj = json.loads(line)
        assert obj["trace_id"] == "trace-fmt-test"
        assert obj["session_id"] == "sess-fmt"
        assert obj["span_id"] is not None
        assert obj["msg"] == "hello world"
    finally:
        TraceContext.reset(token)


@pytest.mark.asyncio
async def test_jsonl_formatter_writes_null_when_no_context() -> None:
    # Ensure clean context.
    assert TraceContext.get()["trace_id"] is None
    rec = logging.LogRecord(
        name="stackowl.test", level=logging.INFO, pathname="",
        lineno=0, msg="bare", args=(),
        exc_info=None,
    )
    formatter = JsonlFormatter()
    obj = json.loads(formatter.format(rec))
    assert obj["trace_id"] is None
    assert obj["session_id"] is None
