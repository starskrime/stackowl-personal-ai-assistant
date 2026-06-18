"""Tests for MCP response schemas — including field-1-first invariant."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.mcp.schemas import (
    AgentStatusResponse,
    BriefResponse,
    SynthesisResponse,
    ToolCallResponse,
)


def test_synthesis_response_is_frozen() -> None:
    r = SynthesisResponse(content="hello")
    with pytest.raises((TypeError, ValidationError)):
        r.content = "other"  # type: ignore[misc]


def test_synthesis_response_first_field_is_content() -> None:
    fields = list(SynthesisResponse.model_fields.keys())
    assert fields[0] == "content"


def test_brief_response_first_field_is_content() -> None:
    fields = list(BriefResponse.model_fields.keys())
    assert fields[0] == "content"


def test_agent_status_response_first_field_is_content() -> None:
    fields = list(AgentStatusResponse.model_fields.keys())
    assert fields[0] == "content"


def test_tool_call_response_first_field_is_content() -> None:
    fields = list(ToolCallResponse.model_fields.keys())
    assert fields[0] == "content"


def test_synthesis_response_model_dump_content_first() -> None:
    r = SynthesisResponse(content="text", consensus="yes", round_count=3)
    dumped = r.model_dump()
    keys = list(dumped.keys())
    assert keys[0] == "content"


def test_synthesis_response_full_fields() -> None:
    r = SynthesisResponse(
        content="body",
        consensus="c",
        recommendation="r",
        disagreement="d",
        confidence=0.9,
        round_count=2,
        owl_names=("Alice", "Bob"),
    )
    assert r.confidence == 0.9
    assert r.owl_names == ("Alice", "Bob")


def test_brief_response_sections_is_tuple() -> None:
    r = BriefResponse(content="brief", sections=("s1", "s2"))
    assert isinstance(r.sections, tuple)


def test_tool_call_response_success_defaults_true() -> None:
    r = ToolCallResponse(content="result")
    assert r.success is True


def test_all_schemas_reject_extra_fields() -> None:
    for cls in (SynthesisResponse, BriefResponse, AgentStatusResponse, ToolCallResponse):
        with pytest.raises(ValidationError):
            cls(content="x", unknown_field=True)  # type: ignore[call-arg]
