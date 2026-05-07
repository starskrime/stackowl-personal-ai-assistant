# Element 14 — Evolution Engine Design Spec

**Date:** 2026-05-06  
**Status:** Draft — awaiting Boss approval  
**Scope:** Inference-time only. No fine-tuning. No new stealth backends.  
**Net file delta:** −7 (808 LOC deleted, 0 new files)

---

## Background and Scope

Element 14 fixes a three-part gap in StackOwl's evolution subsystem:

1. **Dead code cluster** (`src/evolution/`): 7 files, 808 LOC, never called from outside their own directory. Some contain hardcoded keyword arrays that violate the project's no-hardcoded-keywords rule. All 7 are deleted.

2. **Mid-session evolution gap**: `evolve()` fires only at session end (`core.ts:2494–2496`). With 2-hour sessions, an owl's behavior never adjusts in response to a failing streak within a session. A mid-session trigger is added to `PostProcessor.process()`.

3. **Signal digest gap**: `evolve()` reads `userProfileProvider()` and episodic memories but ignores the `owl_learnings` table — the highest-signal behaviorally-grounded lessons already written by PostProcessor's failure-critique job. Injecting the top-5 learnings into the evolution LLM prompt closes this gap.

4. **Decay rate**: Default `decayRatePerWeek = 0.01` is 5-10x too slow (per market research, Mary's Section 5). Corrected to `0.1`. EMA smoothing (β=0.7) added to prevent trait oscillation.

5. **Old `ReflexionEngine` not wired**: `src/evolution/reflexion.ts`'s `reflectOnFailure()` is referenced in `post-processor.ts:615` via `ctx.reflexionEngine`, but `ctx.reflexionEngine` is never set in `core.ts`. Three-line constructor fix.

6. **`outcome_journal` table**: `evolve()` previously queried `outcome_journal` which may not exist in all installations. The already-verified `trajectories` table (used in current code) is the correct query source.

### What is NOT in scope

- No new files in `src/`
- No model fine-tuning
- No changes to the `IntelligenceReflexionEngine` loop (already closed — `src/intelligence/reflexion-engine.ts` + `CritiqueRetriever`)
- No changes to `promptSections` injection (already done at `runtime.ts:2472`)
- No changes to `learnedPreferences` injection (already done at `runtime.ts:2446`)
- No changes to `SelfLearningCoordinator` wiring (already done in `post-processor.ts:324`)
- No changes to `InnerLifeDNABridge` wiring (already done in `post-processor.ts:279–283`)

---

## Locked Architectural Decisions

| ID | Decision |
|----|----------|
| **D1** | Delete `src/evolution/mutation-engine.ts`, `batch-manager.ts`, `outcome-recorder.ts`, `trend-analyzer.ts`, `optimize.ts`, `index.ts`, `types.ts`. Keep `inner-bridge.ts` (wired) and `reflexion.ts` (idle dreams). Net: −7 files, −808 LOC. |
| **D2** | Wire `ctx.reflexionEngine` in `core.ts`: construct `new ReflexionEngine(ctx.sessionStore)` and assign to `ctx.reflexionEngine` at the same site where other engines are built. ~3 lines. |
| **D3** | No change — `promptSections` already injected at `runtime.ts:2472`. G11 was incorrect. |
| **D4** | Mid-session evolution trigger in `PostProcessor.process()`: when rolling avg trajectory reward < −0.2 AND last evolution was > 2 h ago, enqueue background job calling `ctx.evolutionEngine.evolve(owlName)`. Rate-limited per owl. No new class. |
| **D5** | Extend `evolve()` in `evolution.ts`: read `db.owlLearnings.getForOwlSorted(owlName)` (top 5 learnings sorted by failure-first, then confidence) and inject as `RECENT LEARNINGS` section in the LLM prompt, before the transcript. Inline in `evolve()`. |
| **D6** | Change default `decayRatePerWeek` from `0.01` to `0.1` in `evolution.ts:60`. Add EMA blending (β=0.7) for numeric trait updates in `evolve()`: `newVal = 0.7 * proposed + 0.3 * current` for `learnedPreferences` and `expertiseGrowth` keys. Inline in `evolve()`. |

---

## Design

### D1 — Delete dead code cluster

Seven files in `src/evolution/` are unreachable from any production call path. They have no imports from outside the cluster.

**Files deleted:**
- `src/evolution/mutation-engine.ts` (273 LOC) — `parseRecommendation()` uses hardcoded string matching, violating the no-hardcoded-keywords rule. No callers outside cluster.
- `src/evolution/batch-manager.ts` (92 LOC) — never wired to coordinator or PostProcessor.
- `src/evolution/outcome-recorder.ts` (74 LOC) — queries `outcome_journal` which may not exist. Superseded by `db.trajectories`.
- `src/evolution/trend-analyzer.ts` (118 LOC) — no callers outside cluster.
- `src/evolution/optimize.ts` (89 LOC) — APO draft. Never called. `db.promptOptimizations` table exists but nothing feeds it.
- `src/evolution/index.ts` (41 LOC) — re-exports dead cluster, imports would fail if cluster gone.
- `src/evolution/types.ts` (121 LOC) — types for the dead cluster only.

**Files kept:**
- `src/evolution/reflexion.ts` — `dream()` fires via idle schedule, writes behavioral patches to pellets. Not dead; serves idle-time introspection. Kept.
- `src/owls/inner-bridge.ts` — `sync()` called every 5 messages from `PostProcessor` (lines 279–283). Wired and active. Kept.

**How to delete safely:** Run `grep -r "from.*evolution/mutation-engine\|from.*evolution/batch\|from.*evolution/outcome\|from.*evolution/trend\|from.*evolution/optimize\|from.*evolution/index\|from.*evolution/types"` across `src/` to confirm zero callers, then `rm` the 7 files. TypeScript build confirms no broken imports.

---

### D2 — Wire old ReflexionEngine in core.ts

`src/evolution/reflexion.ts` exports `ReflexionEngine` with a `reflectOnFailure()` method. `post-processor.ts:601–628` calls `ctx.reflexionEngine.reflectOnFailure(...)` — but `ctx.reflexionEngine` is never populated in `core.ts`, so the `if (this.ctx.reflexionEngine && ...)` guard always falls through.

**Fix in `src/gateway/core.ts`:**

Find the block where other engines are constructed (near the `PostProcessor` construction site). Add:

```typescript
import { ReflexionEngine } from "../evolution/reflexion.js";

// In the engine constructor or init block:
if (!this.ctx.reflexionEngine && this.ctx.sessionStore) {
  this.ctx.reflexionEngine = new ReflexionEngine(this.ctx.sessionStore);
}
```

This is a 3-line change. `ReflexionEngine`'s constructor takes `sessionStore` — already present on `ctx`.

**Effect:** On loop exhaustion or ≥3 tool failures, `reflectOnFailure()` now fires. It writes a behavioral patch to a pellet, which `reflexion.ts`'s `dream()` can consolidate during idle time.

---

### D4 — Mid-session evolution trigger

Currently, `ctx.evolutionEngine.evolve()` runs only at `endSession()` (`core.ts:2494–2496`). This means a session with persistent failures never adapts until it ends.

**Trigger condition (in `PostProcessor.process()`):**

After the existing reflexion jobs, add a rate-limited check:

```typescript
// Mid-session evolution: fire when recent trajectories show sustained failure
if (this.ctx.evolutionEngine && this.ctx.db && this.messageCount % 5 === 0) {
  const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
  const recent = this.ctx.db.trajectories.getRecent(owlName, 10);
  if (recent.length >= 5) {
    const avgReward = recent.reduce((s, t) => s + t.reward, 0) / recent.length;
    const lastEvolved = this.ctx.owl.dna.lastEvolved
      ? new Date(this.ctx.owl.dna.lastEvolved).getTime()
      : 0;
    const hoursSinceEvolved = (Date.now() - lastEvolved) / (1000 * 60 * 60);

    if (avgReward < -0.2 && hoursSinceEvolved > 2) {
      this.enqueueJob("mid-session-evolution", "background", async () => {
        await this.ctx.evolutionEngine!.evolve(owlName);
        log.engine.info(`[PostProcessor:mid-session-evolution] avg_reward=${avgReward.toFixed(2)} triggered evolution for ${owlName}`);
      });
    }
  }
}
```

**Rate limit:** `hoursSinceEvolved > 2` prevents re-triggering within the same failure window. The `this.messageCount % 5 === 0` gate means the check runs every 5 messages, not every message.

**No new class.** All logic is inline in `PostProcessor.process()`.

---

### D5 — Signal digest in evolve()

`evolve()` in `src/owls/evolution.ts` already builds `profileSection`, `memorySection`, `performanceSection`, and `trajectorySection`. It reads `userProfileProvider()` and `db.owlPerf`, but not `db.owlLearnings` — the highest-quality signal (failure-grounded, confidence-ranked lessons generated by `PostProcessor`'s critique job).

**Addition in `evolve()`, after the `performanceSection` block (around line 262):**

```typescript
// ── Signal digest: inject top-ranked owl learnings ─────────────
let learningsSection = "";
if (this.db) {
  try {
    const learnings = this.db.owlLearnings.getForOwlSorted(owlName);
    if (learnings.length > 0) {
      learningsSection =
        `\nRECENT LEARNINGS (failure-first, ranked by confidence):\n` +
        learnings.slice(0, 5).map((l, i) => `${i + 1}. ${l}`).join("\n") +
        `\nApply these learnings when proposing trait mutations.\n\n`;
    }
  } catch {
    // Non-fatal — learnings may not exist yet
  }
}
```

Then add `learningsSection` to the prompt string alongside `profileSection`, `performanceSection`, etc.

`getForOwlSorted()` is already implemented at `db.ts:2221–2232`. It returns strings sorted failure-first, then by confidence desc, then reinforcement count desc, `LIMIT 6`. We take `slice(0, 5)` for the prompt.

---

### D6 — Decay rate correction + EMA smoothing

**Decay rate:** `evolution.ts:60` reads `this.config.owlDna?.decayRatePerWeek ?? 0.01`. Change the fallback default to `0.1`. This is a one-character change in the fallback value.

**EMA smoothing:** When `evolve()` applies numeric trait mutations to `learnedPreferences` and `expertiseGrowth`, apply EMA blending to prevent oscillation. After the LLM returns proposed DNA values, blend them:

```typescript
// Before writing new value to owl.dna.learnedPreferences[key]:
const proposed = parsedDna.learnedPreferences[key]; // value from LLM
const current = owl.dna.learnedPreferences[key] ?? 0.5;
owl.dna.learnedPreferences[key] = 0.7 * proposed + 0.3 * current;

// Same pattern for expertiseGrowth
```

The β=0.7 value matches PAMU (arXiv:2510.09720) which showed 37% reduction in oscillation on the same trait-mutation task. It weights recent signal at 70% while retaining 30% of prior stable value.

**Where in evolve():** The LLM returns a JSON blob of proposed DNA mutations. The current code parses it and writes values directly. The EMA blending is a one-line wrapper around each numeric assignment. Non-numeric fields (strings, arrays like `promptSections`) are written as-is.

---

### G5 — outcome_journal → trajectories

Verified: `evolution.ts` already uses `this.db.trajectories.getRecent(owlName, 50)` at line 271. The `outcome_journal` query mentioned in the audit was in the dead `outcome-recorder.ts` (deleted under D1). No additional fix required beyond D1 deletion.

---

## File Change Summary

| File | Change | Lines |
|------|--------|-------|
| `src/evolution/mutation-engine.ts` | **Delete** | −273 |
| `src/evolution/batch-manager.ts` | **Delete** | −92 |
| `src/evolution/outcome-recorder.ts` | **Delete** | −74 |
| `src/evolution/trend-analyzer.ts` | **Delete** | −118 |
| `src/evolution/optimize.ts` | **Delete** | −89 |
| `src/evolution/index.ts` | **Delete** | −41 |
| `src/evolution/types.ts` | **Delete** | −121 |
| `src/gateway/core.ts` | Wire `ctx.reflexionEngine` (D2) | +3 |
| `src/gateway/handlers/post-processor.ts` | Mid-session trigger (D4) | +18 |
| `src/owls/evolution.ts` | Signal digest (D5) + EMA (D6) + decay rate (D6) | +18 |

**Net: −789 LOC, 0 new files, 3 modified files.**

---

## Test Coverage

Each change has a failing-first test before implementation:

- **D1**: Confirm `mutation-engine.ts` et al. have no callers (static grep test); TypeScript build passes after deletion.
- **D2**: Unit test — construct `PostProcessor` with null `ctx.reflexionEngine`; after D2, `ctx.reflexionEngine` is non-null and `reflectOnFailure()` is callable.
- **D4**: Unit test — inject mock `ctx.evolutionEngine.evolve` spy; simulate 5 consecutive low-reward trajectories (reward = −0.5) with `lastEvolved` > 2h ago; assert `evolve()` is called once. Verify it is NOT called when avg reward = 0.1 (above threshold).
- **D5**: Unit test — seed `owl_learnings` with 6 rows for `owlName`; call `evolve()`; assert the LLM prompt includes "RECENT LEARNINGS" with top-5 entries ordered failure-first.
- **D6 decay**: Unit test — `applyDecayIfNeeded` with `decayRatePerWeek` unset (default); after 14 days, assert trait moves toward 0.5 faster than with old 0.01 rate.
- **D6 EMA**: Unit test — mock LLM returning proposed `verbosity = 1.0`; existing `verbosity = 0.4`; after `evolve()`, assert stored value ≈ 0.82 (0.7×1.0 + 0.3×0.4).

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Mid-session evolution fires too often | `messageCount % 5` gate + 2h cooldown |
| EMA smoothing too aggressive | β=0.7 is conservative — 30% weight on prior value. Research-backed (PAMU). |
| `getForOwlSorted` returns stale learnings | `getForOwlSorted` reads live DB — always current. Limit 6 rows caps prompt growth. |
| ReflexionEngine patches generate noisy pellets | `dream()` already de-duplicates via pellet similarity check. Unchanged. |
| Deleting `evolution/types.ts` breaks imports | Verify with grep before deletion. D1 test step covers this. |
| Decay rate change breaks existing owl DNA | New rate only affects `applyDecayIfNeeded()` calls going forward; existing stored values are unaffected. |

---

## Composition — No New Primitives

All decisions compose existing infrastructure:

- `db.trajectories.getRecent()` — already used in `evolve()` for the trajectory section.
- `db.owlLearnings.getForOwlSorted()` — already implemented in `db.ts:2221`.
- `PostProcessor.enqueueJob()` — already the pattern for all background work.
- `ctx.evolutionEngine.evolve()` — already called at session end; D4 adds a mid-session call.
- `ReflexionEngine` (`src/evolution/reflexion.ts`) — already exists; D2 just constructs and wires it.
