"""The forensic-tail cap was deleting the memories semantic recall exists to serve.

MEASURED 2026-09-08, from the platform's own log and its live database:

    323  conversation_summary rows DELETED by `_trim_source_type`
         (314 stage events trimming 1, three trimming 2, one trimming 3)
     50  conversation_summary rows remaining, ALL FIFTY carrying an embedding
    320  `sqlite_helpers.staged_semantic_recall` firings in the same window

`staged_semantic_recall` selects `FROM staged_facts WHERE owner_id = ? AND
status = 'staged' AND embedding IS NOT NULL` — with NO `source_type` predicate.
Every summary the cap deleted was inside that corpus.

THE PREMISE WAS TRUE WHEN IT WAS WRITTEN, AND NOTHING RE-READ IT. The cap's own
docstring says: *"These rows have no rich reader: every other staged_facts SELECT
in this class filters source_type = 'conversation'… The cap keeps a forensic
tail, it does not serve a query."* That was a correct survey of
``sqlite_bridge`` on 2026-08-14. On 2026-08-30 the ESC-69 interim shipped
``staged_semantic_recall`` into ``sqlite_helpers`` — the sibling module
``sqlite_bridge`` IMPORTS AT LINE 22 — precisely to make staged rows reachable,
because `committed_facts` was empty and "414 memory searches returned 0 archive
hits". From that day the premise was false, and sixteen days of summaries were
deleted by a bound justified on it.

Two other surfaces already said so and nothing joined them up:
`models.py` records that `conversation_summary` is "still written and still read
as a staged row", and `test_recall_can_see_staged_facts.py` exists to pin the
reader. The suite held both halves of the contradiction.

THE ROOT CAUSE IS THE SHAPE, NOT THE NUMBER. A bound justified by a claim about
the REST OF THE SYSTEM, with nothing tying the two together, is a premise that
ages silently — the same failure this programme cured for escalations
(`premise_check`), for partial stages (`closing_check`) and for design documents
(`doc_check`), now found in CODE. Raising 50 to some larger number would have
bought a quiet week.

So the trim now deletes only what recall CANNOT return, expressed with the
reader's own predicate rather than a list of exempt types. The forensic tail
still exists for genuinely unsearchable rows, which is what it was always for —
the docstring's justification becomes true by construction instead of by
survey.

WHAT THIS DOES NOT FIX, stated rather than hidden: summaries now accumulate, at a
measured ~30/day across the retained window (324 in 11 days). That is small — a
year is ~11k rows — and unbounded growth belongs in the `knowledge_prune` decay
job with a stated horizon, not in a silent write-path delete. It is NOT bounded
here, and this docstring is the record of that choice.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from stackowl.memory.models import StagedFact


def _fact(
    source_type: str, source_ref: str, n: int, *, embedded: bool
) -> StagedFact:
    """A staged fact, optionally carrying the embedding that makes it searchable."""
    return StagedFact(
        content=f"{source_type}-{n}",
        source_type=source_type,
        source_ref=source_ref,
        confidence=0.5,
        staged_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        + datetime.timedelta(minutes=n),
        trust="self",
        # AN ORTHOGONAL vector per fact, and it took two goes to get right. The
        # first fixture gave every row the SAME embedding and the bridge's
        # similarity dedup folded 75 writes into ONE row; the second nudged one
        # component (`0.1 + n*0.01`), which is still cosine ~0.999 — far above the
        # 0.92 dedup threshold — and folded them into two. Real summaries of
        # different conversations are not near-parallel, so a double whose vectors
        # are makes the code under test look far worse than it is. One-hot is the
        # honest stand-in: distinct, orthogonal, and obviously so.
        embedding=[1.0 if i == n else 0.0 for i in range(128)] if embedded else None,
        embedding_model="probe-model" if embedded else None,
    )


async def _count(db: Any, source_type: str) -> int:
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM staged_facts WHERE source_type = ?", (source_type,)
    )
    return int(rows[0]["n"])


@pytest.mark.asyncio
async def test_an_embedded_summary_is_never_trimmed(tmp_db: Any) -> None:
    """The 323 rows. Every one carried an embedding and was inside the corpus."""
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    over = _TURN_HISTORY_FLOOR + 25
    for i in range(over):
        await bridge.stage(_fact("conversation_summary", f"sess-{i}", i, embedded=True))

    remaining = await _count(tmp_db, "conversation_summary")
    assert remaining == over, (
        f"{over - remaining} searchable summaries were deleted by a bound whose "
        f"stated purpose is to keep a forensic tail of rows nothing reads"
    )


@pytest.mark.asyncio
async def test_the_oldest_summary_survives_because_recall_can_still_find_it(
    tmp_db: Any,
) -> None:
    """Not just the count — the specific row. A cap that keeps the newest N is
    exactly wrong for memory, where the OLD thing is the thing worth remembering."""
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    for i in range(_TURN_HISTORY_FLOOR + 10):
        await bridge.stage(_fact("conversation_summary", f"sess-{i}", i, embedded=True))

    rows = await tmp_db.fetch_all(
        "SELECT content FROM staged_facts WHERE source_type = 'conversation_summary'"
    )
    kept = {r["content"] for r in rows}
    assert "conversation_summary-0" in kept, (
        "the oldest summary is gone — the user's earliest remembered conversation "
        "is the first thing this cap threw away"
    )


@pytest.mark.asyncio
async def test_an_unsearchable_row_is_STILL_bounded(tmp_db: Any) -> None:
    """THE FORENSIC TAIL MUST SURVIVE THIS FIX.

    The cap answers a real disease — "every turn appends a row forever: the same
    no-decay disease that grew the fact store to 107,576 rows". Exempting
    everything would trade a silent deletion for a silent accumulation. A row with
    no embedding cannot be returned by `staged_semantic_recall`, so trimming it
    loses nothing a query could have found.
    """
    from stackowl.memory.sqlite_bridge import _TURN_HISTORY_FLOOR, SqliteMemoryBridge

    bridge = SqliteMemoryBridge(tmp_db)
    over = _TURN_HISTORY_FLOOR + 25
    for i in range(over):
        await bridge.stage(_fact("webpage", f"url-{i}", i, embedded=False))

    remaining = await _count(tmp_db, "webpage")
    assert remaining <= _TURN_HISTORY_FLOOR, (
        f"{remaining} unsearchable rows survived {over} writes; the forensic-tail "
        f"bound has been removed rather than narrowed"
    )


@pytest.mark.asyncio
async def test_the_trim_uses_the_READERS_predicate_not_a_list_of_types(
    tmp_db: Any,
) -> None:
    """ONE SOURCE, ASKED — not a second copy of "which types are searchable".

    A hardcoded exempt-list is how the original premise rotted: it recorded a
    fact about the rest of the system and then could not notice the system
    changing. `embedding IS NOT NULL` is the reader's OWN membership test, so a
    new searchable source type is protected the day it starts being embedded,
    with nothing to remember.
    """
    import ast
    import inspect
    import textwrap

    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    # READ THE SQL, NOT THE SOURCE TEXT. The first version asserted over
    # `inspect.getsource(...)` and broke on the method's own DOCSTRING, which
    # NAMES conversation_summary while explaining why an exempt-list by name is
    # wrong. A guard a comment can break is the mirror of one a comment can
    # satisfy; ask the AST for the executable string literals.
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(SqliteMemoryBridge._trim_source_type))  # noqa: SLF001
    )
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    sql = " ".join(
        n.value for n in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    assert "embedding" in sql, (
        "the trim no longer consults embedding membership, so it has gone back to "
        "deciding searchability by a rule of its own"
    )
    assert "conversation_summary" not in sql, (
        "an exempt-list by NAME re-creates the premise that rotted: it states a "
        "fact about the rest of the system that nothing keeps true"
    )
