"""Phase 2 (coding-capability build plan) — memory scope_key.

Covers the pure post-filter (filter_by_scope) and a real round-trip through
SqliteMemoryBridge.stage()/recall() + FactPromoter, proving migration 0085's
scope_key column actually carries a fact's scope from staged_facts through
promotion into committed_facts and back out through recall().
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.models import MemoryRecord
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import filter_by_scope


def _record(fact_id: str, scope_key: str | None) -> MemoryRecord:
    return MemoryRecord(
        fact_id=fact_id,
        content="x",
        embedding=[],
        embedding_model="",
        committed_at=datetime.now(UTC),
        source_type="conversation",
        source_ref="s",
        scope_key=scope_key,
    )


def test_filter_by_scope_none_is_noop() -> None:
    records = [_record("a", "repo-1"), _record("b", None)]
    assert filter_by_scope(records, None) == records


def test_filter_by_scope_keeps_matching_and_global() -> None:
    records = [_record("a", "repo-1"), _record("b", None), _record("c", "repo-2")]
    kept = filter_by_scope(records, "repo-1")
    assert {r.fact_id for r in kept} == {"a", "b"}


async def _insert_staged_scoped(
    db: DbPool, *, fact_id: str, content: str, scope_key: str | None
) -> None:
    await db.execute(
        """INSERT INTO staged_facts (
               fact_id, content, source_type, source_ref, confidence,
               staged_at, reinforcement_count, status, embedding, embedding_model,
               scope_key
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fact_id, content, "conversation", "sess-x", 0.9,
            datetime.now(UTC).isoformat(), 0, "staged", None, None, scope_key,
        ),
    )


@pytest.mark.asyncio
async def test_scope_key_carries_through_promotion_and_recall(tmp_db: DbPool) -> None:
    repo_fid = str(uuid.uuid4())
    global_fid = str(uuid.uuid4())
    other_fid = str(uuid.uuid4())
    # All three share the token "widget" (FTS5 MATCH is token-based, not
    # substring — every content string must contain the actual query word).
    await _insert_staged_scoped(
        tmp_db, fact_id=repo_fid, content="widget repo-a build command", scope_key="repo-a"
    )
    await _insert_staged_scoped(
        tmp_db, fact_id=global_fid, content="widget global preference", scope_key=None
    )
    await _insert_staged_scoped(
        tmp_db, fact_id=other_fid, content="widget other repo-b note", scope_key="repo-b"
    )

    promoter = FactPromoter(tmp_db)
    assert await promoter.force_promote(repo_fid) is True
    assert await promoter.force_promote(global_fid) is True
    assert await promoter.force_promote(other_fid) is True

    bridge = SqliteMemoryBridge(tmp_db, semantic_search_enabled=False)
    scoped = await bridge.recall("widget", limit=10, scope_key="repo-a")
    ids = {r.fact_id for r in scoped}
    assert repo_fid in ids, "repo-a's own fact must be visible"
    assert global_fid in ids, "a global (unscoped) fact must stay visible in every scope"
    assert other_fid not in ids, "repo-b's fact must NOT leak into a repo-a recall"

    # No scope_key given ⇒ no filter ⇒ every fact is visible (byte-identical to
    # pre-Phase-2 behavior).
    unscoped = await bridge.recall("widget", limit=10)
    unscoped_ids = {r.fact_id for r in unscoped}
    assert {repo_fid, global_fid, other_fid} <= unscoped_ids
