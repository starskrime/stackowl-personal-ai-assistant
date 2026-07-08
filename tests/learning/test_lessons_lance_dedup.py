"""Same-batch duplicate lesson_ids must not crash publish_many.

LanceDB's ``merge_insert`` raises "Ambiguous merge inserts are prohibited"
when two SOURCE rows in the same ``.execute()`` call match the same TARGET
row on the merge key. This happens for real: two lessons synthesized for the
same skill/source_ref within one flush (observed in production as
``lesson_id = "skill:learned/reks-research-specialist"``) both carry the same
``lesson_id`` and collapse the whole batch write.

Fix: dedupe by ``lesson_id`` (last-wins — the same semantics sequential
``publish()`` calls for the same id would produce) before handing rows to
``merge_insert``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.learning.lesson import Lesson
from stackowl.learning.lessons_lance import LessonsLanceAdapter, _sync_publish_many


def _connect(data_dir: Path):  # type: ignore[no-untyped-def]
    import lancedb

    data_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(data_dir))


def _lesson(lesson_id: str, content: str) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        source_type="skill",
        source_ref="learned/reks-research-specialist",
        content=content,
        embedding=[0.1, 0.2, 0.3],
        metadata={},
    )


def test_sync_publish_many_dedupes_same_batch_duplicate_lesson_id(tmp_path: Path) -> None:
    """Two lessons sharing a lesson_id in ONE batch, where that lesson_id
    ALREADY has a matching row in the table (the real production shape — a
    prior publish created the target row, then a later batch's two source
    rows both match it), must not raise the ambiguous-merge error — the
    batch collapses to one row, last-wins."""
    conn = _connect(tmp_path / "lance")
    lesson_id = "skill:learned/reks-research-specialist"
    # Seed an existing target row so the batch's two source rows both MATCH
    # it (this is what makes LanceDB's merge_insert call it "ambiguous").
    _sync_publish_many(conn, [_lesson(lesson_id, "seed version")])

    lessons = [
        _lesson(lesson_id, "first version"),
        _lesson(lesson_id, "second version"),
    ]

    _sync_publish_many(conn, lessons)  # must not raise

    table = conn.open_table("lessons")
    rows = table.to_arrow().to_pylist()
    matching = [r for r in rows if r["lesson_id"] == lesson_id]
    assert len(matching) == 1
    assert matching[0]["content"] == "second version"


@pytest.mark.asyncio
async def test_adapter_publish_many_returns_deduped_count(tmp_path: Path) -> None:
    """The reported 'published' count must reflect DISTINCT rows actually
    written, not the raw input length — an honest count, not an overclaim."""
    adapter = LessonsLanceAdapter(data_dir=tmp_path / "lance")
    lessons = [
        _lesson("skill:learned/dup", "v1"),
        _lesson("skill:learned/dup", "v2"),
        _lesson("skill:learned/other", "v1"),
    ]

    written = await adapter.publish_many(lessons)

    assert written == 2
