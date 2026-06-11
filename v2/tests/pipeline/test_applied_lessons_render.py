import pytest

from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, responses):
    return PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="o", pipeline_step="deliver", responses=responses,
    )


def _answer_chunk(text="here is your answer", is_floor=False):
    return ResponseChunk(content=text, is_final=False, chunk_index=0,
                         trace_id="t", owl_name="o", is_floor=is_floor)


@pytest.mark.asyncio
async def test_appends_one_line_when_applied_and_real_answer():
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "x", 0.9),))
        lc.record_applied("L1", "used fetch instead of browse")
        out = await surface_applied_lessons(_state(responses=(_answer_chunk(),)))
        assert len(out.responses) == 2
        assert "used fetch instead of browse" in out.responses[-1].content
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_no_applied_means_unchanged():
    token = lc.bind()
    try:
        s = _state(responses=(_answer_chunk(),))
        out = await surface_applied_lessons(s)
        assert out.responses == s.responses
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_floor_only_response_gets_no_annotation():
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "x", 0.9),))
        lc.record_applied("L1", "did something")
        s = _state(responses=(_answer_chunk("I couldn't finish", is_floor=True),))
        out = await surface_applied_lessons(s)
        assert out.responses == s.responses
    finally:
        lc.reset(token)
