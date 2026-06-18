"""Tests for McpSseEncoder — including field-1-first SSE invariant."""

from __future__ import annotations

import json

from stackowl.mcp.sse_encoder import McpSseEncoder
from stackowl.mcp.schemas import SynthesisResponse


def _enc() -> McpSseEncoder:
    return McpSseEncoder()


def test_encode_event_ends_with_double_newline() -> None:
    result = _enc().encode_event("message", '{"content":"hi"}')
    assert result.endswith("\n\n")


def test_encode_event_includes_event_field() -> None:
    result = _enc().encode_event("my_event", "data")
    assert "event: my_event" in result


def test_encode_event_includes_data_field() -> None:
    result = _enc().encode_event("message", "payload")
    assert "data: payload" in result


def test_encode_event_includes_id_when_provided() -> None:
    result = _enc().encode_event("message", "d", id="42")
    assert "id: 42" in result


def test_encode_event_omits_id_when_none() -> None:
    result = _enc().encode_event("message", "d", id=None)
    assert "id:" not in result


def test_encode_message_produces_sse_string() -> None:
    r = SynthesisResponse(content="hello")
    result = _enc().encode_message(r)
    assert "event: message" in result
    assert "data: " in result


def test_encode_message_content_is_first_key() -> None:
    r = SynthesisResponse(content="hello", consensus="yes", round_count=2)
    sse = _enc().encode_message(r)
    data_line = next(line for line in sse.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line[len("data: "):])
    keys = list(payload.keys())
    assert keys[0] == "content"


def test_encode_message_with_synthesis_response() -> None:
    r = SynthesisResponse(content="synthesis text", confidence=0.9, round_count=3)
    result = _enc().encode_message(r)
    assert "synthesis text" in result
