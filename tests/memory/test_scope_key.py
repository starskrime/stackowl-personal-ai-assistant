"""Phase 2 (coding-capability build plan) — memory scope_key.

Covers the pure post-filter (filter_by_scope) and a real round-trip through
SqliteMemoryBridge.recall(), proving migration 0085's scope_key column actually
carries a fact's scope into committed_facts and back out through recall().

D08.2 seam 3 pass 4 — the round-trip used FactPromoter.force_promote() purely to
get rows INTO committed_facts. The promoter is gone; the guarantee is not, and it
is a TENANCY one: repo-b's fact must never leak into a repo-a recall, and recall()
is live (the `memory` tool calls it). So the fixture writes committed rows
directly through the shared ``_committed_fact_fixture`` helper, which mirrors the
promoter's own INSERT + FTS-sync statements — the point being that the index and
the base table stay in step, which is what recall's FTS path depends on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.models import MemoryRecord
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import filter_by_scope
from tests.memory._committed_fact_fixture import insert_committed


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


@pytest.mark.asyncio
async def test_scope_key_carries_into_committed_and_out_through_recall(tmp_db: DbPool) -> None:
    repo_fid = str(uuid.uuid4())
    global_fid = str(uuid.uuid4())
    other_fid = str(uuid.uuid4())
    # All three share the token "widget" (FTS5 MATCH is token-based, not
    # substring — every content string must contain the actual query word).
    await insert_committed(
        tmp_db, fact_id=repo_fid, content="widget repo-a build command", scope_key="repo-a"
    )
    await insert_committed(
        tmp_db, fact_id=global_fid, content="widget global preference", scope_key=None
    )
    await insert_committed(
        tmp_db, fact_id=other_fid, content="widget other repo-b note", scope_key="repo-b"
    )

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
