"""Write a committed fact directly — the one fixture, for the tests that outlive the promoter.

WHY THIS EXISTS. Several guards over LIVE code used ``FactPromoter.force_promote()``
purely to get rows INTO ``committed_facts``: recall's FTS fallback ladder, its
embedding-drift degrade, scope isolation, and index/base atomicity. The promoter goes
in D08.2 seam 3 pass 4; those guarantees do not, because ``recall()`` and
``bridge.delete()`` are still called by the live ``memory`` tool.

WHY IT IS ONE MODULE AND NOT A COPY PER FILE. Three files needed the same insert, and
three copies of one rule is the shape this codebase keeps having to fix. It is also
the shape that would rot fastest here: the statement below is not obvious, and getting
it subtly wrong makes tests fail for reasons unrelated to what they assert.

WHAT IT MIRRORS, and why that matters more than it looks. The two statements are the
promoter's own (``fact_promoter.py`` ``_INSERT_COMMITTED_SQL`` + ``_INSERT_FTS_SQL``),
kept TOGETHER because a committed row whose FTS row is missing is invisible to recall.
``embedding``, ``embedding_model`` and ``tags`` are each NOT NULL, and the values here
are the promoter's (``pack_embedding``, ``""``, ``json.dumps([])``) rather than
plausible substitutes — three separate guesses were rejected by the schema before this
was read instead of assumed.

NOTE THE PLAIN ``INSERT``. The promoter uses ``INSERT OR IGNORE`` because it re-runs
over a queue. A fixture must not: OR IGNORE swallowed each of those three constraint
violations silently and left a test failing for a reason that had nothing to do with
what it was testing. Here the next schema change fails loudly instead of quietly
writing nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.memory.sqlite_helpers import pack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

_INSERT_COMMITTED = """
INSERT INTO committed_facts
    (fact_id, content, embedding, embedding_model, committed_at,
     source_type, source_ref, tags, trust, reinforcement_count, scope_key)
VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?)
"""

_INSERT_FTS = "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)"


async def insert_committed(
    db: DbPool,
    *,
    fact_id: str,
    content: str,
    source_type: str = "conversation",
    source_ref: str = "sess-x",
    trust: str = "self",
    scope_key: str | None = None,
    embedding: list[float] | None = None,
    embedding_model: str = "",
    reinforcement_count: int = 0,
) -> None:
    """Insert one committed fact and its FTS row, as the promoter did.

    ``embedding`` defaults to an empty packed vector — the shape a fact promoted
    without a computed vector had. Pass a real one when the test exercises the
    semantic path rather than the FTS fallback.
    """
    await db.execute(
        _INSERT_COMMITTED,
        (
            fact_id,
            content,
            pack_embedding(embedding or []),
            embedding_model,
            source_type,
            source_ref,
            json.dumps([]),
            trust,
            reinforcement_count,
            scope_key,
        ),
    )
    rows = await db.fetch_all(
        "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert rows, f"committed insert wrote nothing for {fact_id!r}"
    await db.execute(_INSERT_FTS, (rows[0]["rid"], content))
