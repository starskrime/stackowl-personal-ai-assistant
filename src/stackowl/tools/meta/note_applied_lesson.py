"""note_applied_lesson — the model's honest self-report that a surfaced lesson
changed what it did this turn (pillar ④ explainability).

Non-consequential: it has NO side effect beyond recording an in-turn note (no
consent gate). The render step (``surface_applied_lessons``) turns recorded notes
into one user-facing line — ONLY when this tool was called, so the assistant can
never claim a lesson it didn't act on.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.pipeline import lesson_context as lc
from stackowl.tools.base import Tool, ToolResult

__all__ = ["NoteAppliedLessonTool"]

_SELF_NAME = "note_applied_lesson"


class NoteAppliedLessonTool(Tool):
    """Record that a surfaced lesson (cited by its id) shaped this turn."""

    @property
    def name(self) -> str:
        return _SELF_NAME

    @property
    def description(self) -> str:
        return (
            "Record that one of the lessons listed under '## Cross-Source Lessons' "
            "actually changed what you did THIS turn. Pass its id (e.g. 'L1') and a "
            "short, truthful note of what you did differently because of it. Call it "
            "ONLY when a lesson genuinely influenced your actions — never speculatively. "
            "It has no side effects; it lets the assistant tell the user what it drew on."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "lesson_id": {
                    "type": "string",
                    "description": "The id of the lesson you acted on, e.g. 'L1'.",
                },
                "what_you_did": {
                    "type": "string",
                    "description": "Short truthful note of what you did because of it.",
                },
            },
            "required": ["lesson_id", "what_you_did"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        lesson_id = str(kwargs.get("lesson_id", "")).strip()
        what_you_did = str(kwargs.get("what_you_did", "")).strip()
        # 1. ENTRY
        log.tool.debug(
            "note_applied_lesson.execute: entry",
            extra={"_fields": {"lesson_id": lesson_id, "what_len": len(what_you_did)}},
        )
        # 2. DECISION — validate required args; structured failure, never raise
        if not lesson_id or not what_you_did:
            log.tool.info("note_applied_lesson.execute: missing args — rejecting")
            return ToolResult(
                success=False,
                output="",
                error="Both 'lesson_id' and 'what_you_did' are required.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        # 3. STEP — record into the turn-scoped context
        matched = lc.record_applied(lesson_id, what_you_did)
        log.tool.debug(
            "note_applied_lesson.execute: recorded",
            extra={"_fields": {"lesson_id": lesson_id, "matched": matched is not None}},
        )
        # 4. EXIT
        log.tool.info(
            "note_applied_lesson.execute: exit",
            extra={"_fields": {"lesson_id": lesson_id, "matched": matched is not None}},
        )
        return ToolResult(
            success=True,
            output=f"Recorded that lesson {lesson_id} informed this turn.",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
