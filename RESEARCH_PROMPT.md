# StackOwl — "Jarvis" Root-Cause & Architecture Research (Ralph loop, READ-ONLY)

## Mission
You are performing a **read-only root-cause and architecture study** of StackOwl. The symptom
hunt is already done: `.ralph/FINDINGS.md` holds 88 evidence-backed findings (S1–S4) plus a set of
deferred/partial items. Your job is NOT to patch symptoms. It is to find the **small set of deep
architectural root causes that GENERATE those 88 findings**, and for each, design **one
architectural solution** (an ADR) precise enough that a future implementation session could build
it. You make **zero** code changes — evidence-backed root causes and concrete, buildable designs only.

Governing principle (StackOwl's own): **verification > representation**, and **fix the core, never
patch one symptom**. A good root cause explains many findings at once; a good solution makes a
whole *class* of findings impossible, not merely fixed.

## Production context (read this before designing anything)
- **Production runs on a powerful machine.** Latency, CPU, and memory cost are **NOT** gating
  constraints. When choosing between a cheaper-but-shallower design and a heavier-but-correct one,
  **choose correctness.** The most complete, most-verified architecture wins.
- **Therefore, re-open every deferral that was justified by cost/latency** and re-evaluate it on
  correctness alone — e.g. a per-call learned-heuristic consult on the live path, LLM-derived
  acceptance enabled by default, live semantic (ANN) recall instead of recency-only, an inline
  plan/decompose stage, deeper per-action verification. "It was removed for hot-path latency" is no
  longer a sufficient reason to keep it out; argue it on merit.
- Designs are still **flag-gated for safe rollout**, but in this environment a flag may **default
  ON** in production once verified. "Off = byte-identical" is the migration mechanism, not the
  end state.

## Absolute rules (do not violate)
1. **READ-ONLY.** Never edit, create, or delete any source file. The *only* files you may write are
   the four state files and the ADR documents under `.ralph/adr/`.
2. **One ROOT-CAUSE THEME per iteration.** Diagnose exactly one theme per run. Do not batch.
3. **Evidence required.** Every root cause must (a) name ≥3 findings (by F-number) it generates and
   (b) point at ≥1 `file:line` for the current generating mechanism. Every claim about how the code
   behaves today must be backed by an excerpt you actually read. No evidence → no claim.
4. **Trust disk, not memory.** Re-read the state files and the relevant slice of
   `.ralph/FINDINGS.md` from disk at the start of *every* iteration.
5. **No premature completion.** Done = every theme in `RESEARCH_PLAN.md` is checked off AND each has
   both a `ROOT_CAUSES.md` entry and a complete ADR.
6. **NOTHING IS REMOVED.** Every solution is **additive or unifying** — it must preserve all
   existing capabilities, code paths, and learned data. To "unify the disjoint proxies" means make
   them **delegate to one authority** (centralize the logic, keep every guarantee), NOT delete them.
   Deprecate-in-place behind the new authority; never drop a capability or a learned-data column to
   make a design cleaner. If a signal is currently unused, the answer is **consume it**, not remove it.
7. **Honor the hard product directives** (read them from `CLAUDE.md` and the memory files it
   references). A design that violates one is invalid by construction. At minimum:
   - **Positive-only learning** — never store "this failed / I can't" memories. ("Learn from
     failures" is not an available solution; the question is "how do we avoid repeating failure
     WITHOUT persisting negatives?" — e.g. within-turn awareness, verified-success-only mining.)
   - **No vendor-specific logic** outside thin adapters; **no hardcoded keyword/language lists.**
   - **Verification > representation** — prefer measuring reality over trusting a self-report.

## State files (your memory lives here, in the repo)
- `.ralph/RESEARCH_PLAN.md` — the theme map: clusters of findings → candidate root causes. `[ ]`/`[x]`.
- `.ralph/ROOT_CAUSES.md` — the causal ledger, one entry per theme.
- `.ralph/adr/ADR-<n>-<slug>.md` — one Architectural Decision Record per theme.
- `.ralph/research_progress.txt` — a 5-line handoff note for the next iteration.

## Iteration procedure
1. **Bootstrap (idempotent, every run).** Ensure `.ralph/` and `.ralph/adr/` exist and the state
   files exist. Create any missing file with only its header: `RESEARCH_PLAN.md` → `# Research Plan`;
   `ROOT_CAUSES.md` → `# Root Causes`; `research_progress.txt` → `Iteration 0: not started.`
   (This is the one exception to read-only.)
2. Read `RESEARCH_PLAN.md`, `research_progress.txt`, and the last ADR written.
3. **If `RESEARCH_PLAN.md` has no theme map yet** (first real iteration):
   - Read all of `.ralph/FINDINGS.md` and `.ralph/BACKLOG.md` (and any deferred-items list).
   - **Cluster** every finding into **6–10 candidate root-cause themes** — a theme is a single
     missing/leaky abstraction that several findings are all instances of. Use the candidate themes
     below as starting hypotheses; merge, split, rename, or replace based on evidence. Every finding
     maps to exactly one theme (note cross-cutting ones).
   - Write each as `[ ] <theme name> — generates F-a, F-b, F-c, …` plus a one-line hypothesis.
   - Order themes by **leverage** (findings explained × depth), deepest first.
   - Commit (skip if not a git repo). **End the turn without the completion sigil.**
4. **Else:** select the **first unchecked theme**. That is your one theme for this iteration.
5. Diagnose it against the **Root-Cause Method** below → append a `ROOT_CAUSES.md` entry.
6. Design its **Architectural Solution** against the **ADR Format** below → write the ADR file.
7. Mark the theme `[x]`. Write a fresh 5-line `research_progress.txt` (theme done, the root cause in
   one sentence, the chosen architectural move, anything the next iteration should know).
8. `git add -A && git commit -m "research: <theme>"` (skip if not a git repo).
9. **If every theme is `[x]` and each has a ROOT_CAUSES entry + an ADR:** emit
   `<promise>RESEARCH_COMPLETE</promise>`. Otherwise end the turn without the sigil.

## Root-Cause Method (the diagnostic rubric — apply to the theme under study)
Produce, with evidence:
1. **Symptom set.** The exact findings (F-numbers) that are instances of this theme, and the one
   property they share. If a finding doesn't truly belong, move it.
2. **Causal chain (5-whys).** Walk symptom → local mechanism → the *generating* decision or
   *missing abstraction*. Stop at the architectural root (a structural choice), not a local bug.
3. **Generative power.** State the single missing/leaky abstraction and argue *why* it makes this
   whole class of findings inevitable rather than incidental.
4. **Evidence.** `file:line` + ≤10-line excerpt of the current generating mechanism, and the ≥3
   findings it explains.
5. **Latent blast radius.** What else — not yet a finding — is implied by the same root? Predict
   where the next bug of this class will appear.
6. **Prior art & gap.** What has the codebase already begun (e.g., the B1–B4 verification arc,
   `ToolResult.verified`/`verify_artifact`, `AcceptanceChecker`, the recovery ladder,
   `TurnProgressTracker`, `ConsequentialActionGate`, the delivery seam)? Name what exists and the
   precise gap your ADR must close — by **extending**, not replacing.

## Architectural Solution (ADR) format — the deliverable
Each `ADR-<n>-<slug>.md` must contain:
- **Title & status** (Proposed).
- **Context.** The root cause restated; the directives and existing seams it must honor/reuse.
- **Decision.** The ONE architectural change — the new abstraction, single authority, or seam. Name it.
- **Why this, not the alternatives.** Present ≥2 alternatives and reject them with reasons
  (including "keep N disjoint proxies"). Explain why this is the *core* fix, not another proxy.
  Where a cheaper option was historically chosen for latency, note that the powerful-machine context
  removes that reason and justify the more-correct choice.
- **Shape.** The interface/contract; where it sits in the data flow; **which existing proxies it
  unifies by delegation** (e.g., giveup_floor, overclaim_gate, persistence judge, per-tool
  `verified`, objectives `AcceptanceChecker`) — they route through the new authority, keeping their
  guarantees; **none are deleted**.
- **Invariant established.** The checkable property that makes the whole finding-class *impossible*
  (e.g., "no turn reports success for a declared effect without an observation of that effect").
  This is the heart of the ADR.
- **Migration plan.** Incremental and flag-gated; off = byte-identical for safe rollout, with the
  flag intended to **default ON in production** once verified. Which findings it closes and in what
  order; how it coexists with and **subsumes (not reverts)** the partial fixes already shipped;
  confirm **no capability or learned-data is dropped**.
- **Verification.** How a future session would *prove* it works — the test or live observation. The
  ADR itself must be verifiable, not asserted.
- **Blast radius, risk & rollback.**
- **Effort tier (S/M/L/XL) and dependencies** on other ADRs (which must land first).

## Candidate themes (STARTING HYPOTHESES — validate, merge, split, or replace with evidence)
Seeds, not answers. Some may collapse together; a deeper one may emerge that subsumes several.
- **H1 — Asserted vs measured success.** Success is self-reported by the actor, never observed
  against the intended effect. (F-29/30/31/32/82/83, F-11/12/13/14/15, F-20/23/25/33/34, F-75…)
  B1–B4 began this at the tool boundary — find the residual gaps (non-file effects, normal-turn
  acceptance, fail-open judges). On a powerful machine, default-on verification is on the table.
- **H2 — No single verification/acceptance authority.** "Did it work?" is decided by ≥6 disjoint
  proxies with provable gaps. Unify them under one authority **by delegation**.
- **H3 — Registered ≠ reachable.** Capabilities built but never wired onto the live path
  (F-45/76/77/86, F-50/87, F-78). What structural lack lets a half-edge ship green?
- **H4 — Give-up vs persist (no unified recovery ladder).** Single-attempt surrender; retry /
  fallback / substitution / replan exist as scattered point-solutions
  (F-16/17/40/41/64/60/35/67, F-5/6/7/8/55). Is there one missing "recovery actuator" authority?
- **H5 — Ask-first vs act-first (no reversibility-aware resolver).** Trivial reversible decisions
  bounce to the user (F-3/27/44/68/69/70/71/56). Missing: one reversibility/stakes signal + a
  default-resolution authority.
- **H6 — Learning on an untrustworthy signal under a positive-only constraint.** The learning loop
  is a coping policy on an unmeasured success signal; the directive forbids negative memory
  (F-46/47/48/51/54/26/43/72). Design how the agent *avoids repeating failure* without persisting
  negatives — and how a now-trustworthy (measured) success signal changes what may be mined.
- **H7 — Detect-only lifecycle (no closed-loop heal).** Boot/health/supervisor/restart detect and
  report but don't drive recovery toward a goal (F-36/37/73/74/85/88, F-39). Missing: a closed
  detect→heal→verify loop.
- **H8 — Explainability/trace gaps.** Routing, heuristic influence, recovery, acceptance decisions
  aren't traceable (F-9/10/19/28/39/47/72). Decide if this is its own theme or a symptom of H1/H2.

## Where to look (StackOwl-specific)
- `.ralph/FINDINGS.md` + `.ralph/BACKLOG.md` — the symptom corpus (your input).
- The verification arc: `ToolResult.verified`, `verify_artifact`, `pipeline/acceptance*.py`, the
  recovery ladder in `pipeline/steps/execute.py`, `objectives/driver.py`.
- The disjoint honesty proxies: `giveup_floor.py`, `overclaim_gate.py`, `persistence.py` (delivery
  judge), `supervisor/`, the pipeline progress tracker.
- Directive sources: `CLAUDE.md` and the memory files it references (positive-only learning;
  deepest-root "no verification primitive"; fix-core-not-patch).
- Wiring seams: `notifications/` deliverer, `scheduler/assembly.py`,
  `health/reachability/census.py` — for the registered≠reachable theme.

## Quality bar (what separates a real ADR from a restated finding)
- A root cause that explains **one** finding is a bug report. Aim for themes that explain **many**.
- A solution that adds **another** proxy/flag/checker is a patch. A solution that introduces **one
  authority/invariant the proxies delegate to** is architecture. Prefer the latter; justify it.
- Every design **reuses and unifies** existing seams rather than reinventing or **removing** them —
  the codebase is full of half-built machinery; wiring/unifying beats rebuilding, and **nothing is
  deleted**.
- State the **invariant**, not just the mechanism: "this class of bug can no longer be written."
- Given the powerful production target, **bias toward the most-verified, most-complete design**;
  do not trade correctness for cost, and explicitly reconsider any prior latency-motivated cut.

## Completion
Emit `<promise>RESEARCH_COMPLETE</promise>` **only** when `RESEARCH_PLAN.md` has zero unchecked
themes and every theme has both a `ROOT_CAUSES.md` entry and a complete `ADR-<n>-*.md`. Until then,
end each turn without the sigil so the loop continues.
