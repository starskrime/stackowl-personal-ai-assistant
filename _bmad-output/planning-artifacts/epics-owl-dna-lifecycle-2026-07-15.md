---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md
  - _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/addendum.md
  - _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md
---

# Owl DNA Self-Improvement Lifecycle - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for Owl DNA Self-Improvement Lifecycle, decomposing the requirements from the PRD and Architecture Spine into implementable stories. No UX design contract applies — this is a backend capability spec with no UI surface (single-operator internal tool, confirmed in the PRD's Shape Fit review).

Note: kept as a separate file from the project's existing `epics.md` (which tracks the unrelated failure-retry-loop arc) to avoid colliding with that in-flight/planned tracking.

## Requirements Inventory

### Functional Requirements

FR-1: A single versioning primitive (snapshot, hash-diff, restore, audit row) serves both DNA mutations and skill mutations.
FR-2: An owl's DNA can be restored to any specific prior checkpoint — not only the authored baseline — via an explicit, human-facing command.
FR-3: Every mutation (DNA or skill) writes an audit row recording what changed, why, and when.
FR-4: A failed or low-quality task's reflection is verifiably retrievable on a later turn with matching context — proven with an end-to-end regression test.
FR-5: Any break found anywhere in the reflect → store → recall chain is fixed as part of this feature, not merely reported.
FR-6: A DNA mutation's allowed magnitude scales with the strength of the signal behind it: verified > outcome-binary > LLM-judged quality.
FR-7: The existing hard ceiling (rate cap, authored-baseline envelope, judgment floor) remains final and non-negotiable regardless of signal strength.
FR-8: Before any batch of proposed DNA deltas is promoted from checkpoint to live, it is validated against a held-out sample of the owl's own recent real interactions, in a context with no side effects.
FR-9: Promotion requires N consecutive non-regressions (default 3, operator-configurable) using the existing trustworthy-success verification primitive as the pass/fail oracle.
FR-10: A failed validation automatically restores the pre-mutation checkpoint — no separate or new rollback mechanism.
FR-11: The gate applies uniformly to the nightly batch AND any per-task trigger — enforced structurally, with no bypass path for either caller.
FR-12: An owl can trigger its own DNA evolution mid-turn, immediately after a completed task, instead of only via the nightly batch.
FR-13: A single-task trigger never uses the statistical attribution path — it always routes through the existing LLM-fallback path, under the exact same clamps and governance as the batch path.
FR-14: A per-task trigger is subject to the same shadow-validation gate as the batch path — by construction. May not ship ahead of the shadow gate.
FR-15: The dormant `decay_rate_per_week` field either gets a real, tested decay-toward-authored-baseline mechanism, or is removed.
FR-16: A skill's measured success rate becomes an available input signal to DNA attribution, and an owl's evolved traits become an available input to skill retention/synthesis decisions.
FR-17: This cross-signal is additive and advisory only — it must not weaken either subsystem's existing gates.

### NonFunctional Requirements

NFR-1: No existing capability is removed or disabled by this work.
NFR-2: Every new persisted field or table goes through a proper migration.
NFR-3: Every new `execute()`-style method carries 4-point logging (entry/decision/step/exit).
NFR-4: Anything touching the live turn pipeline gets a gateway-driven integration test that mocks only the AI provider.
NFR-5: The nightly `evolution_batch` job's existing behavior stays backward-compatible except where explicitly changed.

### Additional Requirements (from Architecture Spine)

- Paradigm: Governed Pipes-and-Filters (propose → clamp → validate → commit → observe) — every story's implementation must slot into this pipeline, not build a parallel path (AD-1).
- `LearningArtifactStore` (new, `owls/learning_artifact_store.py`) is the single versioning primitive for both DNA and skills, superseding `DNACheckpointer` and `record_skill_mutation`'s internal storage logic (AD-2).
- The shadow-validation gate is the only function allowed to promote a checkpoint to live storage; both the nightly batch and `evolve_now` call the same function (AD-3).
- Signal-strength tiering computes an effective delta strictly ≤ the raw proposed delta, before `bound_dna()`'s existing clamp — never widens the ceiling (AD-4). Requires a shared `SignalStrength` enum defined once in `dna_governor.py`.
- `evolve_now` routes exclusively through `evolution_prompt.py`'s LLM-fallback path — never branches on `DnaAttributor`'s sample count (AD-5).
- `decay_rate_per_week` gets implemented (weekly exponential decay toward authored baseline), not deleted — flagged `[ASSUMPTION]` in the spine, not yet re-confirmed with the user (AD-6).
- Feature 7 cross-signal is advisory-only in both directions; exact consumption mechanism deferred to story-time (AD-7).
- New migration required: `db/migrations/00XX_learning_artifact_store.sql` (unified snapshot table, DNA + skill artifact types).
- Structural seed (file landing spots): `owls/learning_artifact_store.py` (new), `owls/shadow_validator.py` (new), `owls/dna_governor.py` (modified), `owls/evolution.py` (modified), `owls/dna.py` (modified), `owls/dna_attribution.py` (modified), `tools/knowledge/evolve_now.py` (new), `skills/synthesizer_handler.py` (modified), `commands/owls_command.py` (modified).
- Sizing note (from PRD): Feature 4 / the shadow-validation gate is the largest new-logic surface (no prior art in this codebase or, per research, in the literature) — likely needs its own multi-story breakdown. Features 1, 3, 6 are each plausibly single-story.

### UX Design Requirements

None — no UI surface, single-operator internal tool (confirmed in PRD Shape Fit).

### FR Coverage Map

FR1: Epic 2 - unified versioning primitive
FR2: Epic 2 - restore to any checkpoint
FR3: Epic 2 - mutation audit row
FR4: Epic 1 - reflect_now retrieval proven end-to-end
FR5: Epic 1 - reflect_now chain fixed if broken
FR6: Epic 2 - signal-strength-tiered mutation magnitude
FR7: Epic 2 - existing hard clamp stays the ceiling
FR8: Epic 2 - shadow-validation gate before promotion
FR9: Epic 2 - N-consecutive-non-regression gate
FR10: Epic 2 - failed validation auto-restores checkpoint
FR11: Epic 2 - gate applies uniformly, no bypass
FR12: Epic 3 - mid-turn evolution trigger
FR13: Epic 3 - evolve_now forced onto LLM-fallback path
FR14: Epic 3 - evolve_now subject to the same shadow gate
FR15: Epic 3 - decay_rate_per_week resolved (implement)
FR16: Epic 3 - skill success_rate <-> DNA attribution signal
FR17: Epic 3 - cross-signal advisory-only

NFR1-NFR5: cross-cutting, apply to all three epics (no capability removed, migrations, 4-point logging, gateway-integration tests, backward-compatible nightly batch).

**Revision note (post-elicitation):** original 2-epic split (per PRD) bundled reflect_now-reliability (Feature 2) into the DNA-safety epic despite sharing zero files/dependencies with it. Advanced-elicitation pressure-test (Red Team, Second-Order Thinking, Inversion, Reframe, Pre-mortem — all 5 converged independently) found this diluted the safety epic's identity and done-criteria. Split into 3 epics below; PRD's Epic 1 → Epic 2, PRD's Epic 2 → Epic 3, new Epic 1 carved out.

## Epic List

### Epic 1: Reflect-Now Reliability
An owl's failure-reflection actually gets recalled on a matching future turn — proven, not assumed. The repo audit found this chain likely already works end-to-end (written to a retrieval store, recalled via semantic search) — this epic may resolve to "confirmed working, regression test added" as validly as "found broken, fixed." Standalone, fast, low-risk: touches only the existing reflect→store→recall chain (`memory/reflection_writer_handler.py` and its recall path), zero coupling to DNA mutation machinery — any fix required here must stay out of DNA storage entirely (that's Epic 2's territory, governed by AD-1). Ships first, independent of the other two epics, and is this PRD's literal answer to "won't repeat the same issue."
**FRs covered:** FR4, FR5

### Epic 2: Safe Self-Improvement Foundation
An owl's existing nightly DNA evolution can be checked before it ships and undone if it's wrong — the complete safety spine, valuable and shippable on its own even if Epic 3 never happens. This is a safety retrofit onto evolution that already runs today, not a new evolution capability. Standalone: reuses `dna_governor`, `evolution.py`, `dna_attribution`; adds the one genuinely new capability (the shadow-validation gate) and the one shared primitive (`LearningArtifactStore`) Epic 3 depends on.
**FRs covered:** FR1, FR2, FR3, FR6, FR7, FR8, FR9, FR10, FR11
**Hardening notes from elicitation (in scope for this epic's stories):**
- A manual dry-run/replay capability for the shadow gate, independent of Epic 3's evolve_now trigger — without it, the gate only exercises once/day via the nightly batch and its value is unobservable for days (Second-Order Thinking finding).
- Gate rejections get real visibility (not just a buried WARNING log) when N rejections occur in a row (Inversion Analysis finding).
- Gate's N-threshold and held-out sample size are single-shared config, not per-caller-configurable (Inversion + Red Team finding; now AD-3 in the architecture spine).

### Epic 3: Real-Time Activation & Cross-Signal Polish
An owl acts on Epic 2's safety net immediately after a task instead of waiting for the nightly batch, its dormant decay field does what it claims to do, and its two self-improvement subsystems (DNA, skills) start sharing signal instead of operating blind to each other. Standalone in the sense that it strictly consumes Epic 2's gate (AD-3) rather than needing anything further built — Epic 2 alone is already complete and safe without this epic ever shipping.
**FRs covered:** FR12, FR13, FR14, FR15, FR16, FR17
**Depends on:** Epic 2's shadow-validation gate (FR8-FR11) must exist before FR12-FR14 can ship — the one deliberate cross-epic dependency (AD-3), not incidental.

---

## Epic 1: Reflect-Now Reliability

An owl's failure-reflection actually gets recalled on a matching future turn — proven, not assumed.

### Story 1.1: Prove the reflect → store → recall chain end-to-end

As the platform,
I want an automated regression test that writes a failure reflection and then confirms it's retrieved on a matching later turn,
So that "won't repeat the same issue" is a measured guarantee, not an assumption.

**Acceptance Criteria:**

**Given** a task fails or scores low quality
**When** `reflect_now`'s underlying handler (`memory/reflection_writer_handler.py`) processes it
**Then** a reflection is durably written to the retrieval store
**And** a later turn with matching context (same failure class/topic) surfaces that reflection via `classify.py`'s semantic recall
**And** an automated test proves this end-to-end, not just each stage in isolation (FR-4)

**Given** the test above
**When** it's run against the current, unmodified pipeline
**Then** the result (pass or fail) is reported honestly — this story does not assume a fix is needed

### Story 1.2: Fix any break found in the chain

As the platform,
I want any gap Story 1.1 finds in the reflect → store → recall chain fixed,
So that the guarantee Story 1.1 tests for is actually true, not just tested for.

**Acceptance Criteria:**

**Given** Story 1.1's regression test fails on some stage of the chain
**When** the failing stage is identified (write, storage, or recall)
**Then** the minimal root-cause fix is applied at that stage — no workaround, no new parallel path (FR-5)
**And** Story 1.1's regression test passes afterward
**And** NFR-1 holds: no existing capability is removed or weakened by the fix

**Given** Story 1.1's regression test already passes with no changes
**When** this story is picked up
**Then** it is marked complete with no code change, and the finding ("already worked, confirmed by test") is recorded — a valid, expected outcome per this epic's framing

---

## Epic 2: Safe Self-Improvement Foundation

An owl's existing nightly DNA evolution can be checked before it ships and undone if it's wrong.

### Story 2.1: LearningArtifactStore — unified versioning primitive

As the platform,
I want one snapshot/hash-diff/restore/audit primitive shared by DNA and skill mutations,
So that versioning isn't built twice with only one path (skills) actually wired end-to-end.

**Acceptance Criteria:**

**Given** a new migration (`db/migrations/00XX_learning_artifact_store.sql`)
**When** it runs
**Then** a unified snapshot table exists holding `(artifact_type: "dna"|"skill", artifact_id, payload_json, reason, created_at)` (FR-1, AD-2)

**Given** `owls/learning_artifact_store.py`'s new `LearningArtifactStore` class
**When** `checkpoint()` is called for either artifact type
**Then** a snapshot row is written, and `restore(checkpoint_id)` returns the exact prior payload for that row (FR-1)

**Given** any mutation (DNA or skill) processed through `LearningArtifactStore`
**When** it commits
**Then** an audit row records what changed, why, and when (FR-3)

**Given** NFR-2 and NFR-3
**When** this story ships
**Then** the migration is idempotent and every new method carries 4-point logging

### Story 2.2: DNA restore command

As the operator,
I want to restore an owl's DNA to any specific prior checkpoint, not only the authored baseline,
So that a bad evolution cycle is fully and precisely reversible.

**Acceptance Criteria:**

**Given** Story 2.1's `LearningArtifactStore`
**When** the operator runs `/owls dna-restore <name> <checkpoint_id>` (new command in `commands/owls_command.py`, mirroring the existing `_reset_dna` handler)
**Then** that owl's live DNA is restored to exactly the trait values in that checkpoint (FR-2)
**And** the existing `/owls reset-dna` (restore-to-authored-baseline) command is untouched and still works (NFR-1)

**Given** an invalid or unknown `checkpoint_id`
**When** the command is run
**Then** it fails loudly with a clear error — no silent no-op

### Story 2.3: Migrate DNA and skill mutation call sites onto LearningArtifactStore

As the platform,
I want `owls/evolution.py`'s existing `checkpoint()` call and `skill_manage.py`'s `record_skill_mutation` internals both routed through `LearningArtifactStore`,
So that `DNACheckpointer` and skills' independent versioning logic are superseded, not left as parallel duplicates (AD-2).

**Acceptance Criteria:**

**Given** `EvolutionCoordinator`'s existing checkpoint-then-persist flow
**When** this story ships
**Then** it calls `LearningArtifactStore.checkpoint()` instead of `DNACheckpointer.checkpoint()` directly

**Given** `skill_manage.py`'s existing mutation-versioning call site
**When** this story ships
**Then** it calls `LearningArtifactStore` for its snapshot/restore/audit needs, and its existing wired `/skill restore` command continues to work unchanged (NFR-1)

### Story 2.4: Signal-strength-tiered mutation clamp

As the platform,
I want a DNA mutation's allowed magnitude to scale with the strength of the signal behind it,
So that a verified win can move a trait further than an LLM's opinion of quality alone.

**Acceptance Criteria:**

**Given** a new shared `SignalStrength` enum (`VERIFIED | OUTCOME_BINARY | LLM_QUALITY`) defined once in `dna_governor.py`
**When** any propose-stage caller computes a delta
**Then** it tags the delta with the signal that produced it (FR-6)

**Given** a tagged delta
**When** it reaches `bound_dna()`
**Then** the effective delta passed in is scaled down for `OUTCOME_BINARY` and `LLM_QUALITY` relative to `VERIFIED`, strictly ≤ the raw proposed delta (FR-6, AD-4)

**Given** any signal strength
**When** `bound_dna()`'s existing clamp (rate cap, envelope, judgment floor) applies
**Then** that clamp is never widened by signal strength — it remains the final, unconditional ceiling (FR-7, AD-4)

### Story 2.5: Shadow-validation gate — replay harness core

As the platform,
I want proposed DNA deltas validated against a held-out sample of the owl's own recent real interactions before they ship,
So that a bad mutation is caught before it affects a real turn, not after.

**Acceptance Criteria:**

**Given** a new `owls/shadow_validator.py` module
**When** a batch of proposed deltas is ready to promote
**Then** it replays a held-out sample of that owl's recent real interactions against the proposed DNA, in a context with no side effects (FR-8)

**Given** the replay results
**When** each replayed interaction is scored
**Then** `tools/verification.py`'s `is_trustworthy_success()` is reused as-is as the pass/fail oracle — no new verification primitive invented (FR-8)

**Given** N consecutive non-regressions (default 3, operator-configurable via the shared config from the AD-3 amendment)
**When** that threshold is met
**Then** the batch is eligible for promotion (FR-9)

**Note:** the held-out sample size and "recent"/"held-out" replay-contamination strategy are NOT pre-specified — this is deliberately deferred to this story per the architecture spine's Deferred section, since it's genuinely novel work with no prior art. Decide and document the strategy as part of implementing this story, don't search for a spec that doesn't exist elsewhere.

### Story 2.6: Wire the gate into promotion, with auto-restore on failure

As the platform,
I want the shadow-validation gate to be the *only* path that promotes a checkpoint to live DNA, with automatic rollback on failure,
So that no caller — today or in the future — can bypass validation, by accident or "just this once."

**Acceptance Criteria:**

**Given** `EvolutionCoordinator`'s existing checkpoint → persist → live-refresh → audit flow
**When** this story ships
**Then** Story 2.5's gate is inserted between checkpoint and persist — persist only happens after the gate passes (FR-8, AD-1, AD-3)

**Given** the gate fails (does not reach N consecutive non-regressions)
**When** promotion is denied
**Then** the pre-mutation checkpoint is automatically restored via Story 2.1's `LearningArtifactStore.restore()` — no separate rollback mechanism (FR-10)

**Given** the nightly `evolution_batch` job
**When** it runs after this story ships
**Then** its behavior is unchanged except for now passing through the gate (NFR-5) — no capability regression if the gate always passes on today's real data

### Story 2.7: Gate observability and manual dry-run

As the operator,
I want to see when the shadow-validation gate rejects a batch, and be able to trigger it on demand,
So that the gate's behavior is observable without waiting on the once-a-day nightly cycle (elicitation hardening notes).

**Acceptance Criteria:**

**Given** the gate rejects a batch (fails to reach N consecutive non-regressions)
**When** this happens
**Then** it is logged at a visible level (not buried at WARNING) with the specific non-regression that failed, and the rejection is queryable/countable — not just a single log line

**Given** the operator wants to exercise the gate without waiting for the nightly cron
**When** a manual dry-run command or tool is invoked
**Then** it runs Story 2.5's replay harness against the current live DNA and reports pass/fail, without mutating anything

**Given** Story 2.6's shared gate config (N-threshold, held-out sample size)
**When** the manual dry-run runs
**Then** it uses the exact same config as the real promotion path — not a looser one (closes the AD-3 letter-vs-intent gap)

---

## Epic 3: Real-Time Activation & Cross-Signal Polish

An owl acts on Epic 2's safety net immediately after a task instead of waiting for the nightly batch.

### Story 3.1: evolve_now tool

As an owl,
I want to trigger my own DNA evolution immediately after finishing a task,
So that I don't have to wait for the nightly batch to potentially learn from what just happened.

**Acceptance Criteria:**

**Given** a new `tools/knowledge/evolve_now.py`, mirroring `reflect_now.py`'s thin-wrapper shape
**When** it's invoked mid-turn
**Then** it constructs `EvolutionCoordinator` off `get_services()` and calls `_evolve_one` for the current owl only (FR-12)

**Given** `DnaAttributor`'s statistical path requires ≥20 scored outcomes, which a single task can never meet
**When** `evolve_now` computes a delta
**Then** it is parameterized to force the LLM-fallback path (`evolution_prompt.py`) unconditionally — it never branches on or checks `DnaAttributor`'s sample count (FR-13, AD-5)

### Story 3.2: Route evolve_now through the shared shadow gate

As the platform,
I want `evolve_now`'s proposed delta to pass through the exact same shadow-validation gate as the nightly batch,
So that the per-task trigger can never ship a mutation the batch path wouldn't also allow.

**Acceptance Criteria:**

**Given** Story 3.1's `evolve_now` and Epic 2's shadow-validation gate
**When** `evolve_now` proposes a delta
**Then** it calls the exact same gate function Story 2.6 wired into the batch path — not a second, parallel promotion function (FR-14, AD-1, AD-3)

**Given** this story
**When** it ships
**Then** `evolve_now` cannot ship ahead of Epic 2's Story 2.6 — this story has a hard dependency on Epic 2 being complete (per the PRD's explicit build-order decision)

### Story 3.3: Resolve decay_rate_per_week

As an owl,
I want an unreinforced trait to drift back toward my authored baseline over time,
So that a stale, unconfirmed personality shift doesn't persist indefinitely.

**Acceptance Criteria:**

**Given** `OwlDNA.decay_rate_per_week` (currently defined, zero readers)
**When** this story ships
**Then** a decay function moves an unreinforced trait toward its authored baseline at that rate, itself passing through Epic 2's clamp/gate pipeline like any other mutation (FR-15, AD-1, AD-6)

**Given** a trait that has been reinforced recently (within the decay window)
**When** the decay function runs
**Then** that trait is not decayed — only genuinely unreinforced traits move

### Story 3.4: Skill success rate feeds DNA attribution (advisory)

As the platform,
I want a skill's measured success rate available as an input signal to DNA attribution,
So that DNA evolution has more signal than turn-level outcomes alone.

**Acceptance Criteria:**

**Given** a skill's tracked `success_rate` (`_update_skill_success_rates`)
**When** `DnaAttributor` runs
**Then** this signal is read and factored into at least one real attribution decision per cycle — not merely exposed as an unused getter — while never gating or vetoing what the existing positive-only-learning filter already decides (FR-16, FR-17, AD-7)

### Story 3.5: DNA traits inform skill retention (advisory)

As the platform,
I want an owl's evolved traits available as an advisory weight on skill retention/synthesis decisions,
So that the two self-improvement subsystems stop operating completely blind to each other.

**Acceptance Criteria:**

**Given** `skills/synthesizer_handler.py`'s existing retention/synthesis decision
**When** this story ships
**Then** the owl's current DNA traits are read and factored into at least one real retention/synthesis decision per cycle — not merely exposed as an unused getter — while the existing skill security-scan gate and retention logic are not weakened or bypassed (FR-16, FR-17, AD-7)
