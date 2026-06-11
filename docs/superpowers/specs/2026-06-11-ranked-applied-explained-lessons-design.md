# Design Spec — Ranked, Applied & Explained Lessons

**Date:** 2026-06-11 · **Branch:** `feat/agentic-os-stage1` · **Theme:** customer-visible reliability spine (③ learning + ④ explainability, combined thin slice)
**Status:** approved design (brainstorming gate passed) → feeds implementation plan
**Regression gate:** full `tests/journeys/` suite green (81 pass / 1 skip / ~86s, provider-mock-only)

## Problem

The learning loop is 60% closed: outcomes are mined daily into `tool_heuristics` (SQLite) + lessons (LanceDB) and semantically injected into the system prompt (`pipeline/steps/classify.py:_gather_lessons`). But (a) lessons rank by semantic similarity only — a noisy, low-evidence pattern can outrank a well-proven one; and (b) self-healing/learning is **silent to the user** — the assistant draws on a learned lesson and the user never knows. The theme's headline ("tell the user what it learned") is unbuilt.

This slice closes both with one cohesive, honest flow: **rank → apply → explain**, with overclaiming structurally impossible.

## Non-Goals (cut, YAGNI)

- LLM-based outcome enrichment — genuine Phase-2 backlog (`tool_outcome_miner.py:5`), unchanged.
- Pre-dispatch deterministic tool steering — explicitly rejected (model-decides chosen); no ReAct control-flow change.
- New DB migration / persistent UCB selection-counters — ranking uses existing columns.
- Consuming the dead `tool.heuristic_match` post-exec event — out of scope (telemetry stays telemetry).

## Architecture — three small components, one state field

```
mine (exists) ──► A. RANK at injection ──► model SELF-REPORTS (B. tool) ──► C. EXPLAIN at delivery
   daily         classify._gather_lessons    note_applied_lesson           surface_applied_lessons
```

### A. UCB-style ranking at lesson injection
**Unit:** a pure ranking function consumed by `_gather_lessons`.
**What it does:** Re-ranks the `source_type == "tool_heuristic"` hits before the top-K cut, so the most *trustworthy AND relevant* heuristic surfaces.
**Score:** `score(h) = similarity(h) + c · sqrt( ln(N) / evidence_count(h) )`, with `c = sqrt(2)` and `N = max(1, Σ evidence_count over candidate heuristic-hits)`. High similarity dominates; the exploration term gives a bounded bonus to under-observed heuristics so a new high-signal pattern is not buried by an over-counted stale one.
- Non-heuristic hits (reflections/pellets) keep their existing similarity order and are appended after ranked heuristics — ranking touches only heuristic hits.
- `evidence_count == 0` is impossible (miner floor `_MIN_EVIDENCE = 3`); function still guards with `max(1, evidence_count)`.

**Ranking inputs on the hit:** the LanceDB lesson record must carry `evidence_count` and `mean_quality`. The miner (`tool_outcome_miner.py`, `LessonDraft` build ~line 149) stamps both into the lesson payload at publish time. No migration — LanceDB records carry extra columns; the `LessonHit` projection (`lessons_index.py`) gains two optional fields (`evidence_count: int | None`, `mean_quality: float | None`), defaulting `None` for legacy/non-heuristic rows. A hit with `evidence_count is None` is treated as similarity-only (score == similarity) — fail-safe, no crash on un-restamped rows.

**Stable IDs:** `_gather_lessons` assigns a turn-local short ID per surfaced lesson (`L1`, `L2`, …) and includes it in the block so the model can cite it. IDs are positional and ephemeral (per-turn), never persisted.

**Block format (extended):**
```
## Cross-Source Lessons
If a lesson below changed what you did, call note_applied_lesson with its id.
- [L1] **[tool_heuristic]** (0.88) {snippet}
- [L2] **[reflection]** (0.71) {snippet}
```

### B. `note_applied_lesson` — new non-consequential meta-tool
**Location:** `src/stackowl/tools/meta/note_applied_lesson.py` (registered in tools registry).
**Signature:** `note_applied_lesson(lesson_id: str, what_you_did: str) -> str`.
**Behavior:** Records a structured `AppliedLesson(lesson_id, what_you_did, lesson_summary)` onto the running pipeline turn; returns a short confirmation observation to the model. Resolves `lesson_id` → the lesson snippet surfaced this turn so the explanation can include context; an unknown/hallucinated id is recorded with `lesson_summary = None` and still rendered honestly from `what_you_did` (never errors, never blocks — 4-point logging on the mismatch).
**Authz:** non-consequential, no consent gate, no side effects beyond the turn-local record. Declares no `capability_tag` (not substitutable). 4-point logging in `execute()`.
**Plumbing (resolve in plan):** the tool must reach the current turn to read `surfaced_lessons` and append an `AppliedLesson`. The exact accessor is an open implementation detail — candidate mechanisms already in the codebase: the turn-scoped context used by other turn-aware tools (e.g. `interaction/clarify.py`, consent), or `TraceContext`/`ContextVar` turn binding. The plan's first task verifies which accessor exposes the live `PipelineState` to a tool mid-ReAct and uses it; if none does cleanly, that becomes a tiny dedicated turn-record accessor (no global state). The surfaced + applied lessons live on `PipelineState`.

**State field:** `PipelineState.applied_lessons: tuple[AppliedLesson, ...] = ()` (immutable, via `.evolve()`), plus `surfaced_lessons: tuple[SurfacedLesson, ...]` so the tool can resolve IDs. `AppliedLesson`/`SurfacedLesson` are frozen dataclasses.

### C. `surface_applied_lessons()` — render step at the delivery chokepoint
**Location:** a new single-purpose module `pipeline/applied_lessons.py` exposing `surface_applied_lessons(state) -> PipelineState`, called once per turn pre-delivery in **both** backends at the same point as `surface_critical_failure` (`asyncio_backend.py`, `langgraph_backend.py`) at the same point `surface_critical_failure` is called.
**Behavior:** If `state.applied_lessons` is non-empty AND `state.responses` is non-empty (there is a real answer to annotate), append one `ResponseChunk` built from a localized template. One line per applied lesson, capped (e.g. ≤2) to avoid noise.
**Template:** new key in `setup/localize.py`, e.g. `self_heal_applied_lesson = "ℹ️ I drew on something I learned: {what_you_did}"` (localized; no hardcoded English in logic). Appended after the genuine answer.
**Ordering vs critical-failure:** runs only on the success/partial path; if `surface_critical_failure` already injected a floor/apology (no real answer), applied-lesson annotation is skipped (nothing to annotate).

## Honesty invariants (the point of the slice)

1. **No marker → no claim.** The line renders only when the model actually called `note_applied_lesson`.
2. **The model's own words.** Explanation text is the model's `what_you_did`, not inferred from heuristics.
3. **Weak-model fail-safe.** A model that never calls the tool produces silence, never a false claim. Acceptable degradation (memory: weak local gemma).
4. **Never disrupts the answer.** Annotation is appended after the real response, capped, and skipped when there is no answer.
5. **No hidden errors.** ID-resolution mismatch and ranking-input absence are logged and degrade gracefully, never swallowed silently.

## Functional requirements (Given/When/Then — customer-visible)

- **FR1 (rank):** *Given* multiple mined heuristics relevant to a query, *when* lessons are injected, *then* the higher-evidence, higher-similarity heuristic is surfaced ahead of a low-evidence one.
- **FR2 (apply+explain):** *Given* a surfaced lesson, *when* the model calls `note_applied_lesson` citing it, *then* the user's final response includes an honest line stating what the assistant drew on.
- **FR3 (no overclaim):** *Given* lessons were surfaced but the model did **not** call the tool, *when* the turn completes, *then* the response contains **no** learning/avoidance claim.
- **FR4 (graceful id mismatch):** *Given* the model cites an unknown lesson id, *when* the tool records it, *then* the turn does not error and any explanation derives from `what_you_did` only.
- **FR5 (no answer → no annotation):** *Given* a turn that produced only a floor/critical-failure message, *when* delivery runs, *then* no applied-lesson line is appended.
- **FR6 (zero regression):** the full `tests/journeys/` suite remains green.

## Testing strategy (gateway-driven, provider-mock-only)

New journey `tests/journeys/test_learning_explainability_journey.py`:
- **Happy:** scripted provider surfaces a lesson, calls `note_applied_lesson`, then answers → assert the user-visible response text contains the honest explanation derived from `what_you_did`. (FR2)
- **Negative — silence:** provider answers without calling the tool → assert response contains no learning/avoidance phrasing. (FR3)
- **Negative — id mismatch:** provider calls the tool with a bogus id → assert no error, explanation falls back to `what_you_did`. (FR4)
- **Negative — floor turn:** provider produces no usable answer (self-heal floor) → assert no applied-lesson line. (FR5)
- **Ranking unit/integration:** seed 5 heuristics (varying evidence/similarity) → assert the expected one ranks top in the injected block. (FR1)
- **Regression:** full `tests/journeys/` green. (FR6)

All run through the real gateway → pipeline; only the AI provider is mocked & scripted per scenario (including the tool-call). Real local SQLite/LanceDB.

## House-rules checklist

- Strict mypy; 4-point logging in `note_applied_lesson.execute()` and the render step.
- No silent catches — ranking-input/id-resolution failures logged and degraded.
- No DB migration; LanceDB extra columns + optional `LessonHit` fields are backward-compatible.
- i18n via `localize.py`; no hardcoded English in control logic.
- Runtime state stays under `~/.stackowl/`; no repo writes.
- Reuse over new: extends `_gather_lessons`, `critical_failure` chokepoint, `localize`, `PipelineState.evolve`; one genuinely new tool + one ranking fn + one render step.

## Rollback

Pure-additive. Back out = remove the tool registration, the render-step call in both backends, the ranking re-sort (revert to similarity order), the two `LessonHit` optional fields, and the miner stamping. No data migration to reverse; un-stamped LanceDB rows already degrade to similarity-only.
