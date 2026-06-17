"""CONC-7 (F070) — committed_facts base table + committed_facts_fts must mutate
ATOMICALLY in both promote and delete.

Before the fix, promote did INSERT committed -> SELECT rowid -> INSERT fts as
three separate auto-committed statements, and delete did per-row FTS DELETE then
base DELETE the same way. A crash between the two left the FTS index and the base
table divergent (orphan FTS rows, or committed rows with no searchable index).

We drive a real mid-operation failure: a DbPool.transaction() whose second
statement raises must roll back the first (atomicity), and the bridge/promoter
must route the base+FTS pair through one transaction so a failure leaves NO
orphan in either table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import pack_embedding

pytestmark = pytest.mark.asyncio


async def _insert_staged(db: DbPool, *, fact_id: str, content: str) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id, content, "conversation", "sess-x", 0.9,
            datetime.now(UTC).isoformat(), 0, "staged", pack_embedding(None), None,
        ),
    )


async def _counts(db: DbPool, fact_id: str) -> tuple[int, int]:
    base = await db.fetch_all(
        "SELECT rowid AS r FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    if not base:
        return (0, 0)
    rowid = base[0]["r"]
    fts = await db.fetch_all(
        "SELECT rowid AS r FROM committed_facts_fts WHERE rowid = ?", (rowid,)
    )
    return (len(base), len(fts))


async def test_pool_exposes_transaction_primitive(tmp_db: DbPool) -> None:
    """DbPool must provide an atomic multi-statement transaction() (F070)."""
    assert hasattr(tmp_db, "transaction"), "DbPool must expose transaction()"


async def test_transaction_rolls_back_on_mid_statement_failure(tmp_db: DbPool) -> None:
    """The atomic primitive: a failing 2nd statement rolls back the 1st."""
    import sqlite3

    fid = str(uuid.uuid4())
    await _insert_staged(tmp_db, fact_id=fid, content="atomic probe")

    # The failure must be a genuine SQL error (a missing column), NOT an
    # AttributeError from a missing transaction() — so this fails loudly if the
    # primitive is absent rather than false-greening on AttributeError.
    with pytest.raises(sqlite3.OperationalError):
        async with tmp_db.transaction() as tx:
            await tx.execute(
                "INSERT INTO committed_facts (fact_id, content, embedding, "
                "embedding_model, committed_at, source_type, source_ref, tags, trust) "
                "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?)",
                (fid, "x", b"", "", "conversation", "s", "[]", "trusted"),
            )
            # Deliberately invalid statement → forces a rollback of the INSERT above.
            await tx.execute("INSERT INTO committed_facts (no_such_column) VALUES (1)")

    # The first INSERT must NOT have persisted.
    base, _fts = await _counts(tmp_db, fid)
    assert base == 0, "transaction must roll back the first statement on failure"


async def test_delete_routes_base_and_fts_through_one_transaction(tmp_db: DbPool) -> None:
    """delete() must mutate committed_facts + committed_facts_fts in ONE txn (F070):
    source-scan guards against a regression back to separate auto-committed
    statements that diverge on a crash between them."""
    import inspect

    from stackowl.memory import sqlite_bridge as bridge_mod

    src = inspect.getsource(bridge_mod.SqliteMemoryBridge.delete)
    assert "transaction(" in src, "delete() must wrap base+FTS in a transaction"

    promote_src = inspect.getsource(FactPromoter._promote_one)
    assert "transaction(" in promote_src, "_promote_one() must wrap base+FTS in a transaction"


async def test_promote_commits_base_and_fts_together(tmp_db: DbPool) -> None:
    fid = str(uuid.uuid4())
    await _insert_staged(tmp_db, fact_id=fid, content="the user prefers tabs")
    promoter = FactPromoter(tmp_db)
    assert await promoter.force_promote(fid) is True
    base, fts = await _counts(tmp_db, fid)
    assert base == 1 and fts == 1, f"base+fts must both land (base={base}, fts={fts})"


async def test_delete_removes_base_and_fts_together(tmp_db: DbPool) -> None:
    fid = str(uuid.uuid4())
    await _insert_staged(tmp_db, fact_id=fid, content="ephemeral fact")
    promoter = FactPromoter(tmp_db)
    assert await promoter.force_promote(fid) is True
    assert (await _counts(tmp_db, fid)) == (1, 1)

    bridge = SqliteMemoryBridge(tmp_db, semantic_search_enabled=False)
    await bridge.delete(fid)
    base, fts = await _counts(tmp_db, fid)
    assert base == 0 and fts == 0, f"base+fts must both be gone (base={base}, fts={fts})"
