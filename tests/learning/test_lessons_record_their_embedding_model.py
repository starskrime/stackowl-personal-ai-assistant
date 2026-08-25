"""A vector whose model is unrecorded cannot be compared to anything.

MEASURED 2026-08-25: all 5,146 rows in `lessons` carry ``embedding_model = ''``.
The column exists, the vectors are all there (100% embedded), and not one row
says which model produced it.

WHY THAT IS NOT COSMETIC. The dedup gate refuses to cosine-compare two vectors
unless their ``embedding_model`` matches AND is non-empty, because this platform
does not have one vector space: reflections mix ``all-MiniLM-L6-v2`` (4,772 rows)
with the DEGRADED ``hash-v1-384d`` fallback (18), and both are 384-dim — the
arithmetic succeeds and the answer is meaningless. So an unrecorded model makes
the gate's semantic rung INERT for the largest store, which is precisely the
store that needs it: lessons already deduplicate exactly (the upsert keys on a
deterministic ``<source>:<source_ref>`` id, which is why they show 0% exact
duplicates), and their 1,153 near-duplicates at cosine >= 0.90 are visible to
NOTHING BUT rung 3.

THE CAUSE IS ONE UNSUPPLIED ARGUMENT. ``SqliteLessonsStore.__init__`` takes
``embedding_model: str = ""``. Two callers construct it:

    cli/app.py:524          SqliteLessonsStore(db, embedding_model=registry.active_model)
    memory/assembly.py:270  SqliteLessonsStore(db)          <- the PRODUCTION path

The CLI supplies it. The path that actually runs does not, so the default wins on
every live write. That is the seventh thing this session that exists, works, and
is not reached on the path that matters — after FactReinforcer, add_relation,
is_machine_lane, reinforcement_count, CuratedMemory's target, and the
embedding_registry the bridge held unused.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from stackowl.learning.lessons_store import SqliteLessonsStore


@pytest.mark.asyncio
async def test_a_published_lesson_records_the_model(tmp_db: Any) -> None:
    from stackowl.learning.lesson import Lesson

    store = SqliteLessonsStore(tmp_db, embedding_model="all-MiniLM-L6-v2")
    await store.publish(
        Lesson(
            lesson_id="src:ref",
            source_type="reflection",
            source_ref="ref",
            content="a lesson",
            embedding=[0.1] * 384,
            metadata={},
        )
    )

    row = (await tmp_db.fetch_all(
        "SELECT embedding_model FROM lessons WHERE lesson_id = 'src:ref'"
    ))[0]
    assert row["embedding_model"] == "all-MiniLM-L6-v2"


@pytest.mark.asyncio
async def test_an_unsupplied_model_still_writes_the_lesson(tmp_db: Any) -> None:
    """The default must degrade, not fail — an unrecorded model costs the gate's
    third rung, but losing the lesson entirely would be worse."""
    from stackowl.learning.lesson import Lesson

    store = SqliteLessonsStore(tmp_db)
    await store.publish(
        Lesson(
            lesson_id="src:ref2",
            source_type="reflection",
            source_ref="ref2",
            content="another lesson",
            embedding=[0.1] * 384,
            metadata={},
        )
    )

    rows = await tmp_db.fetch_all("SELECT content FROM lessons WHERE lesson_id='src:ref2'")
    assert len(rows) == 1


def test_the_PRODUCTION_assembly_supplies_the_model() -> None:
    """THE defect. The CLI passed it and the assembly did not, so every live row
    got the empty default and the gate's semantic rung went inert for 5,146 rows.

    Asserted against the assembly source because that is where the wiring lives;
    a behavioural test would need the whole memory assembly stood up, and the
    thing under test is one argument at one construction site.
    """
    from stackowl.memory import assembly

    src = inspect.getsource(assembly)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "SqliteLessonsStore":
            continue
        kwargs = {kw.arg for kw in node.keywords}
        assert "embedding_model" in kwargs, (
            "memory/assembly.py must pass embedding_model — without it every "
            "lesson written in production records '' and the dedup gate can "
            "never compare them"
        )
        return
    raise AssertionError("SqliteLessonsStore construction not found in memory/assembly.py")
