"""F063 (P1) — contradiction scan must be incremental + ANN-bounded.

``_scan_pairs`` was O(n^2) over the FULL committed corpus every run, loading
every embedding BLOB into RAM (``load_committed_for_scan`` had no WHERE/LIMIT).
~50M cosine ops at 10k facts.

Fix: a ``last_contradiction_scan_at`` watermark bounds the LEFT side to
new/changed facts only; an injected ANN ``neighbour_lookup`` supplies the RIGHT
side (a new fact IS compared against the WHOLE corpus via ANN, not just other
new facts). Brute-force fallback is preserved for small-N / no-LanceDB.
"""

from __future__ import annotations

import pytest

from stackowl.memory.contradiction_detector import ContradictionDetector
from stackowl.memory.models import MemoryRecord

pytestmark = pytest.mark.asyncio


def _rec(fact_id: str, vec: list[float], source: str = "conversation_fact") -> MemoryRecord:
    from datetime import UTC, datetime

    return MemoryRecord(
        fact_id=fact_id,
        content=f"content-{fact_id}",
        embedding=vec,
        embedding_model="hash-v1-384d",
        committed_at=datetime.now(UTC),
        source_type=source,
        source_ref="s1",
        tags=[],
        trust="self",
    )


async def test_detect_incremental_only_searches_new_facts_finds_old_dup() -> None:
    """The ANN lookup is called ONLY for the new fact, yet still finds the old dup."""
    old_a = _rec("old-a", [1.0, 0.0, 0.0], source="manual")
    old_b = _rec("old-b", [0.0, 1.0, 0.0], source="manual")
    new = _rec("new-1", [1.0, 0.0, 0.0], source="conversation_fact")  # dup of old_a, diff source

    searched: list[str] = []

    async def neighbour_lookup(fact: MemoryRecord) -> list[MemoryRecord]:
        searched.append(fact.fact_id)
        # ANN over the WHOLE corpus returns the near-identical old fact.
        return [old_a, old_b]

    detector = ContradictionDetector()
    reports = await detector.detect_incremental([new], neighbour_lookup)

    # Only the NEW fact drove an ANN search (the old corpus was NOT rescanned).
    assert searched == ["new-1"]
    # The cross-source near-identical pair is still flagged.
    assert any(
        {r.fact_id_a, r.fact_id_b} == {"new-1", "old-a"} for r in reports
    ), f"must find new↔old contradiction; got {reports}"


async def test_detect_incremental_dedupes_symmetric_pairs() -> None:
    """A symmetric pair surfaced from two directions is reported once."""
    a = _rec("a", [1.0, 0.0], source="manual")
    b = _rec("b", [1.0, 0.0], source="conversation_fact")

    async def neighbour_lookup(fact: MemoryRecord) -> list[MemoryRecord]:
        return [a, b]  # each fact sees both → would double-report without dedupe

    detector = ContradictionDetector()
    reports = await detector.detect_incremental([a, b], neighbour_lookup)
    pairs = [frozenset({r.fact_id_a, r.fact_id_b}) for r in reports]
    assert pairs.count(frozenset({"a", "b"})) == 1


async def test_load_committed_since_filters_by_watermark(tmp_db) -> None:
    """load_committed_for_scan(since=...) only returns facts committed after it."""
    from datetime import UTC, datetime, timedelta

    import numpy as np

    from stackowl.memory.dream_worker_helpers import load_committed_for_scan

    old_t = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    new_t = datetime.now(UTC).isoformat()
    vec = np.array([0.1, 0.2], dtype="<f4").tobytes()
    for fid, t in (("old", old_t), ("new", new_t)):
        await tmp_db.execute(
            """INSERT INTO committed_facts
                   (fact_id, content, embedding, embedding_model, source_type,
                    source_ref, tags, trust, committed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, f"c-{fid}", vec, "hash-v1-384d", "conversation_fact", "s1", "[]", "self", t),
        )

    cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    since = await load_committed_for_scan(tmp_db, since=cutoff)
    ids = {r.fact_id for r in since}
    assert ids == {"new"}, f"watermark must exclude old facts; got {ids}"

    all_facts = await load_committed_for_scan(tmp_db)
    assert {r.fact_id for r in all_facts} == {"old", "new"}


async def test_watermark_round_trip(tmp_db) -> None:
    """The watermark read/write helpers persist + advance the scan cutoff."""
    from stackowl.memory.dream_worker_helpers import (
        get_contradiction_watermark,
        set_contradiction_watermark,
    )

    assert await get_contradiction_watermark(tmp_db) is None
    await set_contradiction_watermark(tmp_db, "2026-06-14T00:00:00+00:00")
    assert await get_contradiction_watermark(tmp_db) == "2026-06-14T00:00:00+00:00"


async def test_same_millisecond_fact_is_not_skipped(tmp_db) -> None:
    """A later fact landing at the SAME committed_at ms as the watermark is scanned.

    Regression for the C3 fast-follow watermark blind spot: ``committed_at`` is
    millisecond precision and SQLite ``'now'`` is constant within a statement, so a
    second promotion in the same ms as an already-watermarked batch was permanently
    skipped by the STRICT ``committed_at > since`` filter. The fix scans
    ``committed_at >= since`` while excluding the fact_ids already processed at the
    boundary timestamp — so no fact is ever skipped and the boundary pair is not
    re-emitted.
    """
    import numpy as np

    from stackowl.memory.dream_worker_helpers import (
        get_contradiction_boundary_ids,
        get_contradiction_watermark,
        load_committed_for_scan,
        set_contradiction_watermark,
    )

    same_ms = "2026-06-14T12:00:00.123000+00:00"
    vec = np.array([0.1, 0.2], dtype="<f4").tobytes()

    async def _insert(fid: str) -> None:
        await tmp_db.execute(
            """INSERT INTO committed_facts
                   (fact_id, content, embedding, embedding_model, source_type,
                    source_ref, tags, trust, committed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fid, f"c-{fid}", vec, "hash-v1-384d", "conversation_fact", "s1", "[]", "self", same_ms),
        )

    # --- First scan: fact A lands at same_ms; watermark advances to it. ---
    await _insert("A")
    wm = await get_contradiction_watermark(tmp_db)
    boundary = await get_contradiction_boundary_ids(tmp_db)
    batch1 = await load_committed_for_scan(tmp_db, since=wm, exclude_ids=boundary)
    assert {r.fact_id for r in batch1} == {"A"}
    # Mirror the dream_worker advance site: newest committed_at + its fact_ids.
    newest = max(r.committed_at for r in batch1)
    boundary_ids = [r.fact_id for r in batch1 if r.committed_at == newest]
    await set_contradiction_watermark(tmp_db, newest.isoformat(), boundary_ids=boundary_ids)

    # --- Second scan: fact B lands at the EXACT SAME committed_at ms. ---
    await _insert("B")
    wm2 = await get_contradiction_watermark(tmp_db)
    boundary2 = await get_contradiction_boundary_ids(tmp_db)
    batch2 = await load_committed_for_scan(tmp_db, since=wm2, exclude_ids=boundary2)
    ids2 = {r.fact_id for r in batch2}
    # B MUST be scanned (the same-ms blind spot is gone); A MUST NOT re-appear.
    assert ids2 == {"B"}, f"same-ms fact must be scanned, boundary fact excluded; got {ids2}"
