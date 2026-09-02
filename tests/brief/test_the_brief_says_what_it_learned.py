"""D09.6 — "what have I actually taught it?", answered on the surface we have.

The reference platform answers this with a learning graph rendered in a desktop
app. StackOwl has no desktop app; it has the morning brief, which is the surface
an equivalent answer can actually reach.

LEARNING IS HAPPENING AND NONE OF IT WAS VISIBLE. Measured 2026-09-02 on the live
database: 5,747 lessons and 586 learning artifacts all-time, with 49 lessons and
21 DNA adjustments written in the last 24 hours alone — and `note_applied_lesson`
invoked 791 times, so they are read back and used. The only way to see any of it
was to query SQLite by hand.

COUNTS FIRST, THEN A FEW TEXTS. 49 entries a day cannot be listed; a brief nobody
reads reports nothing. The counts say how much was learned, and the samples show
the counts are not empty bookkeeping. The lesson bodies are already written for a
reader — "What worked for rca_gatherer: ..." — so they need no reformatting.

THE TWO HALVES FAIL DIFFERENTLY, ON PURPOSE. A lessons read that fails omits the
section: reporting "nothing learned" when the query broke is a false clean bill of
health. A learning_artifacts read that fails keeps the lessons half, because
dropping what already succeeded would report LESS than is known.
"""

from __future__ import annotations

import pytest

from stackowl.brief.assemblers import BriefContext, LearningAssembler

pytestmark = pytest.mark.asyncio


class _Db:
    def __init__(self, lessons: list[dict], artifacts: list[dict] | None = None,
                 fail_artifacts: bool = False) -> None:
        self._lessons = lessons
        self._artifacts = artifacts or []
        self._fail_artifacts = fail_artifacts

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        if "learning_artifacts" in sql:
            if self._fail_artifacts:
                raise RuntimeError("no such table")
            assert "owner_id = ?" in sql, (
                "learning_artifacts is owner-governed — an unscoped read is a "
                "cross-tenant leak and tests/tenancy fails the build for it"
            )
            return self._artifacts
        return self._lessons


class _AngryDb:
    async def fetch_all(self, *a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")


def _lesson(kind: str, text: str = "What worked for scout: it read the page first.") -> dict:
    return {"source_type": kind, "content": text}


def _ctx() -> BriefContext:
    return BriefContext.model_construct()


async def test_it_reports_the_COUNT_and_the_spread() -> None:
    """The headline. 49 a day is the real volume, and the spread is what says
    whether the platform is learning from reflection, skills or heuristics."""
    rows = [_lesson("reflection")] * 43 + [_lesson("skill")] * 5 + [_lesson("tool_heuristic")]
    sec = await LearningAssembler(_Db(rows)).assemble(_ctx())
    assert "49 lessons learned" in sec.items[0]
    assert "43 from reflection" in sec.items[0]


async def test_it_shows_a_FEW_texts_not_all_of_them() -> None:
    """A wall of 49 entries is a brief nobody reads, and a brief nobody reads
    reports nothing."""
    rows = [_lesson("reflection", f"lesson number {i}") for i in range(49)]
    sec = await LearningAssembler(_Db(rows)).assemble(_ctx())
    assert len(sec.items) <= 5
    assert any("lesson number 0" in i for i in sec.items)


async def test_dna_adjustments_are_counted_too() -> None:
    """DNA evolution is learning the user never sees — 21 in one day."""
    sec = await LearningAssembler(
        _Db([_lesson("reflection")], artifacts=[{"artifact_type": "dna", "n": 21}]),
    ).assemble(_ctx())
    assert any("21 dna adjustments" in i for i in sec.items)


async def test_a_quiet_day_omits_the_section() -> None:
    """A section that says "nothing learned" every morning trains you to skip
    the whole brief."""
    sec = await LearningAssembler(_Db([])).assemble(_ctx())
    assert sec.omitted is True


async def test_a_lessons_failure_OMITS_rather_than_reporting_nothing_learned() -> None:
    """The expensive direction. "0 lessons" when the read failed is a false clean
    bill of health — the mistake this project keeps paying for."""
    sec = await LearningAssembler(_AngryDb()).assemble(_ctx())
    assert sec.omitted is True
    assert sec.items == []


async def test_an_artifacts_failure_KEEPS_the_lessons_half() -> None:
    """Partial beats omitted here, and the asymmetry is deliberate: the lessons
    read already succeeded, and dropping it because the second query failed would
    report LESS than is known."""
    sec = await LearningAssembler(
        _Db([_lesson("reflection")], fail_artifacts=True),
    ).assemble(_ctx())
    assert sec.omitted is False
    assert "1 lessons learned" in sec.items[0]


def test_it_is_WIRED_into_the_morning_brief() -> None:
    """A section nothing constructs is decoration — and the whole item is that
    this learning was invisible."""
    import inspect

    from stackowl.scheduler.handlers import morning_brief

    assert "LearningAssembler(db=db)" in inspect.getsource(morning_brief)
