---
baseline_commit: a197fbdb
---

# Story 2.5: Shadow-validation gate — replay harness core

Status: review

## Story

As the platform,
I want proposed DNA deltas validated against a held-out sample of the owl's own recent real interactions before they ship,
so that a bad mutation is caught before it affects a real turn, not after.

## Acceptance Criteria

1. **Given** a new `owls/shadow_validator.py` module
   **When** a batch of proposed deltas is ready to promote
   **Then** it replays a held-out sample of that owl's recent real interactions against the proposed DNA, in a context with no side effects (FR-8)

2. **Given** the replay results
   **When** each replayed interaction is scored
   **Then** `tools/verification.py`'s `is_trustworthy_success()` is reused as-is as the pass/fail oracle — no new verification primitive invented (FR-8)

3. **Given** N consecutive non-regressions (default 3, operator-configurable via the shared config from the AD-3 amendment)
   **When** that threshold is met
   **Then** the batch is eligible for promotion (FR-9)

## This is the largest, most novel piece of this whole PRD — read the whole Dev Notes section before coding

No prior art exists in this codebase (or, per the PRD's own research citations, in the reviewed literature) for a pre-commit replay-and-validate harness. The architecture spine explicitly defers the exact replay/sampling mechanics to this story ("the largest single design surface... deliberately left to Feature 4's own story... benefits from being worked with real interaction data in hand"). This story builds the STANDALONE, testable replay-and-score core only — it is NOT wired into the actual promotion flow yet (that's Story 2.6, the next story, split out deliberately). Nothing in this story runs automatically or affects the live nightly batch.

## Tasks / Subtasks

- [x] Task 1: `ShadowValidator` core class (AC #1, #2, #3)
  - [x] New file `src/stackowl/owls/shadow_validator.py`
  - [x] `@dataclass(frozen=True) class ShadowValidationResult: passed: bool; consecutive_non_regressions: int; n_replayed: int; failures: tuple[dict[str, object], ...]` (failures carries enough to log/report: e.g. `{"input_text": ..., "reason": ...}` per failed replay — for Story 2.7's observability, don't over-design the shape now, keep it minimal and extend later if 2.7 needs more)
  - [x] `class ShadowValidator:` constructed with `(db: DbPool, provider_registry: ProviderRegistry, *, n_consecutive_required: int = 3, sample_size: int = 5)` — these two numbers are THE "shared config from the AD-3 amendment" (AC #3); define them as named constructor params with sane module-level default constants (`_DEFAULT_N_CONSECUTIVE = 3`, `_DEFAULT_SAMPLE_SIZE = 5`) so Story 2.6/2.7 can both reference the SAME defaults without redefining them — do not let `n_consecutive_required` be parameterizable per-caller in a way that lets one caller silently use a looser threshold (per AD-3's explicit "single shared config, not per-caller" amendment) — a caller MAY override it (needed for tests), but production callers (Story 2.6's nightly batch, Story 3.2's `evolve_now`) must both construct `ShadowValidator` with the SAME defaults, not divergent ones. Document this loudly in the class docstring so Story 2.6/3.2 don't each invent their own number.
  - [x] `async def validate(self, owl_name: str, manifest: OwlAgentManifest, proposed_dna: OwlDNA) -> ShadowValidationResult` — the entry point:
    1. **Held-out sample**: reuse `TaskOutcomeStore.list_scored_for_owl(owl_name, since_epoch=lookback_epoch())` (the SAME method `DnaAttributor` already uses — do not write a new query) to fetch the owl's recent scored outcomes, then filter to trustworthy successes via the SAME positive-only-adjacent lens already established (`o.success and not o.failure_class` — reuse the shape of `dna_attribution.py`'s `_filter_scored_outcomes`, but note this is NOT the positive-only-learning rule in the DNA-mutation sense — it's "replay inputs that previously worked, to see if they still work" — document this distinction in a comment so a future reader doesn't confuse it with FR-6/AD-4's tiering or the reflection positive-only rule), take the `sample_size` MOST RECENT such outcomes as the held-out set. Fewer than `sample_size` available (cold-start) → `ShadowValidationResult(passed=False, ..., n_replayed=<actual count>)`, not a crash — an owl with too little history to validate against fails closed (safer default than passing vacuously).
    2. **Replay, side-effect-free**: for each held-out outcome (most-recent-first), run its `input_text` through the REAL pipeline (`classify` → `assemble` → `execute`, i.e. `AsyncioBackend.run(state)`) but under an ISOLATED `StepServices` instance you construct yourself — NOT `get_services()`'s live global services, and NOT the live global `OwlRegistry`. Build a scratch `OwlRegistry()`, `register()` a copy of `manifest` with `dna=proposed_dna` (`manifest.model_copy(update={"dna": proposed_dna})`), and pass that scratch registry into the scratch `StepServices`. This is what "no side effects" means concretely: the live registry is NEVER mutated (no `apply_dna_overlay` on the real registry — that WOULD be a live side effect visible to concurrent real turns, exactly what must be avoided), no `TaskOutcomeStore.record()` call persists the replay's outcome, no message is delivered to any user. Use a synthetic `session_id` (e.g. `f"shadow-validate-{uuid4().hex}"`) and `interactive=False` on the `PipelineState` so nothing in the pipeline attempts a live delivery side-channel. Mirror `tests/pipeline/test_plan_a_gateway_integration.py`'s pattern for constructing an isolated `PipelineState` + running it through `AsyncioBackend` with a REAL `ProviderRegistry` (this DOES call the real LLM provider — that's intentional, it's the whole point of validating real behavior; it does NOT call a fake/recording provider like that test does, since the goal here is a genuine dry-run, not a wiring assertion) — reuse that test's isolation seam (scanner→state-construction→backend.run), don't build a third pattern.
    3. **Score the replay**: reuse `memory/critic_prompt.py`'s `CriticScorerPromptBuilder` directly (build a prompt from the replayed input/response pair, call the fast-tier provider, `parse_critic_response`) to get a `quality_score` for the replayed turn — this mirrors what `CriticScorerHandler` does internally, but WITHOUT going through its DB-coupled `execute(job)` wrapper (which reads/writes `task_outcomes` — this story must not touch that table for replay rows). Reuse the prompt builder + parser, not the whole handler.
    4. **Oracle (AC #2)**: `success = "error" not in <pipeline result>` (or however `PipelineState`/`AsyncioBackend.run`'s result signals a hard failure — check `state.errors`, mirrors `classify_failure`'s existing convention) combined with the fresh `quality_score >= 0.6` (SAME threshold `ReflectionStore.list_pending`'s positive-only trigger already uses — reuse that constant if it's named somewhere, don't hardcode a second magic `0.6` if one already exists as a named constant) as a proxy for `verified` — this story does NOT have a live `ToolResult.verified` signal available for a replayed turn (that tri-state comes from the REAL tool-execution verification ladder, which a side-effect-free replay with a real provider MAY still exercise if the turn calls tools — if it does, thread the real `verified` value through; if the turn is tool-free, `verified=None` degrades to `is_trustworthy_success` falling back to `success` alone, which is correct per that function's own documented semantics). Call `is_trustworthy_success(success, verified)` — literally the imported function, unmodified (AC #2's explicit requirement).
    5. **N-consecutive counting**: walk the held-out sample most-recent-first, count consecutive `is_trustworthy_success() == True` results from the start; stop early once `n_consecutive_required` is reached (`passed=True`) OR once a `False` breaks the streak (`passed=False`, don't keep counting past the first regression — "consecutive" means unbroken from the start of the walk, per FR-9's wording)
  - [x] 4-point logging on `validate()` (entry: owl/sample_size; decision: held-out count vs required; step: each replay's pass/fail; exit: `ShadowValidationResult`), `log.owls` namespace (Story 2.1's precedent)
- [x] Task 2: Tests (AC #1, #2, #3)
  - [x] `tests/owls/test_shadow_validator.py` — inject a fake/stub provider (mirrors `tests/pipeline/test_plan_a_gateway_integration.py`'s `_RecordingProvider` pattern, but here it needs to return DIFFERENT canned responses per call so you can construct both a "all pass" and "one regresses" scenario) so tests don't hit a real network LLM
  - [x] Seed `task_outcomes` rows (real `tmp_db`) with known `input_text`/`success`/`quality_score` values so `list_scored_for_owl` returns a deterministic held-out sample
  - [x] Happy path: N consecutive trustworthy replays → `passed=True`
  - [x] Regression case: a replay whose stub response scores below 0.6 → streak breaks → `passed=False`, and confirm the count stops incrementing PAST that break (a later trustworthy replay in the sample does NOT "rescue" the streak — consecutive means unbroken)
  - [x] Cold-start: fewer than `sample_size` eligible outcomes → `passed=False`, `n_replayed` reflects the actual (smaller) count, no crash
  - [x] Isolation proof: assert the LIVE `OwlRegistry`/`get_services()` global state is genuinely untouched after `validate()` runs (e.g. `registry.get(owl_name).dna` still equals the ORIGINAL dna, not `proposed_dna`) — this is the concrete regression test for "no side effects," don't just assert the return value
- [x] Task 3: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### Why NOT wired into promotion yet

Story 2.6 (`2-6-wire-gate-into-promotion-auto-restore`) is the NEXT story and does the wiring (`EvolutionCoordinator` calls `ShadowValidator.validate()` between checkpoint and persist, auto-restores on failure via Story 2.1's `LearningArtifactStore`). This story's `ShadowValidator` class is fully self-contained and independently callable/testable — do not touch `owls/evolution.py` in this story.

### Architecture Compliance

- AD-1: this story's replay is a READ-only dry-run — it never writes to `owl_dna`, never calls `LearningArtifactStore.checkpoint()` itself (Story 2.6 wires checkpoint→validate→commit; this story only builds the "validate" filter, standalone).
- AD-3: `n_consecutive_required`/`sample_size` are the "single shared config" — Story 2.6 (nightly batch) and Story 3.2 (`evolve_now`) must both construct `ShadowValidator` with the SAME values (the module-level defaults), never a looser per-caller override. This story's job is to make that structurally easy (one class, one set of defaults) — enforcement across both callers is Story 2.6/3.2's job, but the seam has to be right here first.
- AD-2: `ShadowValidator` does NOT touch `LearningArtifactStore` in this story — that composition happens in Story 2.6.
- NFR-3: 4-point logging, `log.owls` namespace.
- NFR-4: this story's own test IS effectively a gateway-driven integration test already (it drives the real pipeline via `AsyncioBackend`, mocking only the AI provider) — satisfies NFR-4 for the new code this story adds.

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`.
- A FAKE/stub provider is required (not the real network LLM) for deterministic tests — but the PRODUCTION code path (`validate()` itself) must call whatever provider `ProviderRegistry` resolves, exactly like `CriticScorerHandler`/`ReflectionWriterHandler` do — do not special-case test-vs-prod branching inside `shadow_validator.py` itself.
- Run: `uv run pytest tests/owls/test_shadow_validator.py -v`. Do NOT run the full suite (hangs on this box).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- New: `src/stackowl/owls/shadow_validator.py`, `tests/owls/test_shadow_validator.py`. No migration (no new persisted state — replay results are transient, returned to the caller, not stored by this story).

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.5] (lines 232-252)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 4] (FR-8, FR-9)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-1, AD-3, Structural Seed, Deferred section — "shadow_validator.py's held-out sample selection strategy")
- [Source: src/stackowl/tools/verification.py] (direct read — `is_trustworthy_success(success, verified)`, reused as-is)
- [Source: src/stackowl/owls/dna_attribution.py] (direct read — `_filter_scored_outcomes`, the pattern this story's held-out filter is adjacent to but NOT identical to; `TaskOutcomeStore.list_scored_for_owl` is the exact method to reuse)
- [Source: src/stackowl/memory/critic_scorer_handler.py], [Source: src/stackowl/memory/critic_prompt.py] (direct read — `CriticScorerPromptBuilder`/`parse_critic_response`, reused for replay scoring WITHOUT the handler's DB-coupled wrapper)
- [Source: src/stackowl/owls/registry.py] (direct read — `OwlRegistry.register()`, confirms `apply_dna_overlay`/`registry.replace()` mutate in place and must NOT be called on the live registry during a shadow replay)
- [Source: src/stackowl/owls/dna_hydrator.py] (direct read — `apply_dna_overlay`, confirms why it's unsafe to use here)
- [Source: tests/pipeline/test_plan_a_gateway_integration.py] (direct read — the isolated-`PipelineState`-construction + `AsyncioBackend.run` pattern to mirror for the replay mechanics)
- [Source: src/stackowl/pipeline/backends/asyncio_backend.py] (direct read — `AsyncioBackend.run(state)` signature)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story / Amelia persona)

### Debug Log References

- `uv run pytest tests/owls/test_shadow_validator.py -v` → 4 passed (10.31s), first run, no red-green iteration needed beyond initial implementation.
- `uv run ruff check src/stackowl/owls/shadow_validator.py tests/owls/test_shadow_validator.py` → All checks passed!
- `uv run mypy src/stackowl/owls/shadow_validator.py` → Success: no issues found in 1 source file.
- `uv run mypy src/` (full, per CLAUDE.md) → 79 pre-existing errors in 16 files, NONE in `shadow_validator.py` or its test; none of those 16 files were touched by this story (`plugins/context.py`, `mcp/server.py`, `channels/telegram/notifications.py`, `scheduler/assembly.py`, `startup/orchestrator.py`, `cli/app.py`) — confirmed baseline debt, not introduced here.
- tonyStyle skill scan run over the new module + the wider read-set (dna_attribution.py, verification.py, critic_scorer_handler.py, critic_prompt.py, registry.py, dna_hydrator.py, outcome_store.py, manifest.py, dna.py, providers/registry.py, providers/base.py, pipeline/backends/asyncio_backend.py, pipeline/state.py, pipeline/services.py, pipeline/registry.py, pipeline/backends/shared.py, pipeline/turn_persist.py, pipeline/steps/{deliver,triage,dispatch,assemble,execute}.py, provider_select.py, reflection_store.py) — no silent catches, disabled features, missing 4-point logging, or dead code found; nothing outside this story's own diff needed fixing.

### Completion Notes List

- Implemented `ShadowValidator`/`ShadowValidationResult` exactly per the Dev Notes design: held-out sample via `TaskOutcomeStore.list_scored_for_owl(owl_name, since_epoch=lookback_epoch())`, filtered via a narrower same-shape sibling of `dna_attribution._filter_scored_outcomes` (`_eligible_for_replay`, doc-commented as distinct from the DNA-attribution positive-only rule), replayed through the REAL `AsyncioBackend`/`PipelineState`/`StepServices` seam mirroring `test_plan_a_gateway_integration.py`, scored via `CriticScorerPromptBuilder`/`parse_critic_response` (no `CriticScorerHandler` DB coupling), gated through the unmodified `is_trustworthy_success`.
- **Design decision 1 (tool_registry deliberately omitted)**: the constructor signature the story specifies (`db`, `provider_registry`, `n_consecutive_required`, `sample_size`) has no `tool_registry` parameter, so a replay's scratch `StepServices` never wires one. This makes "no side effects" a *structural* guarantee rather than a policy: without a `tool_registry`, `execute.run` cannot enter the tool-loop branch, so no tool — consequential or read-only — ever runs for real during a replay (real web_fetch/shell/etc. would otherwise be a genuine external side effect the story's "no side effects" language does not clearly authorize). The Dev Notes' "if it [the turn] calls tools, thread the real verified value through" branch is therefore never reached by this implementation; it degrades to the documented tool-free case. If a future story needs tool-fidelity replay, it will need a bounded/read-only tool subset — explicitly out of scope here.
- **Design decision 2 (resolving the oracle's `quality_score`-vs-`verified=None` tension)**: the Dev Notes text describes two things that read as in tension: "quality_score >= 0.6 as a proxy for verified" vs. "if the turn is tool-free, verified=None degrades to `is_trustworthy_success` falling back to success alone." Taking the second literally would make Task 2's own "regression case" test unsatisfiable (`is_trustworthy_success(success=True, verified=None)` is always `True` regardless of quality_score, so a sub-0.6 critic score could never break the streak). Resolved in favor of the literally-specified, testable behavior: `verified = quality_score >= _HIGH_QUALITY_THRESHOLD` (imported from `reflection_store.py`, not re-hardcoded) is always a concrete bool for a tool-free replay (a parse failure maps to `verified=False`, fail-closed, consistent with the cold-start posture); the "verified=None falls back to success" sentence is read as background documentation of `is_trustworthy_success`'s own general semantics, not a literal instruction for this code path. This is the "genuine design fork" the story flagged as deliberately left open.
- **Design decision 3 (loop stops immediately on the first regression or on reaching the threshold)**: "stop early" is taken literally — `validate()` does not keep replaying the rest of the held-out sample after either terminal condition. `n_replayed` reflects only what was actually replayed. This is cheaper (fewer real LLM calls in production) and makes the "a later trustworthy replay does not rescue the streak" test trivially true by construction rather than by a second check.
- `memory_bridge`/`db_pool`/`tool_registry`/`stream_registry` are all left `None` on the scratch `StepServices` (not just `owl_registry`/`provider_registry` set) — verified by direct reads of `_capture_outcome`, `persist_turn`, `feedback.py:_record_rejection`, and `deliver.run` that every one of those already no-ops (structurally, not by a new guard added here) when its respective service is `None`. This is the simplest way to make the isolation guarantee airtight: nothing new to maintain, the no-op behavior is the pipeline's own pre-existing contract.
- All 4 ACs implemented and covered by tests; `ShadowValidator` is NOT called from anywhere yet (by design — Story 2.6 wires it into the promotion flow; `owls/evolution.py` was not touched).

### File List

- `src/stackowl/owls/shadow_validator.py` (new)
- `tests/owls/test_shadow_validator.py` (new)
