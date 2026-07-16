---
baseline_commit: 6db0f995
---

# Story 3.1: evolve_now tool

Status: done

## Story

As an owl,
I want to trigger my own DNA evolution immediately after finishing a task,
so that I don't have to wait for the nightly batch to potentially learn from what just happened.

## Acceptance Criteria

1. **Given** a new `tools/knowledge/evolve_now.py`, mirroring `reflect_now.py`'s thin-wrapper shape
   **When** it's invoked mid-turn
   **Then** it constructs `EvolutionCoordinator` off `get_services()` and calls `_evolve_one` for the current owl only (FR-12)

2. **Given** `DnaAttributor`'s statistical path requires ≥20 scored outcomes, which a single task can never meet
   **When** `evolve_now` computes a delta
   **Then** it is parameterized to force the LLM-fallback path (`evolution_prompt.py`) unconditionally — it never branches on or checks `DnaAttributor`'s sample count (FR-13, AD-5)

## Important — this story ALREADY ships through the shadow gate, and that's correct, not a scope violation

Epic 3 is built after Epic 2 is fully complete (per this project's own build order — Epic 2, including Story 2.6's gate-wiring and Story 2.7's observability, shipped and was reviewed before this story was written). Story 2.6 already made `EvolutionCoordinator._checkpoint_validate_and_promote()` **the single, structural promotion path** — per AD-1 ("every mutation... reaches storage only via Commit... no tool, handler, or command may call a storage write method directly") there is no other way for ANY new caller to persist a DNA mutation without going through it. So this story's new method necessarily calls `_checkpoint_validate_and_promote()`, which means `evolve_now` is gated from the moment it exists — Story 3.2 (`3-2-route-evolve-now-through-shadow-gate`, next) does NOT need to add gate-wiring that doesn't already exist; it becomes a verification/regression-test story (proving the shared-function guarantee holds, closing FR-14's letter), the same pattern Story 1.2 used when its own regression test already passed clean. Do not build a second, ungated promotion path in THIS story just to artificially preserve a "gate added later" narrative — that would violate AD-1 for no reason and require Story 3.2 to then rip it out.

## Tasks / Subtasks

- [x] Task 1: `EvolutionCoordinator.evolve_one_owl_now()` — forces LLM-fallback, skips attribution entirely (AC #2, FR-13, AD-5)
  - [x] New public method on `EvolutionCoordinator` (`owls/evolution.py`): `async def evolve_one_owl_now(self, owl_name: str) -> bool`. Structurally never calls `self._try_attribution`/`self._attributor` — do not add a boolean flag that "skips" attribution (a flag is a branch DnaAttributor's sample count could theoretically influence later); write a genuinely separate code path that goes straight to the LLM-fallback call, so AD-5's "never branches on DnaAttributor's sample count" is true by construction, not by a conditional that happens to always take one branch today.
  - [x] Look up the manifest: `manifest = self._owl_registry.get(owl_name)` (raises on unknown owl — let it propagate, matches this file's existing convention, e.g. `_dna_restore`'s `registry.get(name)`)
  - [x] Build a minimal `AttributionReport` for `_llm_fallback`'s signature (it takes `attribution: AttributionReport` for its `stats_summary` embed) WITHOUT running any attribution query: `AttributionReport(owl_name=owl_name, n_scored_outcomes=0, deltas={}, per_trait=(), explore_fired=False, explore_trait=None, fallback_reason="evolve_now: single-task trigger, forced LLM-fallback path (FR-13/AD-5)")` — this is honest metadata (zero attribution samples WERE consulted, by design), not a fake/misleading report.
  - [x] `deltas = await self._llm_fallback(manifest, attribution)` — reuses `_llm_fallback` UNCHANGED (same excerpt-fetch + prompt-build + provider-call + `DeltaValidator` parse as the nightly batch's fallback branch already does). Note: `_llm_fallback`'s own existing gate (`len(excerpts) < self._batch_size and attribution.n_scored_outcomes == 0: return {}`) will legitimately return `{}` for a brand-new owl with too little conversation history yet — this is correct, existing, unchanged behavior (not enough material to propose from), not something to bypass.
  - [x] `if not deltas: return False` (nothing proposed — matches `_evolve_one`'s existing no-deltas-skip convention)
  - [x] Scale by strategy (`_scale_deltas(deltas, manifest.evolution_strategy)` — reuse, don't reimplement), build `new_dna` via the SAME `.mutate()` loop shape `_evolve_one` uses (kept as an inline loop, matching `_evolve_one`'s existing shape byte-for-byte — minimal diff, no new shared helper)
  - [x] `return await self._checkpoint_validate_and_promote(manifest, new_dna, evolution_source="evolve_now", signal=SignalStrength.LLM_QUALITY)` — reuses Story 2.6's ONE promotion function (checkpoint → clamp → shadow-gate → commit-or-restore → observe), tagged `LLM_QUALITY` (Story 2.4's tiering — this path never has a verified/attribution-backed signal, matches FR-6's framing exactly)
  - [x] 4-point logging, `log.owls` namespace (Story 2.1/2.5's precedent for new `owls/` module code, not `log.engine` which is this file's older convention)
- [x] Task 2: `evolve_now` tool wrapper (AC #1)
  - [x] New file `tools/knowledge/evolve_now.py`, thin wrapper mirroring `tools/knowledge/reflect_now.py`'s EXACT shape: `Tool` subclass, `name="evolve_now"`, `action_severity="read"` (it analyzes/evolves the agent's OWN DNA based on its own outcomes — same rationale `reflect_now` uses for not being consent-gated: "not the user's data and not an external side effect"), `toolset_group="knowledge"`
  - [x] `execute(**kwargs)`: resolve deps off `get_services()` (`db_pool`, `provider_registry`, `owl_registry` — mirror `reflect_now`'s missing-deps degrade-to-structured-failure pattern exactly, same three-dep check shape), get the current owl name via `TraceContext.get().get("owl_name")` — if `None` (untraced/test context), degrade to a structured failure (`"evolve_now unavailable: no owl context for this turn"`), never raise
  - [x] Construct `EvolutionCoordinator(db_pool, provider_registry, owl_registry)` (same constructor shape Story 2.6 already established, with its own internal `ShadowValidator`/`LearningArtifactStore` wiring — nothing new to configure here) and call `await coordinator.evolve_one_owl_now(owl_name)`
  - [x] Map the bool result to a `ToolResult`: `True` → `"evolved:1"`, `False` → still `success=True` with `"evolved:0"` (no deltas proposed, or gate rejected — these are NORMAL outcomes, not tool failures; only a genuine exception is a tool failure, matching `reflect_now`'s success/failure semantics exactly)
  - [x] Any exception during the coordinator call → B5 structural degrade (log ERROR with `exc_info`, return a failed `ToolResult`), never raise — mirrors `reflect_now`'s exact try/except shape
  - [x] Description text: states the LANE/ANTI-LANE pattern `reflect_now`'s docstring/description use
- [x] Task 3: Tests (AC #1, #2)
  - [x] `tests/owls/test_evolve_one_owl_now.py` (new file — kept `test_evolution_feedback.py` uncrowded): asserts `evolve_one_owl_now` NEVER calls `DnaAttributor.attribute` (spy attributor that would return a confident delta if called — the concrete regression test for AD-5)
  - [x] Asserts it goes through `_checkpoint_validate_and_promote` — drives Story 2.6's `AlwaysPassShadowValidator`/`AlwaysFailShadowValidator` stubs from `tests/_story_2_6_helpers.py` to prove both promotion (live registry + SQLite + checkpoint row) and rejection (DNA restored to pre-mutation baseline) actually happen
  - [x] `tests/tools/knowledge/test_evolve_now.py` (new file, mirrors `test_phaseB_self_improvement.py`'s reflect_now coverage): missing-deps degrade, no-owl-context degrade, exception degrade, no-material-yet (`evolved:0`, `success=True`), happy path (`evolved:1`, live DNA updated)
  - [x] Registration: `evolve_now` registered in `ToolRegistry.with_defaults()` (`tools/registry.py`) AND added to the guaranteed base tool set (`tools/_infra/presentation.py` `_DEFAULT_BASE`, cap bumped 35→36 in lockstep, same pattern as every prior base addition) — mirrors `reflect_now` exactly, which is presented via base-set membership, not merely `ToolRegistry` registration. Added the corresponding assertion to `test_self_improvement_tools_in_presented_schema` (`test_phaseB_self_improvement.py`) plus a direct `registry.get("evolve_now") is not None` assertion in the new tool test file. This was NOT itemized in "Project Structure Notes" but is required for AC #1 ("invoked mid-turn") to hold for a real (especially weak) model — registered-but-not-presented is this codebase's most frequently recurring self-extension bug (see e.g. the P2/owl_build, cronjob, and skills-discovery base-set additions this same file documents).
- [x] Task 4: QA + dev review, tests/ruff/mypy green — **do NOT commit**, leave status=review; the orchestrating session runs independent review and commits (same process note as Stories 2.6/2.7)

## Dev Notes

### Architecture Compliance

- AD-1: `evolve_now`'s tool wrapper never writes to `owl_dna` itself — it only calls `EvolutionCoordinator.evolve_one_owl_now()`, which routes through `_checkpoint_validate_and_promote` exactly like the nightly batch does. No side door.
- AD-3: this IS "the same shared gate function" FR-14/Story 3.2 requires — see the "Important" section above.
- AD-5: `evolve_one_owl_now` is a genuinely separate code path from `_evolve_one`'s attribution-first branch, not a parameterized toggle on it — this is a deliberate structural choice, not just an implementation detail, so a future "optimization" can't accidentally reintroduce a sample-count check into this path.
- NFR-3: 4-point logging on both the new `EvolutionCoordinator` method and the tool's `execute()`.

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`, stub only the LLM provider / `ShadowValidator` boundary.
- Run: whatever test file(s) you land the new tests in, plus `tests/owls/test_evolution_feedback.py` and `tests/owls/test_shadow_validator.py` (regression). Do NOT run the full suite (hangs on this box).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- New: `src/stackowl/tools/knowledge/evolve_now.py`, its test file. Modified: `src/stackowl/owls/evolution.py` (new `evolve_one_owl_now` method only — do not touch `_evolve_one`'s existing body). No migration.

### Process note (same as Stories 2.6/2.7)

Implement + test + verify gates green, set status=review, and STOP. Do NOT `git commit`.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 3.1] (lines 300-314)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 5] (FR-12, FR-13)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-1, AD-3, AD-5)
- [Source: src/stackowl/tools/knowledge/reflect_now.py] (direct read — the exact thin-wrapper shape to mirror: deps check, degrade patterns, ToolResult mapping)
- [Source: src/stackowl/owls/evolution.py] (direct read — `_evolve_one`'s attribution/LLM-fallback branching, `_llm_fallback`'s signature/gate, `_checkpoint_validate_and_promote`'s signature from Story 2.6)
- [Source: src/stackowl/infra/trace.py] (direct read — `TraceContext.get().get("owl_name")`, confirmed the pattern several existing tools already use, e.g. `tools/meta/owl_build.py:1286`, `tools/agents/delegate_task.py:959`)
- [Source: _bmad-output/implementation-artifacts/2-6-wire-gate-into-promotion-auto-restore.md] (Story 2.6 — `_checkpoint_validate_and_promote`'s exact signature this story calls)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story, subagent: Amelia)

### Debug Log References

- Targeted rerun (all green): `uv run pytest tests/owls/test_evolution_feedback.py tests/owls/test_shadow_validator.py tests/owls/test_evolve_one_owl_now.py tests/tools/knowledge/test_evolve_now.py tests/tools/knowledge/test_phaseB_self_improvement.py -v` → 25 passed
- `uv run pytest tests/tools/test_presentation.py tests/tools/test_presentation_memory.py tests/tools/test_presentation_skill_discovery.py tests/tools/test_presentation_owl_build.py -v` → 18 passed (regression check for the `_DEFAULT_BASE`/`_DEFAULT_CAP` edit)
- `uv run ruff check` on all touched files → clean
- `uv run mypy src/` → 0 errors in touched files (79 pre-existing errors elsewhere in the tree, unrelated to this story, left untouched)

### Completion Notes List

- `EvolutionCoordinator.evolve_one_owl_now()` added as a structurally separate method from `_evolve_one` — never references `self._try_attribution`/`self._attributor`. Builds an honest zero-sample `AttributionReport`, calls `_llm_fallback` (unchanged) unconditionally, then reuses `_checkpoint_validate_and_promote` (Story 2.6) tagged `SignalStrength.LLM_QUALITY`. Per the story's "Important" note, this means evolve_now ships through the shadow-validation gate by construction — no second ungated path was built.
- `tools/knowledge/evolve_now.py` added as a thin wrapper mirroring `reflect_now.py`'s shape: 3-dep missing-service degrade, no-owl-context degrade (new — `reflect_now` doesn't need this, `evolve_now` does), exception degrade, `evolved:1`/`evolved:0` output mapping (both `success=True`).
- **Deviation from the story's literal task list, with rationale**: registered `evolve_now` into `tools/_infra/presentation.py`'s `_DEFAULT_BASE` guaranteed set (cap bumped 35→36), not just `ToolRegistry.with_defaults()`. The story's "Project Structure Notes" only listed `tools/registry.py`/`evolution.py` as touched files and didn't mention `presentation.py`. However, Task 3's own "Registration" bullet says to confirm evolve_now is discoverable "the same way reflect_now is" — and `reflect_now`'s actual discoverability comes from `_DEFAULT_BASE` membership (verified by reading `test_phaseB_self_improvement.py::test_self_improvement_tools_in_presented_schema`, which drives an EMPTY profile/pins and still expects `reflect_now` present). `ToolRegistry.with_defaults()` registration alone only makes a tool reachable via `tool_search`'s fuzzy ranking — the exact "registered but not reachable" failure class this codebase has hit repeatedly for other self-extension tools (owl_build, cronjob, skills discovery — see the cap-bump comment history in `presentation.py` itself). Without this, AC #1 ("invoked mid-turn") would not reliably hold for a real/weak model. Added the matching test assertions (`test_self_improvement_tools_in_presented_schema` extended; new `test_evolve_now_registered_in_tool_registry`).
- `tonyStyle` scan run over the diff and its immediate neighbors (evolution.py, evolve_now.py, registry.py, presentation.py) per CLAUDE.md's mandatory-skill rule — no additional defects found (no silent catches, no missing 4-point logging, no architecture violations beyond the one already corrected above).
- Two LLM_QUALITY signal-strength scaling assertions in the new tests were corrected mid-implementation (raw delta 0.02 × 0.3 = 0.006 → 0.506, not 0.52) after the first test run surfaced the actual `bound_dna` clamp behavior — this matches the existing precedent in `test_evolution_feedback.py`'s `test_attribution_path_tagged_verified_llm_fallback_tagged_llm_quality`.

### File List

- `src/stackowl/owls/evolution.py` (modified — new `evolve_one_owl_now` method; `_evolve_one`'s body untouched; one docstring accuracy fix in `_checkpoint_validate_and_promote`'s comment, Story 3.2→3.1 attribution)
- `src/stackowl/tools/knowledge/evolve_now.py` (new)
- `src/stackowl/tools/registry.py` (modified — import + `registry.register(EvolveNowTool())`)
- `src/stackowl/tools/_infra/presentation.py` (modified — `evolve_now` added to `_DEFAULT_BASE`, `_DEFAULT_CAP` 35→36; see Completion Notes deviation)
- `tests/owls/test_evolve_one_owl_now.py` (new)
- `tests/tools/knowledge/test_evolve_now.py` (new)
- `tests/tools/knowledge/test_phaseB_self_improvement.py` (modified — added `evolve_now` to `test_self_improvement_tools_in_presented_schema`'s expected set)
