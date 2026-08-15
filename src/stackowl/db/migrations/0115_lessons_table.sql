-- Migration 0115 — the lessons corpus moves from LanceDB into SQLite.
--
-- WHY. Bakir, 2026-08-14: "I do not want lancedb at all because it is heavy and
-- does not support all platforms." MEASURED rather than taken on faith: the
-- `lancedb` package is 100MB and drags in `pyarrow` at 136MB — 236MB of
-- dependency for one 5.4MB corpus. For contrast `kuzu` is 19MB and `numpy`,
-- already a direct dependency, is 30MB. The objection lands on LanceDB
-- specifically, and on a Jetson-class box (the only dev machine here) the wheel
-- availability problem it names is real.
--
-- WHAT MOVES. Only this table. Of LanceDB's three tables, `committed_facts`
-- (4,985 vectors) and `committed_facts_meta` are ALREADY DEAD — semantic recall
-- hydrates its content from SQLite `committed_facts`, empty since migration 0112,
-- so those vectors hydrate to nothing. `lessons` is the live one: 3,680 rows at
-- 384 dimensions — 3,429 reflections, 198 skills, 53 tool heuristics — queried on
-- every turn, and one of the two blocks D01.1 deliberately KEPT in the system
-- prompt. So this is a replacement, not a deletion.
--
-- WHY A PLAIN TABLE IS ENOUGH, and this is the part worth checking rather than
-- assuming: 3,680 x 384 float32 is 5.4MB, and a brute-force scan is one matmul of
-- ~1.4M FLOPs — sub-millisecond in numpy. An ANN index buys nothing at this size
-- and costs 236MB. It is also EXACT where an ANN is approximate, so recall
-- quality goes up rather than down.
--
-- THE EMBEDDING IS A float32 LITTLE-ENDIAN BLOB, written and read through
-- `memory/sqlite_helpers.pack_embedding` / `unpack_embedding` — the same pair the
-- fact store already used, so there is ONE packing rule rather than a second one
-- that can drift from it.
--
-- `embedding_model` IS STORED PER ROW, deliberately. The fact store learned this
-- the hard way (F062): when the active embedder changes, vectors written under
-- the old one are not comparable, and a store that cannot say which model wrote a
-- row can only guess. Recording it per row lets a mismatched corpus degrade
-- honestly instead of returning confident nonsense.
--
-- IDEMPOTENT: CREATE TABLE / CREATE INDEX IF NOT EXISTS. No VACUUM — migration
-- 0112 failed on "cannot VACUUM from within a transaction" because the runner
-- wraps each migration in one.

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id       TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL,
    source_ref      TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding       BLOB NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- `source_filter` is the only predicate search() takes, and the reflection tier
-- is 93% of the corpus — so filtering to skills or tool heuristics without an
-- index would scan nearly everything to find a few dozen rows.
CREATE INDEX IF NOT EXISTS idx_lessons_source_type ON lessons(source_type);
