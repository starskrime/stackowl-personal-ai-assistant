# Story LAT.1: Batch the boot-time skill-assembly reads (1000+ sequential DB round-trips → 3)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the StackOwl boot sequence,
I want `SkillsAssembly.build()`'s three back-fill passes to read the skill index once per pass instead of once per skill,
so that every gateway/core restart doesn't pay ~1000+ sequential SQLite round-trips before the platform is ready.

## Correction vs. initial framing

This was originally suspected as a **per-turn** cost (triggered by every "hi"). Research found that's wrong: the per-turn assemble step (`assemble.py:148`) already batches correctly (`get_many_by_name` + `list_enabled`, one query each — see Story LAT.2 for that path's real issue). The N sequential `store.get` pairs actually come from **boot-time** `SkillsAssembly.build()`, which happens to run in the same core-process log window as the first message after a restart. This is a co-occurrence misattribution — same class of mistake flagged in this project's own incident history (precise-row-count gate, not mere temporal adjacency, when attributing cause). The fix target changed accordingly; the perf problem itself (real, ~1000+ round-trips) is unchanged.

## Acceptance Criteria

1. `SkillsAssembly.build()`'s three passes (`_embed_missing`, `_summarize_missing`, `_publish_to_lessons`) each issue exactly one DB query to read the skill index, not one query per skill.
2. Each pass's skip/write decision (based on `skill_id`, `embedding`/`embedding_model`, `summary_body_hash`, `lessons_published_hash`) is unchanged — behavior is byte-identical, only the read pattern changes.
3. A fresh snapshot is taken per pass (3 total queries for 3 passes), not one shared snapshot across all three — so `_publish_to_lessons` correctly observes embeddings that `_embed_missing` wrote earlier in the same `build()` call.
4. Boot-time log volume/duration for skill assembly drops measurably (from ~1000+ `pool.fetch_all`/`retry_once` pairs to 3, for a 338-skill store).
5. No change to the turn-time assemble path (`assemble.py:148`'s `get_many_by_name`/`list_enabled`) — that path is out of scope for this story (see LAT.2).

## Tasks / Subtasks

- [ ] Task 1: Add a batch-index method to the store (AC: #1)
  - [ ] Add `async def index_by_source_name(self) -> dict[tuple[str, str], Skill]` to `SkillIndexStore` (`store.py`), mirroring the existing `list_enabled()` shape — one `WHERE owner_id=?` query, keyed by `(source, name)` (not by `name` alone — a builtin and a learned skill can share a name, so don't reuse `get_many_by_name`, which collapses duplicates by `_SOURCE_PRIORITY`)
- [ ] Task 2: Wire the three passes to use the snapshot (AC: #2, #3)
  - [ ] `_embed_missing` (`assembly.py:154-155`): take one snapshot at pass start, replace the per-skill `store.get(source, name)` with a dict lookup
  - [ ] `_summarize_missing` (`assembly.py:277-280`): same pattern, its own fresh snapshot
  - [ ] `_publish_to_lessons` (`assembly.py:225-229`): same pattern, its own fresh snapshot (taken after `_embed_missing` has run, so it sees the embeddings that pass wrote)
- [ ] Task 3: Tests (AC: #1-#4)
  - [ ] `index_by_source_name()` returns all rows for the owner in one query — assert query count via a spy/counter on the DB pool, not just result correctness
  - [ ] Each of the three passes issues exactly 1 index read (assert via query-count spy) regardless of skill count (test with e.g. 50 skills, assert 1 query not 50)
  - [ ] `_publish_to_lessons` sees an embedding written earlier in the same `build()` call by `_embed_missing` (ordering/freshness regression guard)
  - [ ] Full `build()` over a representative skill set (mix of already-embedded/summarized/published and not) produces identical skip/write decisions to the current per-skill-read implementation

## Dev Notes

- **Root cause:** `SkillsAssembly.build()` (`src/stackowl/skills/assembly.py`), invoked once at boot from `src/stackowl/startup/orchestrator.py:809`, runs three passes that each iterate all loaded skills (338 in the observed case: 14 builtin + 324 learned) and call `store.get(source, name)` once per skill — `_embed_missing` (assembly.py:154-155), `_summarize_missing` (assembly.py:277-280), `_publish_to_lessons` (assembly.py:225-229). Each `store.get` (`store.py:214`) is a `fetch_all` wrapped in `retry_once_on_dead_handle` (`db/pool.py:379`) — one real SQLite round-trip plus retry/log overhead per call. 3 passes × 338 skills ≈ 1000+ sequential reads, every single boot — even when every pass's hash-gate ultimately decides to skip the *write*, it still pays the *read* first.
- **Why it looked like a per-turn cost:** gateway/core restart runs `build()` in the core process, and the first message is typically sent and logged in the same window right after — per this project's own "always restart after a fix" workflow habit, boot assembly and the first turn co-occur in every observed trace. That's temporal adjacency, not causation — the actual per-turn assemble path (`assemble.py:148`) already batches correctly via `get_many_by_name` (owned skills) and `list_enabled` (the 335-row global catalog — see Story LAT.2 for what's actually wrong with *that* call). Don't re-open the turn path here.
- **Why not reuse the in-memory skill registry instead of hitting the DB at all:** the three passes need DB-only fields that never make it onto the in-memory `LoadedSkill` — `skill_id`, the stored `embedding`/`embedding_model`, `summary_body_hash`, `lessons_published_hash`. Those live only in SQLite, so a DB read of some form is unavoidable; the fix is batching it (1 query), not eliminating it.
- **Why not reuse `get_many_by_name`:** it collapses duplicate names via `_SOURCE_PRIORITY`, but these three passes key their skip/write decisions on the exact `(source, name)` pair — a builtin and a learned skill can legitimately share a name and must be tracked as distinct rows.
- **Risk:** low. These reads are decide-to-skip reads; nothing mutates the `skills` table mid-pass except each pass's own writes, and no pass re-reads a row it just wrote within the same pass. Taking a fresh snapshot per pass (not one shared snapshot for all three) is the safe/simple choice and avoids any question about whether pass N+1 needs to see pass N's writes (it does, for `_publish_to_lessons` after `_embed_missing` — a shared snapshot would get this wrong for embeddings and possibly summaries too).

### Project Structure Notes

- One new store method (mirrors the existing `list_enabled()` shape and location).
- Three call-site edits in `assembly.py`, each swapping a per-skill DB read for a dict lookup against a pass-local snapshot. No new files, no new dependency, no change to the boot sequence's ordering or the turn-time path.

### References

- [Source: src/stackowl/skills/assembly.py#L154-155] — `_embed_missing`
- [Source: src/stackowl/skills/assembly.py#L225-229] — `_publish_to_lessons`
- [Source: src/stackowl/skills/assembly.py#L277-280] — `_summarize_missing`
- [Source: src/stackowl/skills/store.py#L214] — `get()`, the per-skill round-trip being eliminated
- [Source: src/stackowl/skills/store.py#L199] — `list_enabled()`, the existing batch-query shape to mirror for the new `index_by_source_name()`
- [Source: src/stackowl/db/pool.py#L379] — `retry_once_on_dead_handle`, wraps every `store.get` call
- [Source: src/stackowl/startup/orchestrator.py#L809] — `SkillsAssembly.build()` boot invocation site

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (research); implementer TBD

### Debug Log References

### Completion Notes List

### File List
