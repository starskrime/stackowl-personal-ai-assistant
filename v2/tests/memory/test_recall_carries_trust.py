"""Task 9: recalled MemoryRecord must carry the trust tier from committed_facts.

Two key test cases:
  1. untrusted fact (default) — passes with or without the fix (establishes baseline).
  2. trusted fact — FAILS before the fix because the recall SELECTs don't fetch `trust`,
     so row_to_record falls back to the model default "untrusted" even though the DB row
     says "trusted".

FTS seeding pattern: committed_facts_fts is a rowid-based FTS5 table:
    CREATE VIRTUAL TABLE committed_facts_fts USING fts5(content)
So inserts are: INSERT INTO committed_facts_fts(rowid, content) VALUES (rid, ?)
where rid = rowid of the corresponding committed_facts row.
"""

from __future__ import annotations

import pytest

from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

pytestmark = pytest.mark.asyncio


async def _seed_committed_fts(tmp_db, *, fact_id: str, content: str, trust: str) -> None:
    """Insert one committed_fact and FTS-index it.

    FTS5 table schema: committed_facts_fts(content)  — content column only.
    Indexed via rowid matching committed_facts.rowid.
    """
    await tmp_db.execute(
        "INSERT INTO committed_facts "
        "(fact_id, content, embedding, embedding_model, committed_at, "
        "source_type, source_ref, tags, trust) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            fact_id, content, b"", "m",
            "2026-01-01T00:00:00+00:00",
            "webpage", "https://example.com", "[]", trust,
        ),
    )
    # Fetch rowid so FTS entry links to the right committed_facts row.
    rows = await tmp_db.fetch_all(
        "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    assert rows, f"committed_facts insert failed for fact_id={fact_id}"
    rowid = rows[0]["rid"]
    await tmp_db.execute(
        "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)",
        (rowid, content),
    )


async def test_recalled_record_carries_untrusted(tmp_db) -> None:
    """Baseline: an untrusted fact recalled via FTS5 carries trust='untrusted'."""
    await _seed_committed_fts(
        tmp_db,
        fact_id="f-untrusted",
        content="external claim about widgets",
        trust="untrusted",
    )
    bridge = SqliteMemoryBridge(tmp_db)
    records = await bridge.recall("widgets claim", limit=5)
    assert records, "FTS5 recall must return the seeded fact"
    match = next((r for r in records if r.fact_id == "f-untrusted"), None)
    assert match is not None, f"expected f-untrusted in results; got {[r.fact_id for r in records]}"
    assert match.trust == "untrusted"


async def test_recalled_trusted_fact_reads_trust_from_db(tmp_db) -> None:
    """Core: a trusted fact recalled via FTS5 must carry trust='trusted'.

    This test FAILS before the fix because the recall SELECTs omit `trust` —
    row_to_record falls back to the MemoryRecord default ("untrusted") instead
    of reading the real value from the DB row.
    """
    await _seed_committed_fts(
        tmp_db,
        fact_id="f-trusted",
        content="trusted system knowledge about protocols",
        trust="trusted",
    )
    bridge = SqliteMemoryBridge(tmp_db)
    records = await bridge.recall("system protocols knowledge", limit=5)
    assert records, "FTS5 recall must return the seeded fact"
    match = next((r for r in records if r.fact_id == "f-trusted"), None)
    assert match is not None, f"expected f-trusted in results; got {[r.fact_id for r in records]}"
    # This assertion catches the bug: the SELECT must fetch trust from the DB row,
    # not rely on MemoryRecord's default field value.
    assert match.trust == "trusted", (
        f"trust must be 'trusted' (read from DB), got {match.trust!r}; "
        "the recall SELECT is missing cf.trust / trust column"
    )


async def test_recalled_self_fact_reads_trust_from_db(tmp_db) -> None:
    """Bonus: self-generated facts carry trust='self' after recall."""
    await _seed_committed_fts(
        tmp_db,
        fact_id="f-self",
        content="owl self-generated insight about architecture",
        trust="self",
    )
    bridge = SqliteMemoryBridge(tmp_db)
    records = await bridge.recall("architecture insight", limit=5)
    assert records, "FTS5 recall must return the seeded fact"
    match = next((r for r in records if r.fact_id == "f-self"), None)
    assert match is not None, f"expected f-self in results; got {[r.fact_id for r in records]}"
    assert match.trust == "self", (
        f"trust must be 'self' (read from DB), got {match.trust!r}"
    )
