---
title: Owl DNA Self-Improvement Lifecycle
status: final
created: 2026-07-15
updated: 2026-07-15
---

# Owl DNA Self-Improvement Lifecycle

## Vision

An owl calibrates its own personality traits toward what actually works for the user, safely and reversibly — never by guessing at failures, only by confirmed wins — with a durable, versioned record of how it got there, and a floor that stops it before a bad calibration ships.

## Background

StackOwl already has most of the hard part of this built: seven clamped personality traits per owl, a nightly mutation batch gated by a real three-layer safety governor, a statistical attribution engine, and a versioned checkpoint table. The gap is that half of it isn't wired end-to-end (checkpoint `restore()` has no caller), none of it runs on a single finished task (only a nightly batch), and no pre-commit validation exists anywhere — a gap confirmed by external research to be unsolved in the literature too, not just here. Skills (a separate, parallel self-improvement subsystem) independently built the same versioning problem a second time, and — unlike DNA — actually wired its own restore path.

Full technical detail (existing file/class references, exact reuse points, research citations) is in `addendum.md`.

## Explicitly Out of Scope

- **Failure-driven DNA mutation.** The positive-only-learning rule stays exactly as-is — DNA traits are never nudged by a failed or low-quality outcome. "Won't repeat the same issue" is served by Feature 2 below (hardening the existing reflect → recall pipeline), not by changing what DNA learns from.
- New external dependencies.
- Cross-repo/cross-owl unified confidence scoring (a different, unrelated research thread — see `project_coding_capability_research_2026_07` in memory).

## Features & Functional Requirements

### Feature 1 — Unified Versioning & Rollback

Generalizes DNA's existing (half-wired) checkpoint table and skills' existing (fully-wired) mutation-versioning primitive into one shared mechanism, then wires DNA's missing restore path on top of it.

- **FR-1.** A single versioning primitive (snapshot, hash-diff, restore, audit row) serves both DNA mutations and skill mutations.
- **FR-2.** An owl's DNA can be restored to any specific prior checkpoint — not only the authored baseline — via an explicit, human-facing command.
- **FR-3.** Every mutation (DNA or skill) writes an audit row recording what changed, why, and when.

### Feature 2 — Reflect-Now Reliability

The PRD's answer to "won't repeat the same issue": prove and, if necessary, fix the existing failure-reflection pipeline end-to-end, rather than changing DNA's learning rule.

- **FR-4.** A failed or low-quality task's reflection is verifiably retrievable on a later turn with matching context — proven with an end-to-end regression test, not just confirmed by reading the code.
- **FR-5.** Any break found anywhere in the reflect → store → recall chain is fixed as part of this feature, not merely reported.

### Feature 3 — Signal-Strength-Tiered Mutation Clamp

- **FR-6.** A DNA mutation's allowed magnitude scales with the strength of the signal behind it: a verified outcome gets the largest allowance, an outcome-store binary result gets less, an LLM-judged quality score alone gets the least.
- **FR-7.** The existing hard ceiling (rate cap, authored-baseline envelope, judgment floor) remains final and non-negotiable regardless of signal strength — tiering only narrows the allowance below that ceiling, it never widens it.

### Feature 4 — Pre-Commit Shadow-Validation Gate

The one genuinely novel capability — confirmed by research to not exist in any reviewed self-modifying agent system.

- **FR-8.** Before any batch of proposed DNA deltas is promoted from checkpoint to live, it is validated against a held-out sample of the owl's own recent real interactions, in a context with no side effects.
- **FR-9.** Promotion requires N consecutive non-regressions (default 3, operator-configurable) using the existing trustworthy-success verification primitive as the pass/fail oracle.
- **FR-10.** A failed validation automatically restores the pre-mutation checkpoint via Feature 1 — no separate or new rollback mechanism.
- **FR-11.** The gate applies uniformly to the nightly batch AND any per-task trigger (Feature 5) — enforced structurally, with no bypass path for either caller.

### Feature 5 — Per-Task Evolution Trigger

- **FR-12.** An owl can trigger its own DNA evolution mid-turn, immediately after a completed task, instead of only via the nightly batch.
- **FR-13.** A single-task trigger never uses the statistical attribution path (which requires a minimum sample count no single task can meet) — it always routes through the existing LLM-fallback path, under the exact same clamps and governance as the batch path.
- **FR-14.** A per-task trigger is subject to the same shadow-validation gate as the batch path (Feature 4) — by construction, not by convention. This feature may not ship ahead of Feature 4.

### Feature 6 — `decay_rate_per_week` Resolution

- **FR-15.** The dormant `decay_rate_per_week` field either gets a real, tested decay-toward-authored-baseline mechanism, or is removed. It may not remain defined-but-inert.

### Feature 7 — Skills↔DNA Cross-Signal

- **FR-16.** A skill's measured success rate becomes an available input signal to DNA attribution, and an owl's evolved traits become an available input to skill retention/synthesis decisions.
- **FR-17.** This cross-signal is additive and advisory only — it must not weaken either subsystem's existing gates (positive-only learning, governor clamps, skill security-scan gate).

## Non-Functional Requirements

- **NFR-1.** No existing capability is removed or disabled by this work.
- **NFR-2.** Every new persisted field or table goes through a proper migration.
- **NFR-3.** Every new `execute()`-style method carries 4-point logging (entry/decision/step/exit).
- **NFR-4.** Anything touching the live turn pipeline gets a gateway-driven integration test that mocks only the AI provider.
- **NFR-5.** The nightly `evolution_batch` job's existing behavior stays backward-compatible except where Feature 3 or Feature 4 explicitly change it.

## Success Metrics

- A checkpoint restore reverts DNA to the exact prior trait values (automated test).
- A deliberately-bad batch of deltas fails the shadow-validation gate and is never promoted (automated test).
- `evolve_now` provably never touches the statistical attribution path — only the LLM-fallback path (automated test).
- A failed task's lesson is retrievable on a matching future turn (integration test).

**Counter-metric:** weekly trait-drift rate must not increase versus the pre-existing baseline — these safety additions must not make evolution more aggressive as a side effect.

## Constraints

- Single-user, single-operator platform — no external-user or compliance surface.
- No new third-party dependencies.
- The positive-only-learning rule is not renegotiable within this PRD's scope.

## Epics

- **Epic 1 — Foundation & Safety.** Feature 2, Feature 1, Feature 3, Feature 4. A complete, safe system on its own even if Epic 2 never ships.
- **Epic 2 — Activation & Polish.** Feature 5, Feature 6, Feature 7. Feature 5 is explicitly gated behind Epic 1's Feature 4.

## Open Questions

None outstanding — the two decisions flagged during Discovery (failure-learning scope; evolve_now/shadow-gate build sequencing) were resolved and are logged in `.memlog.md`.
