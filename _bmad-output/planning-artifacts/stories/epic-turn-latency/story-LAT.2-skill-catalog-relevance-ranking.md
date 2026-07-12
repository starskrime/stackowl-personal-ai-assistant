# Story LAT.2: Hybrid (semantic + keyword) skill-catalog retrieval, don't format-all-then-truncate

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Revision note

Original scope (Phase 1 below) was a single call-site swap to `semantic_recall()`. User reviewed and asked for a deeper architectural pass — dynamic hybrid (semantic + keyword) retrieval over the skill catalog, not just ranked-embedding-only. Deep research (see Dev Notes) confirmed the instinct is right (RRF-fused BM25 + cosine catches exact skill/tool-name hits that a blended embedding blurs) and found the exact reusable pattern already live in this codebase (`committed_facts_fts`, memory/) — copy it, don't invent a new mechanism. Both phases are now in scope for this story, built together per explicit direction ("build both").

## Story

As the StackOwl pipeline,
I want the global skill-catalog block in `assemble.py` to retrieve only the top-K relevant skills via hybrid (BM25 keyword + cosine embedding, RRF-fused) search instead of dumping all 335 enabled skills,
so that `standard`-class turns stop paying for a full-row fetch + format + discard cycle, and relevant skills surface via either exact-term match or semantic similarity — whichever fires.

## Acceptance Criteria

### Phase 1 — ranked retrieval (embedding-only fallback tier)

1. For a `standard`-intent turn with a populated `state.query_embedding`, the global-catalog branch in `assemble.py` no longer calls `store.list_enabled()` unconditionally — it retrieves a bounded top-K candidate set (K ~15-25, comfortably above what `instruction_injector`'s render `cap` actually uses), not all 335 rows.
2. For a `conversational`/`clarify`-class turn (`TOOL_FREE_CLASSES`), the global-catalog branch is still skipped entirely (unchanged — this early-exit already exists at `assemble.py:144` and must not regress).
3. When neither `state.query_embedding` nor `state.query_text` is usable (both absent), fall back to the existing `store.list_enabled()` path byte-identically (no behavior change for that case).
4. No regression to `score_owned_skills`-ranked owned skills (assemble.py:154-159) — untouched by this change.

### Phase 2 — hybrid keyword + semantic fusion

5. A new `skills_fts` FTS5 virtual table exists, indexing each skill's retrieval surface (`name + description + when_to_use + summary`, NOT the full `body_text` — body is noise for "should this surface," per the existing embedding-composition precedent at `assembly.py:343-356`).
6. `skills_fts` stays in sync with the `skills` table on every write path — `store.upsert`, `set_summary`, and delete — mirroring the exact sync pattern already used for `committed_facts_fts` (`sqlite_bridge.py:318`). No skill can go enabled/updated without its FTS row reflecting the change.
7. `SkillIndexStore.hybrid_recall(query_text, query_embedding, *, limit=K)` exists: runs the existing cosine pass (reuse `semantic_recall`'s scoring, don't duplicate it) and one `skills_fts MATCH ? ORDER BY bm25(skills_fts)` pass, fuses the two rank lists via Reciprocal Rank Fusion (`score = Σ 1/(k+rank_i)`, k≈60, no score-scale normalization needed), returns top-K `(Skill, fused_score)`.
8. `assemble.py`'s global-catalog branch calls `hybrid_recall` (not bare `semantic_recall`) as its primary path when both `query_text` and `query_embedding` are available; degrades to embedding-only `semantic_recall` when only the embedding is available; degrades to `list_enabled()` only when neither is available (three-tier fallback, not a hard cutover).
9. A skill with `enabled=1` but no embedding row is still reachable via the keyword (BM25) side of `hybrid_recall`, not just the `list_enabled()` fallback — this is strictly better reachability than Phase 1 alone.
10. `instruction_injector.render`'s "catalog truncated by budget" WARNING fires less often / on a much smaller input set than 335, matching Phase 1's improvement.
11. Keyword tokenization/matching is entirely FTS5's job (no hand-rolled tokenizer, no hardcoded stopword/keyword lists) — satisfies this repo's no-hardcoded-language-list convention for free.
12. Migration is idempotent and runnable across all existing DBs (per this repo's DB-problem convention) — `CREATE VIRTUAL TABLE IF NOT EXISTS`, plus a one-time backfill of existing enabled skills into `skills_fts` on first apply.

## Tasks / Subtasks

- [ ] Task 1: Migration — `skills_fts` FTS5 table (AC: #5, #12)
  - [ ] New migration `0081_skills_fts.sql` (or next available number — check `src/stackowl/db/migrations/` for the actual next slot at implementation time), `CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(name, description, when_to_use, summary, content='skills', content_rowid='skill_id')` (contentless/external-content FTS5 table pattern — mirror whatever `committed_facts_fts`'s migration actually does structurally, don't diverge without reason)
  - [ ] Backfill: populate `skills_fts` from existing `skills` rows in the same migration (idempotent — safe to re-run)
- [ ] Task 2: Sync `skills_fts` on every write path (AC: #6)
  - [ ] `store.upsert` — insert/update the FTS row alongside the skills row, in the same transaction
  - [ ] `set_summary` (or wherever `summary` is written) — update the FTS row's `summary` column
  - [ ] Delete path — remove the FTS row when a skill row is deleted
  - [ ] Mirror `sqlite_bridge.py:318`'s sync approach exactly; don't invent a new sync mechanism
- [ ] Task 3: `hybrid_recall()` (AC: #7)
  - [ ] Add `async def hybrid_recall(self, query_text: str, query_embedding: Sequence[float], *, limit: int) -> list[tuple[Skill, float]]` to `SkillIndexStore`
  - [ ] Cosine pass: reuse `semantic_recall`'s existing scoring logic (don't duplicate the cosine math — factor out if needed, or call through)
  - [ ] Keyword pass: `SELECT skill_id, bm25(skills_fts) FROM skills_fts WHERE skills_fts MATCH ? ORDER BY bm25(skills_fts) LIMIT ?`
  - [ ] RRF fusion: `score(skill) = Σ over passes where skill appears: 1/(60 + rank_in_that_pass)`; sort by fused score descending; take top `limit`
- [ ] Task 4: Wire the three-tier fallback into `assemble.py` (AC: #1, #3, #8)
  - [ ] `query_text` and `query_embedding` both present → `hybrid_recall`
  - [ ] Only `query_embedding` present → `semantic_recall` (Phase 1 behavior)
  - [ ] Neither present → `list_enabled()` (today's behavior, unchanged)
  - [ ] Make `K` a named constant, not a magic number inline
- [ ] Task 5: Tests (AC: #1-#11)
  - [ ] `skills_fts` sync: upsert a skill → assert it's findable via `MATCH`; update its summary → assert FTS reflects the update; delete → assert FTS row gone
  - [ ] `hybrid_recall` returns a skill matched ONLY by an exact keyword (e.g. a rare tool name in its `description`) that a semantically-unrelated query embedding would not surface via cosine alone — this is the core regression guard proving the keyword side does real work, not just a decoration
  - [ ] `hybrid_recall` returns a skill matched ONLY by semantic similarity (paraphrase, no shared keyword) — proves the embedding side still does real work
  - [ ] Three-tier fallback: each of the three conditions (both present / embedding-only / neither) routes to the correct method — assert via call spy, not just output correctness
  - [ ] `conversational` turn → catalog branch still not entered at all (regression guard, unchanged from Phase 1)
  - [ ] Migration idempotency: running `0081_skills_fts.sql` twice against the same DB does not error and does not duplicate FTS rows

## Dev Notes

- **Root cause:** `assemble.py:162-168`'s global-catalog branch does `store.list_enabled()` — fetches all 335 enabled rows FULL (body_text + embedding blobs included, `store.py:199`, `_row_to_skill`, store.py:589-599) solely to render truncated names. `instruction_injector.render` (instruction_injector.py:172-191) then truncates to the token budget and drops the rest ("catalog truncated by budget"). Worse than a naive unranked-fetch reading suggests: it's a full-row hydration (bodies + embedding blobs) just to emit a name list.
- **What already exists (confirmed via research, don't rebuild):**
  - Skills already have layered short-text fields: `description` + `when_to_use` (always present, manifest frontmatter) and an optional `summary` (condensed playbook, `summary_source`/`summary_body_hash` columns, migration `0050_skill_summary.sql`). The injector already prefers `summary` when present (`instruction_injector.py:52-53`, `_resolve_text = summary or f"{description} — {when_to_use}"`).
  - What's embedded is NOT the full body — it's a composed blend `name + description + when_to_use + body[:1500 bytes]` (`assembly.py:343-356`, `_BODY_EMBED_BYTES=1500`), already weighted toward the retrieval surface over body detail.
  - `SkillIndexStore.semantic_recall(query_embedding, *, limit=3, min_similarity=0.0)` (store.py:351) — pure cosine over `enabled=1 AND embedding IS NOT NULL` rows, already used by `classify.py:397` and `delivery_gate.py:1282`.
  - **No FTS5/keyword search exists for skills today**, but this repo already runs exactly this pattern for a different table: `committed_facts_fts USING fts5(content)` (`0014_memory_tables.sql:36`), synced at the application layer on write/delete (`sqlite_bridge.py:318`), queried via `committed_facts_fts MATCH ? ORDER BY bm25(committed_facts_fts)` (`sqlite_helpers.py:131-134`). Copy this pattern for skills — do not invent a new keyword-search mechanism.
- **NOT the problem (do not touch / do not regress):**
  - Early-exit for conversational turns already exists: `assemble.py:144` gates the whole block on `state.intent_class not in TOOL_FREE_CLASSES` (`state.py:18`). A bare "hi" (conversational) already skips this entirely.
  - Owned skills are already ranked via `score_owned_skills(...)` using `state.query_embedding` (assemble.py:154-159, cosine + hysteresis) — only the global-catalog branch bypasses ranking. Untouched by this story.
- **Why hybrid over embedding-only:** keyword (BM25) catches exact/rare-token matches (a specific skill or tool name, an acronym) that a 1500-byte blended embedding can blur or dilute; embedding catches semantic paraphrase matches keyword search misses entirely. RRF fusion (`score = Σ 1/(k+rank_i)`, k≈60) needs no score-scale normalization between the two heterogeneous scoring systems (BM25 scores and cosine similarities aren't on the same scale) — this is the standard reason RRF is preferred over a hand-tuned weighted sum here.
- **Why NOT a rerank stage (rejected):** a cross-encoder/LLM rerank stage earns its latency cost at 10k+ candidates; at 335 skills, top-K straight from hybrid fusion is sufficient — added rerank complexity would be over-engineering for this corpus size. Do not add one.
- **Why index description/when_to_use/summary, not full body, in FTS:** mirrors the embedding-composition precedent (`assembly.py:343-356` already weights retrieval surface over body) — body-text keyword matches would be noisy signal for "is this skill relevant," not a useful addition.
- **Rejected alternatives:**
  - Per-session TTL cache of `list_enabled()` rows — treats the DB-fetch symptom, not the unranked-retrieval root cause. Superseded entirely by hybrid retrieval.
  - New LanceDB-backed skill index — the codebase's own docstring defers this to "larger corpora"; 335 rows doesn't justify a new vector-store dependency when SQLite FTS5 (already in the codebase, already proven on `committed_facts_fts`) covers the keyword side with zero new dependency.
  - Shipping Phase 2 speculatively without Phase 1 landing first — research's original recommendation was to sequence (ship the cheap embedding-only swap, gate FTS5-hybrid on evidence of embedding-only recall misses). Explicitly overridden by direction to build both now; the sequencing caveat is preserved here as context, not as a blocker.

### Project Structure Notes

- One new migration (`skills_fts` FTS5 virtual table + backfill).
- `store.py` grows one new method (`hybrid_recall`) and gets sync hooks added to existing write paths (`upsert`, `set_summary`, delete) — no new module, mirrors the existing `sqlite_bridge.py` FTS-sync pattern already used for `committed_facts_fts`.
- Single call-site change in `assemble.py`'s global-catalog branch, now a three-tier fallback instead of a single call.
- No new external dependency — SQLite FTS5 is part of the SQLite build already in use (confirm at implementation time that the driver was compiled with FTS5, matching however `committed_facts_fts` already assumes this).

### References

- [Source: src/stackowl/pipeline/steps/assemble.py#L141-L185] — global-catalog branch, `TOOL_FREE_CLASSES` early-exit, owned-skill scoring
- [Source: src/stackowl/skills/store.py#L199] — `list_enabled()`, current (unranked, full-row) fetch path — Phase 1/2's outermost fallback tier
- [Source: src/stackowl/skills/store.py#L351] — `semantic_recall()`, reused for both Phase 1 and as Phase 2's middle fallback tier + cosine half of `hybrid_recall`
- [Source: src/stackowl/skills/store.py#L589-599] — `_row_to_skill`, per-row embedding unpack cost `list_enabled()` currently pays for all 335 rows
- [Source: src/stackowl/skills/instruction_injector.py#L52-53] — `_resolve_text`, existing summary-over-description preference
- [Source: src/stackowl/skills/instruction_injector.py#L172-191] — `render()`, where the "catalog truncated by budget" WARNING fires
- [Source: src/stackowl/pipeline/state.py#L18,197] — `TOOL_FREE_CLASSES`, `query_embedding`
- [Source: src/stackowl/skills/assembly.py#L343-356] — embedding composition (`_BODY_EMBED_BYTES=1500`), precedent for what to index in `skills_fts`
- [Source: src/stackowl/db/migrations/0050_skill_summary.sql] — `summary`/`summary_source`/`summary_body_hash` schema precedent
- [Source: src/stackowl/db/migrations/0014_memory_tables.sql#L36] — `committed_facts_fts` FTS5 table, the exact pattern to copy
- [Source: src/stackowl/memory/sqlite_bridge.py#L318] — FTS sync-on-write pattern to mirror for `store.upsert`/`set_summary`/delete
- [Source: src/stackowl/memory/sqlite_helpers.py#L131-134] — `MATCH ... ORDER BY bm25(...)` query pattern to mirror in `hybrid_recall`

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (research); implementer TBD

### Debug Log References

### Completion Notes List

### File List
