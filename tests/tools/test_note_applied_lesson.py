import pytest

from stackowl.pipeline import lesson_context as lc
from stackowl.tools.meta.note_applied_lesson import NoteAppliedLessonTool


@pytest.mark.asyncio
async def test_records_known_lesson_and_returns_success():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "browse fails on pdf", 0.9),))
        res = await tool.execute(lesson_id="L1", what_you_did="used fetch instead of browse")
        assert res.success is True
        applied = lc.drain_applied()
        assert len(applied) == 1 and applied[0].lesson_id == "L1"
        assert applied[0].what_you_did == "used fetch instead of browse"
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_unknown_id_still_succeeds_no_raise():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        lc.set_surfaced(())
        res = await tool.execute(lesson_id="L9", what_you_did="did a thing")
        assert res.success is True
        assert lc.drain_applied()[0].lesson_summary is None
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_missing_what_you_did_is_rejected_cleanly():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        res = await tool.execute(lesson_id="L1", what_you_did="")
        assert res.success is False and res.error
    finally:
        lc.reset(token)


def test_tool_registered_in_defaults():
    from stackowl.tools.registry import ToolRegistry
    reg = ToolRegistry.with_defaults()
    assert reg.get("note_applied_lesson") is not None
