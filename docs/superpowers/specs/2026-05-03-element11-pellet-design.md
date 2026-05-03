# Element 11 — Pellet System: Quality Flywheel, Compounding Knowledge

**Date:** 2026-05-03  
**Status:** Approved for implementation  
**References:**
- Market research: `_bmad-output/planning-artifacts/research/market-pellet-knowledge-system-research-2026-05-03.md`
- Architecture audit: Phase 2 of E11 brainstorm session

---

## Goal

Fix the Pellet system's three quality failures in one targeted intervention:

1. **Intelligence-First violations** — keyword array in `event-based-generator.ts`, raw `provider.chat()` in `dedup.ts` and `knowledge-base.ts`, `OwlEngine` in `generator.ts`
2. **No retrieval feedback loop** — pellets retrieved into context are never distinguished from pellets that were ignored; knowledge base grows but doesn't get smarter
3. **Lost scheduler state** — `ProactiveGenerator` tracks last-run times in-memory; state lost on restart causing re-runs or skips

This is NOT a rewrite of the pellet system. No new files. Three deleted files. Schema extensions on existing tables/stores.

---

## Architecture

### Compounding Flywheel

```
User turn
  ↓
RelevantPelletsLayer.build()
  → pelletStore.searchWithGraph(query, 5)     ← NEW: graph-expanded search
  → quality re-rank by successCount/failureCount  ← NEW
  → req.retrievedPelletIds = [...]             ← NEW: side-channel write
  ↓
ContextPipeline.run() returns { output, trace, retrievedPelletIds }  ← NEW
  ↓
GoalVerifier.verify() → verdict
  ↓
gateway/core.ts post-turn block:
  Hook 4: pelletStore.recordOutcome(retrievedPelletIds, verdict)    ← NEW
  Hook 5 (ADVANCES only):
    get owls who generated those pellets
    → updatePelletGeneratorDNA(owlNames, topicCategory, db)         ← NEW
```

Every time a retrieved pellet advances the user's goal, that pellet's `successCount` increments and the owls who generated it get their topic `expertiseGrowth` nudged up. Over time: high-quality owls surface first, their pellets rank higher in retrieval, and the knowledge base self-improves.

### Quality Re-Rank Formula

```
score * 0.8 + (successCount / (successCount + failureCount + 1)) * 0.2
```

Unscored pellets (successCount=0, failureCount=0) contribute `0.5 * 0.2 = 0.1` — they receive neutral treatment until evidence accumulates. Learning rate is asymptotic: a pellet with 10 successes and 1 failure scores `0.8s + 0.17` rather than `0.8s + 0.2`, keeping it grounded in retrieval signal.

---

## Components

### Modified: `src/pellets/store.ts`

**Current state:** 411 lines. Pellet interface lacks outcome fields. No feedback API.

**Changes:**
- Add to `Pellet` interface: `successCount: number`, `failureCount: number`, `provenance: string[]`
- Add method: `recordOutcome(ids: string[], verdict: "ADVANCES"|"PARTIAL"|"BLOCKED"|"NEUTRAL"): Promise<void>`
  - ADVANCES/PARTIAL → successCount++
  - BLOCKED → failureCount++
  - NEUTRAL → no-op
  - Each ID update is non-fatal (individual try/catch per ID)
- Add method: `get(id: string): Promise<Pellet | null>`

### Modified: `src/pellets/generator.ts`

**Current state:** 108 lines. Constructor takes `OwlEngine` — runs full ReAct loop for a 150-token LLM task. Includes `console.log`.

**Changes (full rewrite of internals, same public API):**
- Constructor: `constructor(private router: IntelligenceRouter)`
- `generate()` calls `router.resolve("generation", prompt)` — no ReAct overhead
- Replace all `console.log` with `log.engine`
- Guard: empty conversation → return null early

### Modified: `src/pellets/dedup.ts`

**Current state:** 472 lines. `decideWithLlm()` calls raw `this.provider.chat()`.

**Changes:**
- Add optional `router?: IntelligenceRouter` to constructor
- `decideWithLlm()`: use `router.resolve("classification")` when router present, fall back to `provider.chat()` when absent (backward-compatible)

### Modified: `src/pellets/knowledge-base.ts`

**Current state:** 206 lines. `computeCoverageGaps()` has hardcoded `["technical", "personal", "goals", ...]` array.

**Changes:**
- Add optional `router?: IntelligenceRouter` to constructor
- `computeCoverageGaps()`: use `router.resolve("classification")` when router present
- Keep hardcoded array as static fallback only (when router absent)

### Modified: `src/pellets/event-based-generator.ts`

**Current state:** 291 lines. Uses `content.includes("decision") || content.includes("decided")...` keyword detection. Imports from old `EventBus`.

**Changes:**
- Add `router: IntelligenceRouter` to constructor
- Replace keyword array with `router.resolve("classification", {content})` returning `{ isDecision: boolean, isInsight: boolean, isCorrection: boolean }`
- Migrate from old `EventBus` to `GatewayEventBus`

### Modified: `src/pellets/proactive-generator.ts`

**Current state:** 268 lines. `lastCouncilRun`, `lastDreamRun`, `lastEvolveRun` are in-memory fields, lost on restart.

**Changes:**
- Add `db: MemoryDatabase` to constructor
- Replace all in-memory run-time fields with `db.getPelletGenRun(key)` / `db.setPelletGenRun(key, isoDate)`
- Keys: `"council"`, `"dream"`, `"evolve"`

### Modified: `src/context/layers/knowledge.ts`

**Current state:** 49 lines. Calls `pelletStore.search(query, 3)`. Returns 300-char truncated content. Does not write pellet IDs to request.

**Changes:**
- `build()` calls `pelletStore.searchWithGraph(query, 5)` (graph-expanded, up to 5 results)
- Quality re-rank: `score * 0.8 + (successCount/(successCount+failureCount+1)) * 0.2`
- Sort descending, `slice(0, 5)`
- Content truncation: 500 chars (from 300)
- Write: `req.retrievedPelletIds = scored.map(s => s.p.id)`

### Modified: `src/context/layer.ts`

**Changes:**
- Add `retrievedPelletIds?: string[]` to `ContextRequest` interface

### Modified: `src/context/pipeline.ts`

**Changes:**
- `run()` return type: add `retrievedPelletIds: string[]`
- At end of `run()`: `return { output, trace, retrievedPelletIds: request.retrievedPelletIds ?? [] }`

### Modified: `src/gateway/core.ts`

**Changes — post-turn block gains hooks 4 and 5:**

```typescript
// Hook 4: feed GoalVerifier verdict back to retrieved pellets
if (retrievedPelletIds.length > 0 && goalVerdict !== "NEUTRAL") {
  await this.ctx.pelletStore
    .recordOutcome(retrievedPelletIds, goalVerdict)
    .catch((err) => log.engine.warn("[gateway] recordOutcome failed", err));
}

// Hook 5: reinforce DNA of owls who generated helpful pellets
if (goalVerdict === "ADVANCES" && retrievedPelletIds.length > 0 && this.ctx.db) {
  const retrievedPellets = await Promise.all(
    retrievedPelletIds.map((id) => this.ctx.pelletStore!.get(id))
  ).catch(() => []);
  const generatorOwlNames = [
    ...new Set(
      retrievedPellets.flatMap((p) => p?.owls ?? []).filter(Boolean)
    ),
  ];
  // topicCategory: use first tag from the highest-ranked retrieved pellet,
  // fall back to "general" if no tags. Avoids a classifier call in the hot path.
  const topicCategory =
    retrievedPellets[0]?.tags?.[0] ?? "general";
  if (generatorOwlNames.length > 0) {
    await updatePelletGeneratorDNA(
      generatorOwlNames,
      topicCategory,
      this.ctx.db,
    ).catch((err) => log.engine.warn("[gateway] updatePelletGeneratorDNA failed", err));
  }
}
```

Both hooks are non-fatal (catch + log). No change to primary response path.

### Modified: `src/owls/evolution.ts`

**New export:**

```typescript
export async function updatePelletGeneratorDNA(
  owlNames: string[],
  topicCategory: string,
  db: MemoryDatabase,
): Promise<void> {
  const LEARNING_RATE = 0.03; // smaller than Parliament's 0.05 — pellet signal is indirect
  for (const name of owlNames) {
    try {
      const owl = await db.owls.getByName(name);
      if (!owl) continue;
      owl.dna.expertiseGrowth[topicCategory] = clamp(
        (owl.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
        0.1,
        0.9,
      );
      await db.owls.updateDNA(name, owl.dna);
    } catch (err) {
      log.engine.warn(`[evolution] pelletGeneratorDNA update failed for ${name}`, err);
    }
  }
}
```

Follows exact same pattern as `updateClarificationAutonomy()` and `updateParliamentDNA()`. Non-fatal per owl.

### Modified: `src/memory/db.ts`

**Schema v21:**

```sql
CREATE TABLE IF NOT EXISTS pellet_generation_runs (
  key      TEXT PRIMARY KEY,
  last_run_at TEXT NOT NULL
);
```

`applyV21Migration()` — guarded by `SELECT name FROM sqlite_master WHERE type='table' AND name='pellet_generation_runs'`.

**New helpers:**
```typescript
getPelletGenRun(key: string): Promise<Date | null>
setPelletGenRun(key: string, date: Date): Promise<void>
```

### Modified: `src/pellets/lance-store.ts`

**In `init()`:**

```typescript
const existing = table.schema.fields.map((f) => f.name);
const toAdd: Record<string, number | string[]> = {};
if (!existing.includes("successCount")) toAdd["successCount"] = 0;
if (!existing.includes("failureCount")) toAdd["failureCount"] = 0;
if (!existing.includes("provenance"))   toAdd["provenance"]   = [];
if (Object.keys(toAdd).length > 0) {
  await table.addColumns(
    Object.entries(toAdd).map(([name, defaultValue]) => ({ name, defaultValue }))
  );
}
```

Idempotent — safe to run on existing databases.

---

## Integration Contracts

### ContextRequest side-channel

`RelevantPelletsLayer.build()` writes to the mutable `ContextRequest` object:

```typescript
req.retrievedPelletIds = scoredResults.map((s) => s.p.id);
```

`ContextPipeline.run()` reads this at the end and includes it in the return value:

```typescript
return { output, trace, retrievedPelletIds: request.retrievedPelletIds ?? [] };
```

The gateway reads `retrievedPelletIds` from the pipeline result and uses it in hooks 4 and 5. This is a one-way side-channel — no other layer writes to `retrievedPelletIds`.

### DNA update ordering

Hooks 4 and 5 both run in the same post-turn block as the existing GoalVerifier call. Ordering: GoalVerifier → Hook 4 (recordOutcome) → Hook 5 (updatePelletGeneratorDNA). All three are non-fatal and run sequentially in the catch-wrapped block.

### topicCategory derivation (Hook 5)

`topicCategory` for `updatePelletGeneratorDNA` is derived from the first tag of the highest-ranked retrieved pellet (`retrievedPellets[0]?.tags?.[0] ?? "general"`). This avoids an extra classifier call in the hot path. The imprecision is acceptable: `expertiseGrowth` is a soft signal and the asymptotic clamp prevents any single category from dominating.

### searchWithGraph contract

`PelletStore.searchWithGraph(query: string, topK: number)` — new method on store:
- Vector search: top 5, cosine distance < 0.55
- Kuzu 1-hop expansion: for each seed pellet, traverse `RELATED_TO` edges, add neighbors not already in seed set
- Return type: `Array<{ p: Pellet; score: number }>` — score from vector step, not re-calculated after graph expansion

---

## Data Model

### Pellet interface additions

```typescript
interface Pellet {
  // ... existing fields ...
  successCount: number;   // incremented on ADVANCES or PARTIAL
  failureCount: number;   // incremented on BLOCKED
  provenance: string[];   // source trace: ["parliament", "session-abc", "turn-3"]
}
```

`provenance` is written at generation time by `PelletGenerator` — Parliament sessions include `["parliament", sessionId]`, event-based pellets include `["event", eventType, turnIndex]`.

### LanceDB Arrow schema (v21 equivalent)

Columns added via `addColumns()` to existing table — no new LanceDB table, no migration script beyond `init()` guard:

| Column | Type | Default |
|---|---|---|
| `successCount` | `int32` | `0` |
| `failureCount` | `int32` | `0` |
| `provenance` | `list<utf8>` | `[]` |

### SQLite schema v21

```sql
CREATE TABLE IF NOT EXISTS pellet_generation_runs (
  key         TEXT PRIMARY KEY,
  last_run_at TEXT NOT NULL
);
```

Rows: `"council"`, `"dream"`, `"evolve"` — written by ProactiveGenerator on each run.

---

## Acceptance Criteria

**AC-1: Dead files deleted**
`ls src/pellets/search.ts src/pellets/semantic-dedup.ts src/pellets/tfidf.ts` → all three return "No such file". `npx tsc --noEmit` passes.

**AC-2: No Intelligence-First violations remain**
`grep -r "content\.includes\|provider\.chat\|OwlEngine" src/pellets/` returns zero matches.

**AC-3: PelletGenerator uses IntelligenceRouter**
Unit test: mock `IntelligenceRouter.resolve`. Call `generator.generate(conversation)`. Assert `router.resolve()` was called with a generation prompt. Assert no `console.log` emitted.

**AC-4: EventBasedGenerator uses IntelligenceRouter for classification**
Unit test: mock router returning `{ isDecision: true }`. Assert pellet generated. Mock router returning `{ isDecision: false, isInsight: false, isCorrection: false }`. Assert no pellet generated.

**AC-5: Dedup uses IntelligenceRouter when present**
Unit test: construct `Dedup` with router. Assert `router.resolve("classification")` called in `decideWithLlm()`. Construct without router. Assert `provider.chat()` called instead.

**AC-6: KnowledgeBase uses IntelligenceRouter when present**
Unit test: `computeCoverageGaps()` with router present → calls `router.resolve`. Without router → uses hardcoded array.

**AC-7: RelevantPelletsLayer quality re-rank**
Unit test: 3 pellets — A (score=0.9, success=0, failure=0), B (score=0.7, success=10, failure=0), C (score=0.8, success=0, failure=5). After re-rank, order should be B > A > C.

**AC-8: retrievedPelletIds written to ContextRequest**
Unit test: mock `searchWithGraph` returning 3 pellets. Assert `req.retrievedPelletIds` has 3 IDs after `build()` completes.

**AC-9: ContextPipeline returns retrievedPelletIds**
Unit test: run pipeline with a knowledge layer. Assert `result.retrievedPelletIds` is present and non-empty.

**AC-10: recordOutcome increments correct counter**
Unit test (5 cases):
- ADVANCES → `successCount++`
- PARTIAL → `successCount++`
- BLOCKED → `failureCount++`
- NEUTRAL → no change
- Multi-ID: all IDs updated, one failing ID doesn't abort others

**AC-11: Hook 4 fires in gateway**
Integration test: mock `pelletStore.recordOutcome`. Run gateway turn with retrieved pellet IDs and non-NEUTRAL verdict. Assert `recordOutcome` called with correct IDs and verdict.

**AC-12: Hook 5 fires only on ADVANCES**
Unit test: ADVANCES → `updatePelletGeneratorDNA` called. BLOCKED → not called. PARTIAL → not called.

**AC-13: Schema v21 migration**
Test: fresh DB + migrate → `pellet_generation_runs` table exists. Existing DB + migrate → table added without data loss in other tables.

**AC-14: ProactiveGenerator persists run times**
Unit test: mock `db.getPelletGenRun` / `db.setPelletGenRun`. Run ProactiveGenerator. Assert `setPelletGenRun` called with correct keys. Assert `getPelletGenRun` called on next run (not in-memory field).

**AC-15: LanceDB addColumns is idempotent**
Unit test: call `init()` twice on same table. Assert no error second time (columns already present → no-op).

**AC-16: Existing pellet tests still pass**
`npx vitest run __tests__/pellets/` — zero regressions.

---

## What We Are NOT Doing

| Not doing | Why |
|---|---|
| Per-message LLM extraction | MemMachine: 97.8% junk rate. Parliament sessions are quality gates. |
| Replacing LanceDB with Zep/Weaviate | No temporal query use cases. LanceDB + Kuzu is sufficient. |
| Semantic dedup on every insert | Real problem is extraction quality, not post-hoc dedup. Dedup stays as operator-run job. |
| Bi-temporal versioning on Pellets | No temporal query use cases yet. `version` chain sufficient. |
| GraphRAG pre-query graph construction | 75% token budget pre-query, 41x slower. Kuzu used only for 1-hop retrieval expansion. |
| Merging KnowledgeCouncil with Parliament | Different triggers, different cadence. Shared PelletStore only. |
| Importance score at write time | SuccessCount/failureCount is more honest — importance revealed by use, not declared at birth. |
| Any new LanceDB table or Kuzu schema change | `addColumns()` on existing table only. |

---

## Test Estimate

| Area | Count | Notes |
|---|---|---|
| `store.ts` — `recordOutcome` | 5 | ADVANCES, PARTIAL, BLOCKED, NEUTRAL, multi-ID non-fatal |
| `generator.ts` rewrite | 4 | Uses router, no console.log, empty conversation guard, idempotent |
| `dedup.ts` — IntelligenceRouter path | 3 | Router path, fallback path, no regression |
| `knowledge-base.ts` — IntelligenceRouter path | 2 | Router path, static fallback |
| `event-based-generator.ts` | 4 | Router classification, GatewayEventBus, decision, non-decision |
| `proactive-generator.ts` | 3 | DB-backed run times, keys correct, no in-memory field |
| `layers/knowledge.ts` | 5 | `searchWithGraph` called, re-rank formula, slice(0,5), 500-char, IDs written |
| Schema v21 migration | 2 | Fresh DB, existing DB |
| `lance-store.ts` addColumns | 2 | Adds columns, idempotent on second init |
| `evolution.ts` — `updatePelletGeneratorDNA` | 4 | ADVANCES fires, clamped, multiple owls, missing owl no-op |
| `gateway/core.ts` hooks 4+5 | 5 | Hook 4 calls recordOutcome, Hook 5 on ADVANCES only, no crash on absent store, BLOCKED skips DNA, empty names skips DNA |
| Dead file deletion regression | 1 | `tsc --noEmit` passes |
| AC-16 regression | 0 new | Existing `__tests__/pellets/` all pass |

**~40 new tests total.**

---

## Constraints Summary

| Constraint | How Met |
|---|---|
| No hardcoded keywords/regex | Keyword array in `event-based-generator.ts` → `router.resolve()` |
| All model selection via IntelligenceRouter | `generator.ts`, `dedup.ts`, `knowledge-base.ts`, `event-based-generator.ts` |
| Passive correlator (no new files) | All changes are additive to existing files |
| Non-fatal post-turn hooks | All hooks wrapped in `.catch(log)` |
| Idempotent schema migrations | `addColumns()` guarded by `schema.fields` check; v21 guarded by `sqlite_master` |
| LEARNING_RATE smaller than Parliament | 0.03 vs Parliament's 0.05 — pellet signal is indirect |
| No new LanceDB tables | `addColumns()` on existing table only |
