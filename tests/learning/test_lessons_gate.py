"""A lesson learned twice, worded differently, is stored once.

MEASURED on the live store: 5,146 lessons, 0% exact duplicates, and 1,153 rows
(22%) sitting at cosine >= 0.90.

BOTH HALVES OF THAT MATTER. The 0% is not luck — `publish` upserts on
``ON CONFLICT(lesson_id)`` and lesson_id is the deterministic
``"<source>:<source_ref>"``, so re-mining the same source collapses onto the same
row by construction. What that CANNOT see is the same lesson learned again from a
DIFFERENT source and written in different words: an example pair from the live
data sits at cosine 0.945 and shares barely half its vocabulary —

    A: the agent's unbounded shell execution hung indefinitely
    B: the agent stalled because a shell command hung for over two minutes

Rungs 1 and 2 are blind to that by construction. Rung 3 is the only thing that
sees it, which is why the store's blank ``embedding_model`` mattered so much: it
made the one useful rung inert for the largest store.

A HIT IS DROPPED, NOT COUNTED. `lessons` has no reinforcement column, and adding
one would be a schema change smuggled into a dedup fix. The hit is logged at INFO
instead — the only evidence the rung ever fires.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from stackowl.learning.lesson import Lesson
from stackowl.learning.lessons_store import SqliteLessonsStore

MODEL = "all-MiniLM-L6-v2"


def _vec(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(384).astype(np.float32)
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


def _near(base: list[float], nudge: float, seed: int = 7) -> list[float]:
    a = np.array(base, dtype=np.float32)
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(a.shape).astype(np.float32)
    n /= np.linalg.norm(n)
    out = a * (1.0 - nudge) + n * nudge
    out /= np.linalg.norm(out)
    return [float(x) for x in out]


def _lesson(lid: str, content: str, emb: list[float]) -> Lesson:
    return Lesson(
        lesson_id=lid, source_type="reflection", source_ref=lid.split(":")[-1],
        content=content, embedding=emb, metadata={},
    )


@pytest.mark.asyncio
async def test_a_reworded_lesson_from_another_source_is_NOT_stored_twice(
    tmp_db: Any,
) -> None:
    """The 22%. Different lesson_id, different words, same meaning."""
    store = SqliteLessonsStore(tmp_db, embedding_model=MODEL)
    v = _vec(1)
    await store.publish(_lesson("reflection:a", "the shell hung indefinitely", v))
    await store.publish(
        _lesson("reflection:b", "a command stalled for over two minutes", _near(v, 0.10))
    )

    rows = await tmp_db.fetch_all("SELECT lesson_id FROM lessons")
    assert len(rows) == 1, "the second wording must not become a second row"
    assert rows[0]["lesson_id"] == "reflection:a", "the FIRST one is the one kept"


@pytest.mark.asyncio
async def test_a_genuinely_different_lesson_is_still_stored(tmp_db: Any) -> None:
    """The gate must not turn learning off — the failure mode that would make
    this worse than the duplication it removes."""
    store = SqliteLessonsStore(tmp_db, embedding_model=MODEL)
    await store.publish(_lesson("reflection:a", "the shell hung", _vec(1)))
    await store.publish(_lesson("reflection:c", "the browser recycled mid-open", _vec(99)))

    rows = await tmp_db.fetch_all("SELECT lesson_id FROM lessons")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_re_publishing_the_SAME_id_still_revises_the_row(tmp_db: Any) -> None:
    """An upsert on the same lesson_id is a REVISION of a known row, not a
    duplicate. Suppressing it would freeze a lesson at its first wording — so the
    gate must ignore the row it is about to replace."""
    store = SqliteLessonsStore(tmp_db, embedding_model=MODEL)
    v = _vec(2)
    await store.publish(_lesson("reflection:x", "first wording", v))
    await store.publish(_lesson("reflection:x", "revised wording", v))

    rows = await tmp_db.fetch_all("SELECT lesson_id, content FROM lessons")
    assert len(rows) == 1
    assert rows[0]["content"] == "revised wording", (
        "a same-id republish must still update — the gate must not block a revision"
    )


@pytest.mark.asyncio
async def test_an_unrecorded_model_cannot_suppress_a_lesson(tmp_db: Any) -> None:
    """The 5,146 back-catalogue rows carry embedding_model = ''. The gate refuses
    to compare those, so a store built without a model must never drop a lesson
    on a similarity it is not entitled to compute."""
    store = SqliteLessonsStore(tmp_db)  # no model -> ''
    v = _vec(3)
    await store.publish(_lesson("reflection:p", "one wording", v))
    await store.publish(_lesson("reflection:q", "another wording", _near(v, 0.02)))

    rows = await tmp_db.fetch_all("SELECT lesson_id FROM lessons")
    assert len(rows) == 2, (
        "with no recorded model the vectors are formally incomparable, so BOTH "
        "must be stored — dropping one would be a merge on evidence the gate "
        "explicitly refuses"
    )


@pytest.mark.asyncio
async def test_a_gate_failure_never_costs_the_lesson(
    tmp_db: Any, monkeypatch: Any
) -> None:
    """B5. Learning is the point; deduplicating is the improvement."""
    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("gate exploded")

    monkeypatch.setattr("stackowl.memory.remember_gate.should_remember", _boom)
    store = SqliteLessonsStore(tmp_db, embedding_model=MODEL)

    await store.publish(_lesson("reflection:z", "do not lose me", _vec(4)))

    rows = await tmp_db.fetch_all("SELECT content FROM lessons")
    assert len(rows) == 1
    assert rows[0]["content"] == "do not lose me"
