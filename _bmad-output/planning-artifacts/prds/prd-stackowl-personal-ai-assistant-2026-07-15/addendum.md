# Addendum — Owl DNA Self-Improvement Lifecycle

Technical-how detail supporting `prd.md`. Architecture and epics/stories should treat this as the primary reuse map — the goal throughout is wiring and thin extension, not rebuilding subsystems that already exist and work.

## Existing subsystem map (repo audit, verified by direct read)

- `owls/dna.py` — `OwlDNA`: 7 clamped `[0,1]` float traits (`challenge_level, verbosity, curiosity, formality, creativity, precision, completion_drive`) + `decay_rate_per_week` (defined, zero readers anywhere — Feature 6 target).
- `owls/dna_defaults.py` — seed/default trait values.
- `owls/dna_authored.py` — `capture_authored_dna()` / `read_authored_dna()`: boot-time capture of the human-authored baseline into `owl_dna_authored`, used as the governor's anchor.
- `owls/dna_storage.py` — `upsert_owl_dna()` (overwrite-in-place, `UNIQUE(owl_name)`, no history) and `DNACheckpointer` (`checkpoint()` — called; `restore()` — **zero callers**, migration `0012_dna_checkpoints.sql`). Feature 1's foundation.
- `owls/dna_governor.py` — `bound_dna()`: the single authoritative clamp (rate cap, authored-envelope, judgment floor). Feature 3 extends this function's inputs, does not replace it.
- `owls/evolution_limits.py` — pure constants: `MAX_DELTA=0.05`, `ENVELOPE=0.3`, `FLOOR_TRAITS`, `DNA_NEUTRAL`.
- `owls/evolution.py` — `EvolutionCoordinator`, `_evolve_one(manifest)` (self-contained per-owl unit — Feature 5 calls this directly), `DeltaValidator` (raw-JSON `[-0.25,0.25]` clamp), checkpoint-then-persist-then-live-refresh ordering (`checkpoint()` → DB write → `apply_dna_overlay()` → audit log). Feature 4's promotion gate slots in between "compute deltas" and "persist".
- `owls/evolution_prompt.py` — `EvolutionPromptBuilder.build()`: the LLM-fallback path Feature 5 must route single-task triggers through (never the statistical path).
- `owls/dna_attribution.py` — `DnaAttributor`: statistical trait-band attribution, `MIN_SAMPLES_FOR_ATTRIBUTION=20`, positive-only filter (`_filter_scored_outcomes`: `success=True AND failure_class is None AND approach_rating != "negative"`) — this filter is the positive-only-learning rule; out of scope to touch.
- `owls/dna_hydrator.py` / `dna_injector.py` — live-refresh (`apply_dna_overlay`) and prompt injection (`DNAPromptInjector`, hysteresis-latched at 0.7/0.3 thresholds). No changes needed for this PRD.
- `scheduler/handlers/evolution.py` — `evolution_batch` handler, seeded daily 02:00 (`scheduler/assembly.py`). Feature 4's gate wraps this handler's promotion step.
- `tools/knowledge/reflect_now.py` — the exact wrapper shape Feature 5's `evolve_now` should mirror: thin tool, constructs the handler off `get_services()`, runs it mid-turn instead of waiting for cron. Feature 2 verifies/hardens this tool's own underlying pipeline (`memory/reflection_writer_handler.py` → retrieval store → `classify.py` semantic recall).
- `tools/knowledge/synthesize_skills.py` / `skills/synthesizer_handler.py` — skills' mining pipeline; `skills/skill_manage.py` → `record_skill_mutation` (hash-diff + snapshot + audit + **wired** `/skill restore`) is Feature 1's template — copy its restore-wiring pattern onto the unified primitive, don't reinvent it.
- `tools/verification.py` — `is_trustworthy_success(success, verified)`: the oracle Feature 4's shadow-validation gate reuses as-is.
- `memory/outcome_store.py` — `TaskOutcome` (`success`, `failure_class`, `quality_score`, `dna_snapshot`, `approach_rating`); `success` is verification-aware (derived from `is_trustworthy_success`), `quality_score` is LLM-judge-only (`CriticScorerHandler`) — this distinction is exactly what Feature 3's tiering keys on.
- `commands/owls_command.py` — `_reset_dna` handler (existing `/owls reset-dna`) is the pattern Feature 1's new restore command mirrors.
- `objectives/*` (epic_runner.py, driver.py) — confirmed zero DNA/evolution touchpoints; not relevant to this PRD.

## Research-to-requirement mapping

- **FR-8–FR-11 (shadow gate)** — directly motivated by the deep-research finding that Gödel Agent's only safety net is catch-and-rollback-**after** a self-edit ships (still nets 14% of runs negative), and SICA's is an archive+overseer, also post-hoc. No reviewed system validates **before** committing. Sources: `openreview.net/forum?id=dML3XGvWmy` (Gödel Agent), `arxiv.org/pdf/2504.15228` (SICA).
- **FR-14 (evolve_now gated behind the shadow gate)** — same research finding, applied to sequencing: shipping a higher-frequency mutation trigger (Feature 5) before the pre-commit gate exists (Feature 4) reproduces exactly the reactive-only risk profile the research flags as the field's unsolved problem. This is why Epic 2 (which contains Feature 5) is ordered strictly after Epic 1 (which contains Feature 4).
- **FR-6–FR-7 (tiered clamp)** — loosely informed by the "Layered Mutability" preprint's framing of harmful drift as an observability gap (actual change outpacing what governance can observe) rather than a pure magnitude threshold. Source: `arxiv.org/html/2604.14717v2` — single-author, unreviewed, treat the underlying theory as a lens, not as validation; the requirement itself (tie magnitude to signal strength) stands on its own engineering merit independent of the paper.
- **Feature 1 (unified versioning)** — the weight-free-learning research (Reflexion/ExpeL/Voyager/AWM) consistently separates "produce a candidate" from "commit it" — StackOwl's existing checkpoint-before-mutate pattern already follows this shape; Feature 1 just stops it being built twice (DNA vs. skills) with only one path actually wired end-to-end.

## Sizing note for architecture/epics-and-stories

Feature 4 (shadow-validation gate) is the largest single piece of new logic in this PRD — it needs a replay harness against held-out real interactions, which nothing in the existing codebase does today. Everything else is either wiring an existing method to a caller (Feature 1's restore, most of Feature 5) or a bounded, local change to one function (Feature 3's clamp, Feature 6's decision). Architecture should size story granularity accordingly — Feature 4 likely warrants its own multi-story breakdown; Features 1, 3, 6 are each plausibly single-story.
