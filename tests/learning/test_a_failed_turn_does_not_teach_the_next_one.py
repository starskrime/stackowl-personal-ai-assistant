"""The platform scored the turn a zero, then kept its lesson recallable.

STANDING RULE (Bakir): self-learning stores WINS, never failures. The writer
honours it — 5,507 of 5,772 lessons open "What worked for <owl>: ...". This is
about what happens after something gets in anyway.

MEASURED 2026-09-03 on the live corpus. Every reflection-sourced lesson carries
its turn's ``quality_score`` in metadata, and the distribution has a clear tail::

    0.0  23      0.6   216
    0.1  25      0.7   167
    0.2  14      0.8  1024
    0.3  10      0.9  1919
    0.4   5      1.0  2097
    0.5   7
                 median 0.95,  p10 0.75

Eighty-four lessons sit below 0.6 — 1.5% of the corpus — and TWENTY-THREE of them
carry a quality of exactly 0.0. Forty-six say "(low-quality)" in the prose. They
all entered in one batch at 2026-08-15T01:52:24, so no live writer is producing
them; they are residue from a backfill.

THE DEFECT IS THAT NOTHING READS THE SCORE. ``lessons_store.search`` ranks purely
by distance — ``np.argsort(distances)[:limit]`` — and ``rank_lessons`` only
UCB-ranks HEURISTIC-sourced hits, appending reflections "in order". So
``quality_score`` is written on every reflection lesson and read by nobody: a
write with no reader, the first shape on this codebase's own list of what accounts
for nearly every real defect here.

WHAT IT COSTS. A lesson mined from a turn the platform itself scored 0.0 competes
for injection on cosine similarity alone, against one scored 1.0. The rule is
enforced at the WRITER and nowhere else, so anything that bypasses the writer — a
backfill, an import, a future change — puts a failed turn's advice in front of the
model. Enforcing it at the READER makes the rule hold regardless of who writes.

WHY 0.6, AND WHY NOT LOWER. The break in the data is between 0.5 (7 rows) and 0.6
(216). Below it is 1.5% of the corpus and every "(low-quality)" row; above it is
the mass. A floor at 0.7 would drop 368 rows (6%) including a healthy 0.6 band
that nothing has shown to be bad.

ABSENCE IS NOT A LOW SCORE. 265 lessons carry no numeric quality at all — the
skill and tool_heuristic tiers do not mine one — and they are KEPT. Excluding them
would silently delete two whole tiers from recall on no evidence, which is the
"an unknown must not read as none" rule this codebase applies elsewhere.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.learning.lesson import Lesson

pytestmark = pytest.mark.asyncio

#: The measured break in the live distribution.
FLOOR = 0.6


def _store(db: DbPool):
    from stackowl.learning.lessons_store import SqliteLessonsStore

    return SqliteLessonsStore(db)


def _lesson(lid: str, vec: list[float], quality: float | None,
            *, source_type: str = "reflection") -> Lesson:
    meta: dict[str, object] = {}
    if quality is not None:
        meta["quality_score"] = quality
    return Lesson(
        lesson_id=lid, source_type=source_type,  # type: ignore[arg-type]
        source_ref=f"ref-{lid}", content=f"content of {lid}",
        embedding=vec, metadata=meta,
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


async def test_a_zero_quality_lesson_is_not_recalled(tmp_db: DbPool) -> None:
    """THE DEFECT. 23 live lessons carry a quality of exactly 0.0 and were
    recallable on similarity alone."""
    store = _store(tmp_db)
    await store.publish(_lesson("failed", [1.0, 0.0, 0.0], 0.0))

    hits = await store.search([1.0, 0.0, 0.0], limit=5)

    assert hits == [], (
        "a lesson mined from a turn the platform scored 0.0 was served as advice"
    )


async def test_a_good_lesson_is_still_recalled(tmp_db: DbPool) -> None:
    """The other direction. The median lesson scores 0.95 and the store's value
    is measured — lessons genuinely help his interactive turns (+5.6pp, 2.1σ)."""
    store = _store(tmp_db)
    await store.publish(_lesson("good", [1.0, 0.0, 0.0], 0.95))

    assert [h.lesson_id for h in await store.search([1.0, 0.0, 0.0], limit=5)] == ["good"]


async def test_a_lesson_with_NO_score_is_kept(tmp_db: DbPool) -> None:
    """ABSENCE IS NOT A LOW SCORE. 265 live lessons carry no numeric quality —
    the skill and tool_heuristic tiers do not mine one — and dropping them would
    delete two whole tiers from recall on no evidence."""
    store = _store(tmp_db)
    await store.publish(_lesson("skill", [1.0, 0.0, 0.0], None, source_type="skill"))

    assert [h.lesson_id for h in await store.search([1.0, 0.0, 0.0], limit=5)] == ["skill"]


async def test_a_non_numeric_score_is_kept_not_guessed_at(tmp_db: DbPool) -> None:
    """A corrupt or unexpected value is an unknown, and an unknown must not read
    as a failure — the same rule the metadata parser already follows when it
    treats corruption as empty rather than fatal."""
    store = _store(tmp_db)
    lesson = Lesson(
        lesson_id="weird", source_type="reflection", source_ref="ref-weird",
        content="content of weird", embedding=[1.0, 0.0, 0.0],
        metadata={"quality_score": "0.0"},  # a STRING, not a number
    )
    await store.publish(lesson)

    assert [h.lesson_id for h in await store.search([1.0, 0.0, 0.0], limit=5)] == ["weird"]


async def test_the_floor_sits_at_the_measured_break(tmp_db: DbPool) -> None:
    """The break in the live histogram is between 0.5 (7 rows) and 0.6 (216).
    Just below is excluded, exactly at the floor is kept."""
    store = _store(tmp_db)
    await store.publish(_lesson("below", [1.0, 0.0, 0.0], FLOOR - 0.1))
    await store.publish(_lesson("at", [0.99, 0.14, 0.0], FLOOR))

    ids = {h.lesson_id for h in await store.search([1.0, 0.0, 0.0], limit=5)}
    assert ids == {"at"}, ids


async def test_a_filtered_lesson_does_not_shrink_the_result(tmp_db: DbPool) -> None:
    """The cut happens before the limit is applied, or a corpus with a few bad
    rows would silently return fewer lessons than asked for — the quiet
    degradation this codebase keeps finding."""
    store = _store(tmp_db)
    await store.publish(_lesson("bad", [1.0, 0.0, 0.0], 0.0))
    for i, v in enumerate(([0.99, 0.1, 0.0], [0.98, 0.2, 0.0])):
        await store.publish(_lesson(f"ok{i}", v, 0.9))

    hits = await store.search([1.0, 0.0, 0.0], limit=2)

    assert len(hits) == 2, f"the bad row ate a slot: {[h.lesson_id for h in hits]}"
    assert "bad" not in {h.lesson_id for h in hits}
