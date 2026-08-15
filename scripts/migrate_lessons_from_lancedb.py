"""One-time data move: the lessons corpus out of LanceDB and into SQLite.

RUN ONCE, then never again — the writers publish straight to SQLite after D08.2.
This exists because the corpus is REAL learned state (reflections the owl wrote
about its own work, mined tool heuristics, skill descriptions), not a cache that
can be regenerated. Dropping the dependency without moving the rows would silently
throw away 3,680 lessons and leave every recall returning nothing, which looks
exactly like "no lessons matched".

SAFE TO RE-RUN. The write is an upsert keyed on lesson_id, so a second run
overwrites identical rows rather than duplicating them. It reports what it found,
what it wrote and what it skipped, and verifies the count afterwards rather than
trusting the write.

IT DOES NOT DELETE ANYTHING. The LanceDB directory is left exactly as it was; the
backup and removal are a separate, deliberate step.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stackowl.db.migrations.runner import MigrationRunner  # noqa: E402
from stackowl.db.pool import DbPool  # noqa: E402
from stackowl.memory.sqlite_helpers import pack_embedding  # noqa: E402
from stackowl.paths import StackowlHome  # noqa: E402

_UPSERT = """
INSERT INTO lessons
    (lesson_id, source_type, source_ref, content, embedding, embedding_model, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(lesson_id) DO UPDATE SET
    source_type     = excluded.source_type,
    source_ref      = excluded.source_ref,
    content         = excluded.content,
    embedding       = excluded.embedding,
    embedding_model = excluded.embedding_model,
    metadata        = excluded.metadata
"""


def _read_lancedb(lancedb_dir: Path) -> list[dict[str, object]]:
    import lancedb

    db = lancedb.connect(str(lancedb_dir))
    if "lessons" not in db.table_names():
        print(f"no `lessons` table under {lancedb_dir} — nothing to move")
        return []
    table = db.open_table("lessons")
    total = table.count_rows()
    rows = table.search().limit(total + 1000).to_list()
    print(f"read {len(rows)} rows from LanceDB (table reports {total})")
    return rows


async def main() -> int:
    workspace = StackowlHome.workspace()
    lancedb_dir = workspace / "lancedb"
    db_path = workspace / "stackowl.db"
    print(f"lancedb : {lancedb_dir}")
    print(f"sqlite  : {db_path}")

    rows = _read_lancedb(lancedb_dir)
    if not rows:
        return 0

    # The table must exist before we can write into it. Idempotent.
    MigrationRunner(db_path=db_path).run()

    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        before = (await pool.fetch_all("SELECT COUNT(*) AS n FROM lessons", ()))[0]["n"]
        written = 0
        skipped_no_vec = 0
        by_type: dict[str, int] = {}
        async with pool.transaction() as tx:
            for raw in rows:
                embedding = list(raw.get("embedding") or [])
                if not embedding:
                    # A lesson with no vector can never be recalled — it would be a
                    # row with no reader, so it is counted and left behind.
                    skipped_no_vec += 1
                    continue
                metadata = raw.get("metadata") or "{}"
                if not isinstance(metadata, str):
                    metadata = json.dumps(metadata)
                source_type = str(raw.get("source_type") or "reflection")
                by_type[source_type] = by_type.get(source_type, 0) + 1
                await tx.execute(
                    _UPSERT,
                    (
                        str(raw["lesson_id"]),
                        source_type,
                        str(raw.get("source_ref") or ""),
                        str(raw.get("content") or ""),
                        pack_embedding([float(x) for x in embedding]),
                        "",
                        metadata,
                    ),
                )
                written += 1

        after = (await pool.fetch_all("SELECT COUNT(*) AS n FROM lessons", ()))[0]["n"]
        print(f"lessons rows: {before} -> {after} (wrote {written}, "
              f"skipped {skipped_no_vec} with no embedding)")
        print(f"by source_type: {by_type}")

        # Verify by READING BACK, not by trusting the write.
        sample = await pool.fetch_all(
            "SELECT lesson_id, LENGTH(embedding) AS blob_len FROM lessons LIMIT 3", ()
        )
        for s in sample:
            print(f"  sample {s['lesson_id']}: embedding blob {s['blob_len']} bytes "
                  f"({s['blob_len'] // 4} float32s)")
        # Compare against DISTINCT source ids, not the raw write count. LanceDB has
        # no primary key, so the source can legitimately hold several rows for one
        # lesson_id — 3 did, all copies of skill:learned/reks-research-specialist,
        # legacy residue from before publish() adopted merge_insert. SQLite's PRIMARY
        # KEY collapses them, which is the point: uniqueness becomes a schema
        # guarantee instead of something every writer has to remember.
        distinct_source_ids = len({str(r["lesson_id"]) for r in rows if r.get("embedding")})
        if after < distinct_source_ids:
            print(f"MISMATCH: {after} rows for {distinct_source_ids} distinct source "
                  "ids — investigate before removing LanceDB")
            return 1
        collapsed = written - distinct_source_ids
        if collapsed:
            print(f"collapsed {collapsed} duplicate row(s) that LanceDB allowed and "
                  "the primary key now prevents")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
