# Story LAT.5: Don't block boot on serial per-skill LLM summarization

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the StackOwl boot sequence,
I want `SkillsAssembly.build()`'s enrichment passes (embed/summarize/publish-to-lessons) to run in the background after the gateway starts accepting turns, instead of gating boot-readiness,
so that a store with many not-yet-summarized skills doesn't add tens of seconds to every restart.

## Context — found via live measurement of the LAT.1-4 fixes

After LAT.1 (batched boot-time skill-index reads) shipped and was verified live (zero `store.get` calls in this phase), a live "hi" latency measurement still showed `SkillsAssembly.build()` taking ~98s wall-clock with zero DB reads in that window. Root-caused: `_summarize_missing` (`assembly.py:277-345`) does a **serial** `for ls in loaded: await provider.complete(...)` — one real network LLM round-trip per skill needing a summary, no batching, no concurrency. `_embed_missing` and `_publish_to_lessons` are already properly batched (one call each) — this pass is the sole outlier.

This is NOT a caching bug: `_summary_hash` (store.py:88-102) is a stable content hash (body + override + source + sanitizer version, no timestamp/random/env value) — once a skill is summarized, the next boot's gate correctly skips it. The cost converges toward zero as skills accumulate summaries across boots. One recurring sub-case: a skill whose provider call returns empty text (`if not text: continue`, assembly.py:341) never writes a hash and is re-attempted every single boot forever — a permanent tax for that subset.

## Acceptance Criteria

1. `SkillsAssembly.build()`'s three enrichment passes (`_embed_missing`, `_summarize_missing`, `_publish_to_lessons`) no longer block the awaited boot path — the platform starts accepting turns without waiting for them to complete.
2. The enrichment passes still run (as a background task fired once the gateway is up), so the same eventual convergence (skills get summarized/embedded/published over time) is preserved — this is a deferral, not a removal of the capability.
3. `_summarize_missing`'s exit logs summarized/skipped/failed/empty counts (mirroring `_embed_missing`'s existing `embedded` count log) — the current bare "exit" debug log with no counts is an observability gap; fix it as part of this story.
4. A skill whose summarize call returns empty text is logged distinctly from a genuine failure (so the permanent-retry-forever subset is visible in logs, not indistinguishable from occasional real failures).
5. If the background enrichment task is still running when a turn needs the DB (e.g. a turn triggers a skill lookup while the background task holds a write), there's no deadlock or DB contention regression — this shares the single-writer SQLite connection with foreground turns, so verify against LAT.4's existing chunked-transaction pattern rather than introducing a new contention source.
6. No change to `_embed_missing`'s or `_publish_to_lessons`'s already-batched behavior — this story only changes execution timing (blocking → background) and `_summarize_missing`'s observability, not their internal logic.

## Tasks / Subtasks

- [ ] Task 1: Move enrichment passes off the awaited boot path (AC: #1, #2)
  - [ ] Find the boot call site (`src/stackowl/startup/orchestrator.py:809`, `await SkillsAssembly.build(...)` or equivalent) — change it to fire the three enrichment passes as a background `asyncio.create_task(...)` instead of awaiting them inline
  - [ ] Whatever `build()` currently returns/wires synchronously before the enrichment passes (e.g. the loader's `load_all()` result, needed for the platform to know what skills exist) must remain synchronous/awaited — only the enrichment (embed/summarize/publish) passes move to background, not skill *loading* itself
  - [ ] Log entry when the background task starts and exit when it completes, with total elapsed time
- [ ] Task 2: Add missing observability to `_summarize_missing` (AC: #3, #4)
  - [ ] Track counts: summarized (real write), skipped (hash already current), failed (exception), empty (provider returned empty text — distinct from failed)
  - [ ] Log all four counts at pass exit, matching `_embed_missing`'s existing count-log pattern
- [ ] Task 3: Contention check against LAT.4 (AC: #5)
  - [ ] Confirm the background enrichment task's writes (embed/summarize/publish) don't reintroduce the per-row-autocommit starvation LAT.4 fixed — `_embed_missing`/`_publish_to_lessons` are already batched (unaffected); confirm `_summarize_missing`'s per-skill `set_summary` write, now happening in the background while foreground turns are live, doesn't hold the writer lock long enough to matter (it's one row at a time already, same as before this story — just now concurrent with live turns instead of during boot when nothing else was writing)
- [ ] Task 4: Tests (AC: #1-#4)
  - [ ] Boot readiness (whatever signals "platform accepting turns" — e.g. the gateway's ready state) is NOT gated on `_summarize_missing`/`_embed_missing`/`_publish_to_lessons` completing — assert boot proceeds while a slow/mocked enrichment task is still running
  - [ ] `_summarize_missing`'s exit log includes all four counts, correctly incremented for each case (mock a mix of already-current, freshly-summarized, failed, and empty-response skills)
  - [ ] Empty-response skills are logged distinctly from exception-failures

## Dev Notes

- **Root cause:** `_summarize_missing` (`assembly.py:277-345`) is a serial loop, one `provider.complete(...)` LLM round-trip per skill needing a summary (`assembly.py:332`, provider resolved via `provider_registry.get_with_cascade("fast")` — a remote provider per this project's config). ~50-100 skills needing summaries × ~1-2s each ≈ the observed ~98s boot-time cost. Zero DB reads in that window (LAT.1's fix holds) — the cost is entirely LLM I/O, not the database.
- **NOT a caching bug — confirmed:** `_summary_hash` (`store.py:88-102`) is a stable content hash with no non-deterministic inputs; `_SUMMARY_SANITIZER_VERSION` has never been bumped since the v2→root migration; migration `0050_skill_summary.sql` is an additive nullable column, doesn't invalidate existing summaries. The hash gate correctly skips already-summarized skills on subsequent boots — cost converges toward zero over time for the normal case.
- **The one genuine recurring sub-case:** a skill whose provider call returns empty text hits `if not text: continue` (assembly.py:341) and never persists a hash — re-attempted every boot forever. This story's AC #4 makes this visible in logs; it does not by itself fix the underlying empty-response cause (that's a separate, potentially provider-specific investigation, out of scope here).
- **Why background-task deferral over concurrency/batching the LLM calls:** deferral removes the boot-latency symptom entirely and is a smaller, safer change than adding `asyncio.gather` + semaphore-bounded concurrency to a loop making real LLM calls (concurrency changes would need their own rate-limit/cost consideration). Concurrency bounding is a legitimate follow-up if background-task deferral alone doesn't converge fast enough in practice, but isn't required to fix the reported symptom (boot/turn latency), since the fix here means boot no longer waits on this pass at all.
- **Rejected:** batching the summarize LLM calls into fewer round-trips — unlike `_embed_missing` (embeddings can genuinely batch multiple texts into one API call) and `_publish_to_lessons` (a batch write), LLM summarization of N distinct skill bodies isn't naturally batchable into one prompt without either a much larger context window per call or quality tradeoffs from cramming multiple skills into one summarization prompt. Deferral sidesteps needing to solve that.

### Project Structure Notes

- Boot call-site change in `startup/orchestrator.py` (await → background task).
- `assembly.py` changes: extract/refactor to allow enrichment passes to run independently of the loader's synchronous return, plus the new count-logging in `_summarize_missing`.
- No schema change, no new files beyond tests.

### References

- [Source: src/stackowl/skills/assembly.py#L98-124] — `build()`, current synchronous orchestration of all passes
- [Source: src/stackowl/skills/assembly.py#L179] — `_embed_missing`'s batched `provider.embed(texts)` call (reference pattern — already correct, untouched)
- [Source: src/stackowl/skills/assembly.py#L206] — `_embed_missing`'s existing count-log pattern to mirror
- [Source: src/stackowl/skills/assembly.py#L267] — `_publish_to_lessons`'s batched `lessons_index.publish_many(...)` (already correct, untouched)
- [Source: src/stackowl/skills/assembly.py#L277-345] — `_summarize_missing`, this story's primary change target
- [Source: src/stackowl/skills/assembly.py#L302] — serial per-skill loop
- [Source: src/stackowl/skills/assembly.py#L332] — the actual `provider.complete(...)` LLM call site
- [Source: src/stackowl/skills/assembly.py#L341] — empty-response `continue`, the recurring sub-case
- [Source: src/stackowl/skills/store.py#L88-102] — `_summary_hash`, confirmed stable/deterministic
- [Source: src/stackowl/startup/orchestrator.py#L809] — boot call site, currently `await`ed
- [Source: _bmad-output/planning-artifacts/stories/epic-turn-latency/story-LAT.1-*.md] — sibling story, the DB-read batching this story's finding built on top of
- [Source: _bmad-output/planning-artifacts/stories/epic-turn-latency/story-LAT.4-*.md] — sibling story, the chunked-transaction pattern this story's Task 3 checks against

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (research); implementer TBD

### Debug Log References

### Completion Notes List

### File List
