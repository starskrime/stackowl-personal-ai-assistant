"""T9 — safety-net surfaces SWALLOWED delegation failures.

A ``delegate_task`` tool call can fail silently: the parent produced no usable
answer AND the tool-result record carries a terminal status (``child_error``,
``cycle``, ``timeout``, ``empty``, ``target_not_found``).  The existing
``detect_critical_failure`` path only fires on an ``execute:`` step error;
a swallowed delegation failure leaves no such error — the execute step itself
succeeded (it got a tool result back), but the model produced no final text.

``_delegation_failed_with_no_answer`` closes this gap:

* Returns True  iff (no usable response) AND (a delegate record's status is
  in the terminal set).
* Returns False when the parent DID produce an answer (even with a terminal
  delegate status — the model recovered autonomously).
* Returns False for recovered/ok/truncated statuses.
* JSON parse is DEFENSIVE — non-delegation / unparseable results are skipped.
* ``detect_critical_failure`` wires the new predicate into the existing OR.
"""

from __future__ import annotations

import json

import pytest

from stackowl.pipeline.critical_failure import (
    _delegation_failed_with_no_answer,
    detect_critical_failure,
)
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk


# ---- helpers ----------------------------------------------------------------


def _make_tool_call(status: str) -> ToolCall:
    """A ToolCall whose result JSON matches the delegate_task envelope."""
    payload = json.dumps(
        {"note": "test", "record": {"status": status, "to_owl": "specialist"}}
    )
    return ToolCall(
        tool_name="delegate_task",
        args={"goal": "do something"},
        result=payload,
        error=None,
        duration_ms=1.0,
    )


def _chunk(text: str) -> ResponseChunk:
    return ResponseChunk(
        content=text,
        is_final=False,
        chunk_index=0,
        trace_id="trace-t9",
        owl_name="secretary",
    )


def _state(status: str, response_text: str = "") -> PipelineState:
    """PipelineState with one delegate tool call and an optional response chunk."""
    tc = _make_tool_call(status)
    chunks: tuple[ResponseChunk, ...] = (_chunk(response_text),) if response_text else ()
    return PipelineState(
        trace_id="trace-t9",
        session_id="sess-t9",
        input_text="do a task",
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        tool_calls=(tc,),
        responses=chunks,
    )


# ---- _delegation_failed_with_no_answer tests --------------------------------


def test_child_error_no_answer_trips() -> None:
    assert _delegation_failed_with_no_answer(_state("child_error", "")) is True


def test_cycle_no_answer_trips() -> None:
    assert _delegation_failed_with_no_answer(_state("cycle", "")) is True


def test_timeout_no_answer_trips() -> None:
    assert _delegation_failed_with_no_answer(_state("timeout", "")) is True


def test_empty_no_answer_trips() -> None:
    assert _delegation_failed_with_no_answer(_state("empty", "")) is True


def test_target_not_found_no_answer_trips() -> None:
    assert _delegation_failed_with_no_answer(_state("target_not_found", "")) is True


def test_recovered_delegation_does_not_trip() -> None:
    assert _delegation_failed_with_no_answer(_state("recovered_via_secretary", "")) is False


def test_ok_delegation_does_not_trip() -> None:
    assert _delegation_failed_with_no_answer(_state("ok", "")) is False


def test_truncated_delegation_does_not_trip() -> None:
    """truncated has partial content — treat as an answer, do not surface."""
    assert _delegation_failed_with_no_answer(_state("truncated", "")) is False


def test_nonempty_answer_does_not_trip() -> None:
    """Parent produced a real answer — do not inject apology over it."""
    assert _delegation_failed_with_no_answer(_state("child_error", "here is the answer")) is False


def test_nonempty_answer_ok_does_not_trip() -> None:
    assert _delegation_failed_with_no_answer(_state("ok", "real answer")) is False


def test_no_tool_calls_does_not_trip() -> None:
    """No tool calls at all — predicate must not crash."""
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="execute",
    )
    assert _delegation_failed_with_no_answer(state) is False


def test_unparseable_tool_result_does_not_crash() -> None:
    """Garbage result string — skip defensively, never crash the safety net."""
    tc = ToolCall(
        tool_name="delegate_task",
        args={},
        result="not-json{{{",
        error=None,
        duration_ms=0.0,
    )
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="execute",
        tool_calls=(tc,),
    )
    assert _delegation_failed_with_no_answer(state) is False


def test_none_result_does_not_crash() -> None:
    """ToolCall.result=None — skip defensively."""
    tc = ToolCall(
        tool_name="delegate_task",
        args={},
        result=None,
        error=None,
        duration_ms=0.0,
    )
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="execute",
        tool_calls=(tc,),
    )
    assert _delegation_failed_with_no_answer(state) is False


def test_non_delegation_tool_result_skipped() -> None:
    """A non-delegate tool result (no record.status key) is skipped."""
    tc = ToolCall(
        tool_name="shell",
        args={},
        result=json.dumps({"output": "some shell output"}),
        error=None,
        duration_ms=0.0,
    )
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="execute",
        tool_calls=(tc,),
    )
    assert _delegation_failed_with_no_answer(state) is False


# ---- detect_critical_failure integration tests ------------------------------


def test_detect_critical_failure_via_delegation() -> None:
    """detect_critical_failure returns True for a swallowed delegation failure."""
    assert detect_critical_failure(_state("child_error", "")) is True


def test_detect_critical_failure_not_tripped_with_answer() -> None:
    """detect_critical_failure returns False when parent produced an answer."""
    assert detect_critical_failure(_state("child_error", "the answer")) is False


def test_detect_critical_failure_not_tripped_for_recovered() -> None:
    """detect_critical_failure returns False for a recovered delegation."""
    assert detect_critical_failure(_state("recovered_via_secretary", "")) is False
