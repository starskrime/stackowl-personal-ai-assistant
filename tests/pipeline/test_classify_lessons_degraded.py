"""FR-3 follow-up — lessons_index recall failure must not look like "no lessons".

FR-3 collapsed classify's direct-SQLite reflections block into the unified
lessons_index (_gather_lessons is now the sole reflection-surfacing path).
That collapse must carry over the F-49 honesty guarantee the old reflections
path had: a recall FAILURE is retried once, then annotated DEGRADED — never
silently treated as "nothing to surface".
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


class _FailingLessonsIndex:
    """A lessons_index stand-in whose every search raises."""

    def __init__(self) -> None:
        self.search_calls = 0

    async def search(self, query: str, limit: int = 3) -> list[object]:  # noqa: ARG002
        self.search_calls += 1
        raise RuntimeError("lessons recall boom")


class _EmptyLessonsIndex:
    async def search(self, query: str, limit: int = 3) -> list[object]:  # noqa: ARG002
        return []


async def test_lessons_recall_failure_retries_once_then_annotates_degraded() -> None:
    fake = _FailingLessonsIndex()
    token = set_services(StepServices(lessons_index=fake))  # type: ignore[arg-type]
    try:
        block = await classify._gather_lessons("what did we learn", limit=3)
    finally:
        reset_services(token)

    assert fake.search_calls == 2
    assert "DEGRADED" in block
    assert block != ""


async def test_lessons_legitimate_empty_returns_empty_no_degraded() -> None:
    token = set_services(StepServices(lessons_index=_EmptyLessonsIndex()))  # type: ignore[arg-type]
    try:
        block = await classify._gather_lessons("what did we learn", limit=3)
    finally:
        reset_services(token)

    assert block == ""
    assert "DEGRADED" not in block
