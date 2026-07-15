---
baseline_commit: 35005bf7b3efef6e6bd860ff7c3b5d597d6307d9
---

# Story 1.1: Prove the reflect → store → recall chain end-to-end

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform,
I want an automated regression test that writes a task reflection and then confirms it's retrieved on a matching later turn,
so that "won't repeat the same issue" is a measured guarantee, not an assumption.

## Acceptance Criteria

1. **Given** an outcome that is `success=True`, `failure_class=None`, `quality_score >= 0.6` (the system's actual reflection-eligibility trigger — see Dev Notes, "Positive-only, not literally 'failed'")
   **When** `ReflectionWriterHandler.execute()` (`memory/reflection_writer_handler.py`) processes it
   **Then** a reflection is durably written to the `reflections` table (via `ReflectionStore.write`)
   **And** it is published into the unified `LessonsIndex` (LanceDB) via `_publish_to_lessons`

2. **Given** the reflection from AC1 is in place
   **When** a later turn runs `classify.run()` with matching-topic `input_text` for the same `owl_name`, with `intent_class="standard"` and `intent_classified=True`
   **Then** `_gather_lessons()` retrieves it through `lessons_index.search()` and the resulting `memory_context` block contains the reflection's content

3. **Given** AC1 + AC2 chained in one test
   **When** the test runs end-to-end (write → store → recall), not each stage mocked/asserted in isolation
   **Then** the test passes or fails honestly against the CURRENT, unmodified pipeline (FR-4) — this story does not assume or force a fix

4. **Given** an outcome that does NOT meet the positive-only trigger (`success=False`, or `quality_score < 0.6`)
   **When** the same chain runs
   **Then** no reflection is written and nothing is recalled — asserted as a boundary case so the test can't pass by accident (e.g. an empty-string false-positive)

## Tasks / Subtasks

- [x] Task 1: Write the end-to-end regression test (AC: #1, #2, #3, #4)
  - [x] New test file `tests/memory/test_reflect_recall_chain_e2e.py` (or extend an existing suite if a maintainer judges that cleaner — see Dev Notes on existing coverage)
  - [x] Stage 1 (write): insert a `TaskOutcome` via `TaskOutcomeStore.record()` + `set_quality_score()`, run `ReflectionWriterHandler.execute(job)` against a real `tmp_db` fixture, assert the row lands in `reflections` (mirror `tests/memory/test_reflection_capture.py`'s `_make_outcome` helper — do not reinvent it)
  - [x] Stage 2 (publish): wire a real or fake `LessonsIndex` into the handler's `lessons_index=` constructor arg so `_publish_to_lessons` actually runs (currently `None` in the unit tests it inherited from — this story's whole point is exercising the FULL chain, so this hop must not be skipped)
  - [x] Stage 3 (recall): call `stackowl.pipeline.steps.classify._gather_lessons(query, limit=3)` (or `classify.run(state)` for closer-to-production coverage) with the SAME `lessons_index` instance, matching `owl_name`/topic, and assert the reflection surfaces in the returned block
  - [x] Negative case (AC #4): repeat with a disqualifying outcome, assert nothing is written/recalled
- [x] Task 2: Run the test, record the honest result (AC: #3)
  - [x] `uv run pytest tests/memory/test_reflect_recall_chain_e2e.py -v`
  - [x] Do NOT modify pipeline code in this story to force a pass — if it fails, that is Story 1.2's job, not this one's. Record which stage failed (write/publish/recall) in Dev Agent Record → Completion Notes for Story 1.2 to consume.
- [x] Task 3: Gateway-integration variant (NFR-4)
  - [x] Anything touching the live turn pipeline needs a gateway-driven integration test mocking only the AI provider. `_gather_lessons`/`classify.run` are turn-pipeline code, so extend or add a case that drives the chain through the real `classify` step inside a `GatewayScanner`-style flow, following `tests/pipeline/test_plan_a_gateway_integration.py`'s pattern (real `ProviderRegistry` resolving a fake `_RecordingProvider`, real `AsyncioBackend`) — reuse that pattern, do not build a second one
- [x] Task 4: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (per CLAUDE.md mandatory process — tonyStyle skill scan included)

## Dev Notes

### Positive-only, not literally "failed" — read before writing the test

FR-4's wording ("a failed or low-quality task's reflection is verifiably retrievable") is **stale PRD phrasing carried over from before a deliberate pivot** — do not implement it literally. The actual, current, non-negotiable system behavior (confirmed by direct code read, `memory/reflection_writer_handler.py:1-22` docstring, and cross-checked against `tests/memory/test_reflection_capture.py`'s own `test_list_pending_excludes_failures` / `test_list_pending_excludes_low_quality_outcomes`) is:

> Reflections are **positive-only by design** (operator directive). `ReflectionStore.list_pending()`'s eligibility filter is `success = 1 AND failure_class IS NULL AND quality_score >= 0.6`. A genuinely failed or low-quality outcome is explicitly **excluded** — no reflection is ever written for it. This mirrors DNA attribution's own positive-only filter (`dna_attribution.py`'s `_filter_scored_outcomes`) and is the same system-wide rule the PRD itself calls "not renegotiable within this PRD's scope."

So "won't repeat the same issue" in this codebase is implemented as: **remember what worked, retrieve it on a similar future turn, so the owl repeats the win instead of drifting away from it** — not as literal failure-memory. Writing this story's test against a `success=False` outcome and expecting a reflection would test something the system is deliberately built to refuse. AC #1 uses the REAL trigger; AC #4 is the failure/low-quality case, and it must assert the ABSENCE of a reflection, not its presence.

If you think this is a PRD defect worth flagging upward (stale wording vs. actual — intentional — behavior), note it in Completion Notes; do not silently "fix" the positive-only filter to match the literal FR-4 text — that would violate the non-negotiable rule and NFR-1 (no existing capability removed/weakened).

### The chain as it actually runs today

1. **Write**: `ReflectionWriterHandler.execute(job)` (`memory/reflection_writer_handler.py`) — runs `CriticScorerHandler` first (scores pending outcomes), then `ReflectionStore.list_pending()` (positive-only filter above), then for each eligible row: builds a prompt (`ReflectionPromptBuilder`), calls the LLM, parses `(summary, suggested_strategy)`, embeds the summary, and `ReflectionStore.write()`s it inside a chunked transaction.
2. **Publish**: same `execute()` call, `_publish_to_lessons()` — best-effort, OUTSIDE the DB transaction, pushes a `LessonDraft(source_type="reflection", ...)` into `services.lessons_index` (LanceDB). **This step is silently skipped if `lessons_index=None`** — it degrades gracefully (logged WARNING), which is correct production behavior but means a test that doesn't wire a real/fake `lessons_index` will never reach recall.
3. **Recall**: `pipeline/steps/classify.py`'s `run()` calls `_gather_lessons(query, limit=3)` → `lessons_index.search(query, limit=limit)`, filters out `source_type == "skill"` hits, ranks via `rank_lessons`, and folds the result into `memory_context` under a `## Cross-Source Lessons` block.

**Important, easy-to-miss trap**: `classify.py` ALSO defines `_gather_recent_reflections()` (a direct SQLite `ReflectionStore.recent_for_owl`/`semantic_for_owl` read) — but per the comment at `classify.py:634-643`, this function is **no longer called from `run()`**. It was superseded by the unified `_gather_lessons` path (an earlier de-complication PRD's FR-3) and is now kept alive ONLY because two existing tests (`tests/pipeline/test_classify_reflections_degraded.py`, `tests/journeys/test_no_false_history_journey.py`) still exercise it directly. **Do not write this story's E2E test against `_gather_recent_reflections` — it is dead in the live path.** The real recall surface is `_gather_lessons` / the `LessonsIndex`.

`_should_surface_failure_history(state)` also gates recall: it requires `state.intent_class == "standard" and state.intent_classified`. If driving `classify.run(state)` directly (Task 1's closer-to-production option), the test's `PipelineState` must set these — though note `_gather_lessons` itself is NOT gated by `_should_surface_failure_history` (that gate only applies to `actions_block`); `lessons_block` is gated only by `_lean` (i.e. `state.intent_class` not being in `TOOL_FREE_CLASSES`).

### Existing coverage — what NOT to duplicate

- `tests/memory/test_reflection_capture.py` already covers `ReflectionStore` CRUD, the positive-only filter in isolation, and `ReflectionPromptBuilder`'s framing. Reuse its `_make_outcome` helper pattern; don't recreate it.
- `tests/pipeline/test_gather_lessons.py` already covers `_gather_lessons`'s ranking/id-assignment/audit-trace logic against a **fake** `LessonsIndex`. This story's job is different: prove the REAL write (`ReflectionWriterHandler`) actually reaches the REAL recall (`_gather_lessons`) — i.e., that `_publish_to_lessons`'s output shape is what `_gather_lessons` can actually consume. That seam is currently untested end-to-end.
- `tests/pipeline/test_classify_lessons_degraded.py` / `test_classify_reflections_degraded.py` cover degradation paths (DB/index failures) — out of scope here, don't duplicate.
- `tests/pipeline/test_plan_a_gateway_integration.py` is the canonical NFR-4 gateway-integration pattern (real `GatewayScanner`, real `AsyncioBackend`, real `ProviderRegistry` resolving a fake provider). Mirror its structure for Task 3, don't invent a second harness.

### Architecture Compliance

- This story is **read-path only** — Feature 2 explicitly has "no mutation," per the Architecture Spine's Capability → Architecture Map (`Feature 2 — reflect_now reliability | memory/reflection_writer_handler.py (existing, verified not rebuilt) | AD-1 (n/a — read-path, no mutation)`). AD-1's "one pipeline, no side doors" rule does not apply here since nothing in this story writes to `owl_dna` or skill storage.
- Zero coupling to DNA mutation machinery (`owls/*`) — do not touch `dna_governor.py`, `evolution.py`, or checkpoint code from this story. That is Epic 2's territory.
- NFR-3 (4-point logging): this is a test-only story — no new `execute()`-shaped production method is being added, so NFR-3 doesn't generate new logging obligations here. If Story 1.2 needs a fix, that story picks up NFR-3.
- NFR-4 (gateway-integration test mocking only the AI provider): satisfied by Task 3.

### Testing Standards

- `pytest` + `pytest-asyncio` (existing convention — see `pytestmark = pytest.mark.asyncio` at the top of sibling files).
- Use the `tmp_db` fixture (already used throughout `tests/memory/`) for a real, ephemeral `DbPool` — do not mock the database (matches this repo's existing integration-test convention of exercising real SQLite over mocks).
- Run: `uv run pytest tests/memory/test_reflect_recall_chain_e2e.py -v` and the new/extended gateway-integration test individually before the full suite. **Do not run the full `uv run pytest` suite on this box** — it hangs (known Jetson dev-box constraint); use targeted paths with a timeout.

### Project Structure Notes

- New test file lands under `tests/memory/` (alongside `test_reflection_capture.py`, `test_reflection_semantic_recall.py`) since it's anchored on `ReflectionWriterHandler` — consistent with existing naming/location conventions. No production source files are added or moved by this story.
- No migration, no new module, no new tool — this is the "epics doc may resolve to 'confirmed working, regression test added'" case (Epic 1's own framing). Don't manufacture a fix that isn't needed.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 1.1] (lines 113-129)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 2 — Reflect-Now Reliability] (FR-4, FR-5)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/addendum.md] (reflect_now / reflection_writer_handler reuse map)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md#Capability → Architecture Map] (Feature 2 row)
- [Source: src/stackowl/memory/reflection_writer_handler.py] (direct read — positive-only trigger, publish-to-lessons hop)
- [Source: src/stackowl/pipeline/steps/classify.py] (direct read — `_gather_lessons` is the live recall path; `_gather_recent_reflections` is dead in `run()`)
- [Source: src/stackowl/tools/knowledge/reflect_now.py] (thin wrapper shape, for context only — not modified by this story)
- [Source: tests/memory/test_reflection_capture.py], [Source: tests/pipeline/test_gather_lessons.py], [Source: tests/pipeline/test_plan_a_gateway_integration.py] (patterns to reuse)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (Amelia — dev-story workflow)

### Debug Log References

- `uv run pytest tests/memory/test_reflect_recall_chain_e2e.py -v` → 2 passed
- `uv run pytest tests/pipeline/test_reflect_recall_gateway_integration.py -v` → 1 passed
- Combined regression sweep (new tests + 8 existing sibling suites covering the
  same modules — `test_reflection_capture.py`, `test_gather_lessons.py`,
  `test_plan_a_gateway_integration.py`, `test_reflection_writer_chunked_writes.py`,
  `test_reflection_writer_critic_merge.py`, `test_reflection_writer_handler_defer.py`,
  `test_classify_lessons_degraded.py`, `test_classify_reflections_degraded.py`,
  `test_lessons_lance_dedup.py`) → 41 passed, 0 failed, no regressions
- `uv run ruff check src/ tests/` → clean on all 3 new files (0 findings after
  one autofix: import-order in the gateway-integration test); pre-existing
  findings elsewhere in `src/` are unrelated to this story (untouched files:
  `service/shutdown.py`, `setup/yaml_writer.py`, `tui/coordinator.py`)
- `uv run mypy src/` → 79 pre-existing errors in 16 files, ALL pre-existing
  (git status confirms zero `src/` files touched by this story) and unrelated
  to the reflect/lessons/classify chain (telegram notifications, scheduler
  assembly, startup orchestrator, cli app) — not introduced or worsened here
- `tonyStyle` scan of `reflection_writer_handler.py`, `classify.py`,
  `lessons_index.py`, `lessons_lance.py`: no silent catches, no missing
  4-point logging on `execute()`-shaped methods, no architecture violations.
  One minor pre-existing gap noted (see Completion Notes) — NOT fixed here
  per this story's read-path-only / no-src-changes scope.

### Completion Notes List

- **Honest result (AC #3): NO BREAK FOUND.** Both the direct-call chain test
  (`tests/memory/test_reflect_recall_chain_e2e.py`) and the full
  gateway-driven integration test
  (`tests/pipeline/test_reflect_recall_gateway_integration.py`) PASSED against
  the current, unmodified pipeline on the first run — no fix was forced. All
  three stages verified independently within the same chained test so a
  false pass can't be masked:
  - **Write**: `ReflectionWriterHandler.execute()` correctly writes an
    eligible outcome (`success=True, quality_score>=0.6, failure_class=None`)
    to the `reflections` table.
  - **Publish**: `_publish_to_lessons()` correctly reaches a REAL
    `LessonsIndex` (LanceDB, temp-dir adapter + `HashEmbeddingProvider`
    fallback — no network/model download needed) — verified independently
    via a direct `lessons_index.search()` call before the recall stage runs,
    so a publish break would be distinguishable from a recall break.
  - **Recall**: `classify._gather_lessons()` retrieves the published
    reflection through `lessons_index.search()` and folds its content into
    the `## Cross-Source Lessons` block — verified BOTH via a direct call
    (Task 1) AND via the full live turn pipeline
    (`GatewayScanner.scan()` → `AsyncioBackend.run()` → `triage` → `dispatch`
    → `classify` → `assemble` → `execute`, mocking only the AI provider),
    where the reflection's content reached the tool-loop provider's
    `system_text`.
  - **AC #4 (boundary)**: a genuine failure (`success=False`) and a
    low-quality success (`quality_score=0.3`) both correctly produce NO
    reflection row and NO recallable lesson — confirms the happy-path
    assertions above aren't passing by accident (e.g. an empty-string
    false-positive).
  - **Conclusion for Story 1.2**: since no break was found, Story 1.2
    ("fix-reflect-recall-chain-break") resolves to Epic 1's own documented
    alternative outcome — "confirmed working, regression test added" — there
    is no broken stage for it to fix. Recommend re-scoping or closing Story
    1.2 rather than inventing a fix for a chain that already works; that
    re-scoping decision is left to the epic owner, not made unilaterally
    here.
- Reused existing patterns exactly as Dev Notes directed: `_make_outcome`
  shape from `test_reflection_capture.py`, `_gather_lessons` fake-index call
  shape from `test_gather_lessons.py`, and the `GatewayScanner` →
  `AsyncioBackend` → `_RecordingProvider` harness from
  `test_plan_a_gateway_integration.py` (structurally mirrored, not a second
  harness). Shared plumbing common to both new test files (`ScriptedReflectionProvider`,
  `NoOpCritic`, `build_lessons_index`, `seed_outcome`, `reflection_job`) was
  factored into `tests/_reflect_recall_chain_helpers.py`, following this
  repo's existing `tests/_story_X_helpers.py` convention for helpers shared
  across more than one test file for the same story.
- Positive-only framing followed per Dev Notes: AC #1 uses the system's REAL
  reflection-eligibility trigger (success + quality_score>=0.6), never a
  literal "failed task" outcome; AC #4 asserts absence for both a genuine
  failure AND a low-quality success, never presence.
- `_gather_recent_reflections`/`_should_surface_failure_history` were left
  untouched and NOT exercised by the new tests — confirmed dead-in-`run()`
  per the story's own trap warning; `_gather_lessons` is the only recall path
  driven here.
- **tonyStyle finding (reported, not fixed — out of this story's scope):**
  `LessonsIndex.search()` (`src/stackowl/learning/lessons_index.py:162-200`)
  has three early-return branches; the "no embedder / empty query" branch and
  the "embed returned empty vectors" branch both return with NO log call at
  all, unlike its sibling `publish()` which logs its analogous early exit and
  unlike `classify._gather_lessons()` which logs every one of its own early
  exits. Minor NFR-3 (4-point logging) gap, not a functional bug — does not
  affect this story's chain (the chain never hits these branches once an
  embedder is wired), so left unfixed per this story's read-path-only /
  no-`src/`-changes scope. Candidate for a follow-up logging pass alongside
  Story 1.2 or later hardening work.

### File List

- `tests/_reflect_recall_chain_helpers.py` (new) — shared test doubles:
  `ScriptedReflectionProvider`, `NoOpCritic`, `reflection_job()`,
  `build_lessons_index()`, `seed_outcome()`
- `tests/memory/test_reflect_recall_chain_e2e.py` (new) — Task 1: direct
  write → publish → recall chain test (AC #1-#4)
- `tests/pipeline/test_reflect_recall_gateway_integration.py` (new) — Task 3:
  NFR-4 gateway-driven integration variant (AC #2, full live pipeline)

No `src/` files were added, modified, or deleted (test-only story, per AC #3
and the Architecture Compliance section — read-path only, no mutation).

## Change Log

- 2026-07-15 — Added end-to-end regression coverage for the reflect → store →
  recall chain (Task 1: direct-call chain test + AC #4 boundary case; Task 3:
  NFR-4 gateway-driven integration variant). Result: chain confirmed working
  end-to-end against the unmodified pipeline — no production fix required or
  applied. Shared test doubles factored into `tests/_reflect_recall_chain_helpers.py`.
