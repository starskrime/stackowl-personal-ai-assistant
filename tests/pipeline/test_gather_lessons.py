"""Test that _gather_lessons ranks heuristics, assigns L# ids, and stashes surfaced lessons."""

from __future__ import annotations

import pytest

from stackowl.learning.lesson import LessonHit
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps.classify import _gather_lessons


class _FakeIndex:
    def __init__(self, hits: list[LessonHit]) -> None:
        self._hits = hits

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_filter: object = None,
    ) -> list[LessonHit]:
        return self._hits[:limit]


@pytest.mark.asyncio
async def test_gather_ranks_assigns_ids_and_stashes_surfaced() -> None:
    hits = [
        LessonHit(
            lesson_id="a",
            source_type="tool_heuristic",  # type: ignore[arg-type]
            source_ref="a",
            content="low-evidence note",
            similarity=0.60,
            metadata={"evidence_count": 3},
        ),
        LessonHit(
            lesson_id="b",
            source_type="tool_heuristic",  # type: ignore[arg-type]
            source_ref="b",
            content="well-proven note",
            similarity=0.80,
            metadata={"evidence_count": 50},
        ),
    ]
    services = StepServices(lessons_index=_FakeIndex(hits))  # type: ignore[arg-type]
    stoken = set_services(services)
    ltoken = lc.bind()
    try:
        block = await _gather_lessons("some query", limit=3)
        assert "## Cross-Source Lessons" in block
        assert "note_applied_lesson" in block           # contract line present
        assert "[L1]" in block and "[L2]" in block      # turn-local ids
        assert block.index("[L1]") < block.index("[L2]")
        # 'b' (well-proven, high evidence) ranked first by UCB → appears before [L2]
        assert "well-proven note" in block.split("[L2]")[0]
        surfaced = lc.get_surfaced()
        assert [s.lesson_id for s in surfaced] == ["L1", "L2"]
        assert surfaced[0].content == "well-proven note"
    finally:
        lc.reset(ltoken)
        reset_services(stoken)
