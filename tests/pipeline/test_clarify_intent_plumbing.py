"""Task 1 — tool-free intent class plumbing for the `clarify` verdict.

Tests that:
- TOOL_FREE_CLASSES is exported from state and has the correct members.
- PipelineState has a `clarify_question` field that defaults to None.
- `_ensure_tool_capable` passes through an incapable provider untouched on a
  `clarify` turn, exactly as it does for `conversational`.
"""
from stackowl.pipeline.state import PipelineState, TOOL_FREE_CLASSES
from stackowl.pipeline.provider_select import _ensure_tool_capable


class _NoToolsProvider:
    name = "weak"
    supports_tools = False


def _state(**kw):
    return PipelineState(
        input_text="x",
        session_id="s",
        channel="cli",
        trace_id="t",
        owl_name="secretary",
        pipeline_step="test",
        **kw,
    )


def test_tool_free_classes_membership():
    assert TOOL_FREE_CLASSES == frozenset({"conversational", "clarify"})


def test_clarify_question_field_defaults_none():
    assert _state().clarify_question is None
    assert _state(clarify_question="What kind?").clarify_question == "What kind?"


def test_ensure_tool_capable_passes_through_for_clarify():
    # A clarify turn needs no tools; an incapable provider must pass through
    # untouched (no raise), exactly like conversational.
    p = _NoToolsProvider()
    out_provider, out_model = _ensure_tool_capable(
        p, "", registry=None, state=_state(intent_class="clarify"),
        log_selection=False,
    )
    assert out_provider is p
    assert out_model == ""
