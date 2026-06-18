# Element 8 — PostProcessor: Priority Pipeline, Bidirectional Wiring & Telemetry

**Date:** 2026-05-02  
**Status:** Approved — ready for implementation  
**Element:** 8 (PostProcessor — save, learn, evolve, queue)

---

## Problem

`PostProcessor` is a 770-line flat list of `taskQueue.enqueue()` calls with four structural defects:

1. **No priority system.** A slow `dna-evolve` LLM call (2–8 s) can block `digest-update` (50 ms, needed before the next prompt). The existing `TaskQueue` is FIFO — job order determines latency variance.

2. **11 of 23 jobs have no error handling.** Silent failures are indistinguishable from success. You cannot know whether fact extraction, reflexion, or compression is actually working.

3. **4 zombie jobs.** `knowledge-extract`, `timeline-snapshot`, `goal-extraction`, and (partially) `predictive-prep` write to storage but their outputs are never read back into any future prompt. They consume LLM tokens every session and produce zero behavior change.

4. **Two broken wiring paths.**
   - `KnowledgeGraphLayer` reads from `(req.session as any).knowledgeGraphContext` — a cast that is never populated. The real `KnowledgeGraph` class is not in `ContextDependencies`.
   - `PredictiveQueue` has no context layer at all — `predictive-prep` prepares tasks that nothing ever injects into the system prompt.

5. **Three synchronous direct calls with no error handling** — `coordinator.processMessage()`, `patternAnalyzer.recordAction()` / `enrichFromProfile()`, and `sentimentProbe.arm()` / `onNextMessage()` all fire on every message with no try/catch. A crash in any of these terminates the entire `process()` call silently.

**Supporting evidence from 2026 production research:** Mem0's architecture shows a 26% recall improvement over OpenAI built-in memory — but only when extraction is coupled with retrieval into the system prompt. Storage without retrieval is dead weight. Agent.xpu's dual-queue architecture demonstrates that separating real-time (high-priority) from background jobs reduces p95 latency by 40–60% for the jobs that matter.

---

## Design Decisions

### Decision 1 — Three-tier TaskQueue (maps to existing priority vocabulary)

`TaskQueue` already has `TaskPriority = "high" | "normal" | "low"` (`src/queue/task-queue.ts:12`). The three tiers map directly to this existing type — **no renaming, no blast radius**:

| Tier name | Maps to `TaskPriority` | Jobs | Guarantee |
|-----------|------------------------|------|-----------|
| CRITICAL | `"high"` | `digest-update`, `sentiment-challenge-update` | Drains before next LLM call |
| STANDARD | `"normal"` | `fact-extract`, `success-recipe`, `learning-orchestrator`, `learning` (legacy fallback), `reflexion-write`, `quality-reflexion`, `compress`, `gap-feedback`, `sleep-consolidation`, `memory-decay`, `coordinator-save` | Completes within 5 s on p95 |
| BACKGROUND | `"low"` | `dna-evolve`, `inner-life-dna-sync`, `dna-preference-feedback`, `anticipation`, `pattern-save`, `trust-save`, `predictive-prep`, `knowledge-extract` (re-enabled) | Runs opportunistically, never blocks |

The `enqueueJob()` helper (Decision 4) maps tier names to `TaskPriority` values internally:
```typescript
const TIER_PRIORITY: Record<"critical" | "standard" | "background", TaskPriority> = {
  critical: "high",
  standard: "normal",
  background: "low",
};
```

All existing `taskQueue.enqueue(name, fn)` call sites outside PostProcessor continue to use the default `"normal"` priority unchanged.

**`drainCritical()` implementation** — add to `TaskQueue`:
```typescript
async drainCritical(): Promise<void> {
  while (this.queue.some(t => t.priority === "high")) {
    await this.runNext(); // existing method that processes one task
  }
}
```
The runtime calls `await taskQueue.drainCritical()` after `postProcessor.process()` returns and before `provider.chat()` is called for the next turn.

Note: cost tracking (`costTracker.record()`), event bus emission, and `owlPerf.record()` are already synchronous direct calls in PostProcessor — they are not queued and satisfy the pre-LLM-call guarantee without needing CRITICAL tier treatment.

### Decision 2 — Structured telemetry, not EventBus noise

On job failure: `log.warn` + write one row to `post_processor_job_runs`. No EventBus events (avoids alert spam). DB rows are queryable for future monitoring dashboards. A spike in `success=0` rows for a specific `job_name` is immediately visible via SQL.

### Decision 3 — Zombie jobs: kill enqueue calls, keep code

Remove 3 enqueue call sites from PostProcessor:
- `knowledge-extract` (every 5 messages): removed. Re-added as a BACKGROUND / `"low"` job every 10 messages once `KnowledgeContextLayer` is properly wired (Decision 5).
- `timeline-snapshot`: removed. `TimelineManager` code preserved — PostProcessor no longer drives it.
- `goal-extraction` (`maybeExtractGoals` private method): method deleted, `setGoalExtractor()` removed. `GoalExtractor` code preserved.

`predictive-prep` stays — restructured as BACKGROUND tier, now feeds `PredictiveContextLayer` (Decision 6).

### Decision 4 — Universal job wrapper `enqueueJob()`

All enqueued job call sites are replaced with a private helper. The helper maps tier names to `TaskPriority`, wraps in try/catch, and records telemetry:

```typescript
private enqueueJob(
  name: string,
  tier: "critical" | "standard" | "background",
  fn: () => Promise<void>,
): void {
  const priority = TIER_PRIORITY[tier]; // "high" | "normal" | "low"
  this.taskQueue.enqueue(name, async () => {
    const start = Date.now();
    try {
      await fn();
      this.recordJobRun(name, tier, true, Date.now() - start);
    } catch (err) {
      const code = err instanceof Error ? err.constructor.name : "unknown";
      log.warn(`[PostProcessor:${name}] Failed: ${err instanceof Error ? err.message : err}`);
      this.recordJobRun(name, tier, false, Date.now() - start, code);
    }
  }, priority);
}

private recordJobRun(
  name: string, tier: string, success: boolean,
  durationMs: number, errorCode?: string,
): void {
  if (!this.ctx.db) return;
  try {
    this.ctx.db.rawDb.prepare(
      `INSERT INTO post_processor_job_runs
       (job_name, tier, success, error_code, duration_ms, user_id, session_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    ).run(name, tier, success ? 1 : 0, errorCode ?? null, durationMs,
      this._lastProcessUserId || null, this._lastSessionId || null);
  } catch {
    // telemetry must never crash the caller
  }
}
```

### Decision 5 — KnowledgeContextLayer properly wired

**Current state:** `KnowledgeGraphLayer` reads `(req.session as any).knowledgeGraphContext` — a property cast that is never set. Result: the layer always returns `""`.

**Fix:**
1. Add `knowledgeGraph?: KnowledgeGraph` to `ContextDependencies` in `src/context/layer.ts`.
2. Rewrite `KnowledgeGraphLayer.build()` to call `req.deps.knowledgeGraph?.queryContext(triage.userMessage)`. Remove the `(req.session as any)` cast.
3. Wire `ctx.knowledgeGraph` into the `ContextDependencies` object in `src/gateway/core.ts`.
4. Re-enable `knowledge-extract` as a `"low"` priority job (every 10 messages) so the layer has data to read.

`KnowledgeGraph.queryContext(userMessage: string): string` — new method on `src/knowledge/graph.ts` that returns the top-3 graph nodes relevant to the user message as a formatted string (keyword match against node labels if no embeddings available, cosine search if available).

### Decision 6 — PredictiveContextLayer (new)

**Current state:** `predictive-prep` calls `generatePredictions()` and `prepareTask()` — storing `PredictedTask[]` in a JSON file. Nothing reads it back into any prompt.

**Fix:**
1. `PredictiveQueue.getReadyTasks()` already exists (`src/predictive/queue.ts:111`) returning all ready tasks. The context layer calls `queue.getReadyTasks().slice(0, 3)` — no new method needed.
2. Add `predictiveQueue?: PredictiveQueue` to `ContextDependencies`.
3. Build `PredictiveContextLayer` at `src/context/layers/predictive.ts` (priority 90, maxTokens 200).
4. Register in `src/context/index.ts`.
5. Wire `ctx.predictiveQueue` into `ContextDependencies` in `src/gateway/core.ts`.

Output format:
```xml
<predicted_next>
  <task confidence="0.82">Review open pull requests</task>
  <task confidence="0.71">Check daily calendar</task>
</predicted_next>
```

### Decision 7 — sessionId threading fix

Replace `sessionId ?? "unknown"` at both call sites with an early-return guard: if `sessionId` is `undefined`, skip the job. Logging `"unknown"` sessionId groups unrelated failures and pollutes the telemetry table.

Capture sessionId at top of `process()` alongside `_lastProcessUserId`:
```typescript
this._lastSessionId = sessionId ?? null;
```

### Decision 8 — sentimentProbe null guard

`ctx.db!.rawDb.prepare(...)` in the SentimentProbe callback uses a bare `!` assertion. If `ctx.db` is absent (e.g. test environments), this crashes the PostProcessor constructor. Replace with:
```typescript
this.ctx.db?.rawDb?.prepare(...)?.run(this._lastProcessUserId);
```

### Decision 9 — Synchronous direct call guards

Three synchronous paths in `process()` run on every message with no error isolation:

1. **`coordinator.processMessage()`** (line ~313): wrap in `try/catch`; on error log.warn and record to telemetry with `tier="standard"`, `success=false`.

2. **`patternAnalyzer.recordAction()` / `enrichFromProfile()`** (lines ~472–485): wrap both calls in a single `try/catch`; silent `log.warn` on failure.

3. **`sentimentProbe.arm()` / `sentimentProbe.onNextMessage()`** (lines ~110–112): wrap in `try/catch`; on error log.warn and record. A crash here must not abort the rest of `process()`.

These are not enqueued so they don't go through `enqueueJob()` — they get individual inline try/catch blocks. The rationale: these fire synchronously before the queue so wrapping them in the async job pattern would change their execution semantics.

---

## Bidirectionality Map (post-implementation)

All 21 active queued jobs with confirmed write destination and read-back path. Two removed zombies listed at bottom.

| Job | Priority | Writes to | Read back via |
|-----|----------|-----------|---------------|
| `digest-update` | `high` | `ConversationDigest` | `WorkingMemoryDigestLayer` |
| `sentiment-challenge-update` | `high` | `outcome_journal` | → coordinator gate → DNA evolution |
| `fact-extract` | `normal` | `FactStore` | `memoryBus` → `UserMemoryLayer` |
| `success-recipe` | `normal` | `FactStore` | same |
| `learning-orchestrator` | `normal` | `PelletStore` | Pellet context layers |
| `learning` (legacy fallback) | `normal` | `PelletStore` | Pellet context layers |
| `compress` | `normal` | `summaries` table | `CompressionSummaryLayer` |
| `reflexion-write` | `normal` | `intelligence_reflexions` | `CritiqueRetriever` → pre-task prompt |
| `quality-reflexion` | `normal` | `evolution_reflexions` | `ReflexionEngine` behavioral patch |
| `gap-feedback` | `normal` | `PelletStore` | Pellet context layers |
| `sleep-consolidation` | `normal` | `PelletStore` | Pellet context layers |
| `memory-decay` | `normal` | `FactStore` | Maintains retrieval quality |
| `coordinator-save` | `normal` | coordinator JSON | Loaded at next boot |
| `dna-evolve` | `low` | owl DNA file | `buildSystemPrompt()` DNA directives |
| `inner-life-dna-sync` | `low` | owl DNA file | same |
| `dna-preference-feedback` | `low` | owl DNA file | same |
| `anticipation` | `low` | `LearningOrchestrator` | → pellets → context |
| `pattern-save` | `low` | `PatternAnalyzer` JSON | → coordinator enrichment |
| `trust-save` | `low` | `TrustChain` JSON | `/trust` CLI command |
| `predictive-prep` | `low` | `PredictiveQueue` JSON | **`PredictiveContextLayer`** (new) |
| `knowledge-extract` | `low` | `KnowledgeGraph` | **`KnowledgeGraphLayer`** (fixed) |

**Removed (zombie):** `timeline-snapshot`, `goal-extraction`

**Synchronous (not queued, covered by Decision 9 guards):**
- `coordinator.processMessage()` — feeds MicroLearner → SignalBus → MutationTracker
- `patternAnalyzer.recordAction()` / `enrichFromProfile()` — feeds PatternAnalyzer state
- `sentimentProbe.arm()` / `onNextMessage()` — feeds SentimentProbe → `sentiment-challenge-update`
- `costTracker.record()` — cost monitoring
- `eventBus.emit("message:responded")` — event subscribers
- `owlPerf.record()` — owl performance table

---

## Schema Migration — v18

Current `SCHEMA_VERSION` in `src/memory/db.ts` is **17**. This migration targets v18.

```sql
-- post_processor_job_runs: telemetry for all PostProcessor jobs
CREATE TABLE IF NOT EXISTS post_processor_job_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name     TEXT    NOT NULL,
  tier         TEXT    NOT NULL,  -- 'critical' | 'standard' | 'background'
  success      INTEGER NOT NULL,  -- 0 or 1
  error_code   TEXT,              -- exception class name on failure
  duration_ms  INTEGER,
  user_id      TEXT,
  session_id   TEXT,
  ts           TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ppjr_job_ts ON post_processor_job_runs(job_name, ts);
CREATE INDEX IF NOT EXISTS idx_ppjr_success ON post_processor_job_runs(success, ts);
```

---

## Files Modified

| File | Change |
|------|--------|
| `src/queue/task-queue.ts` | Add `drainCritical()` method |
| `src/gateway/handlers/post-processor.ts` | `enqueueJob()` wrapper + `TIER_PRIORITY` map, zombie removal, sessionId fix, null guards (Decision 8 + 9), `_lastSessionId` field |
| `src/memory/db.ts` | Schema v18: `post_processor_job_runs` table + indexes |
| `src/context/layer.ts` | Add `knowledgeGraph?`, `predictiveQueue?` to `ContextDependencies` |
| `src/context/layers/knowledge.ts` | Rewrite `KnowledgeGraphLayer.build()` to use `req.deps.knowledgeGraph`; remove `(req.session as any)` cast |
| `src/context/layers/predictive.ts` | **New** — `PredictiveContextLayer` |
| `src/context/index.ts` | Register `PredictiveContextLayer` |
| `src/gateway/core.ts` | Wire `knowledgeGraph` + `predictiveQueue` into `ContextDependencies`; add `await taskQueue.drainCritical()` call site |
| `src/knowledge/graph.ts` | Add `queryContext(userMessage: string): string` method |
| `src/engine/runtime.ts` | Await `taskQueue.drainCritical()` before each `provider.chat()` call |

---

## Verification Plan

**Phase A — Infrastructure (priority queue + telemetry + error handling):**
1. `npm test` — all existing 633 tests pass; new tests for priority drain order, job wrapper error recording, schema v18 migration.
2. Mock a failing job; confirm `post_processor_job_runs` row written with `success=0` and correct `error_code`.
3. Mock a `"low"` priority job that takes 3 s; confirm a subsequent `"high"` priority job still completes first.
4. Confirm `digest-update` rows in `post_processor_job_runs` have `tier='critical'`.
5. Confirm that a thrown error inside `coordinator.processMessage()` does not abort `process()` — subsequent jobs still fire.

**Phase B — Zombie removal:**
6. Confirm `maybeExtractGoals` method is gone; `setGoalExtractor()` is gone.
7. Confirm `timeline-snapshot` and `knowledge-extract` (old 5-message site) are not called.
8. `npm test` — no regressions.

**Phase C — KnowledgeContextLayer wiring:**
9. Seed `KnowledgeGraph` with 3 nodes; call `queryContext("test")`; confirm non-empty string returned.
10. Build a `ContextRequest` with `deps.knowledgeGraph` set; confirm `KnowledgeGraphLayer.build()` returns `<knowledge_graph>` block.
11. Run `knowledge-extract` BACKGROUND job; confirm rows appear in KnowledgeGraph after 10 messages.

**Phase D — PredictiveContextLayer:**
12. Seed `PredictiveQueue` with 2 `status="ready"` tasks; call `getReadyTasks().slice(0, 3)`; confirm correct ordering by confidence.
13. Build a `ContextRequest` with `deps.predictiveQueue` set; confirm `PredictiveContextLayer.build()` returns `<predicted_next>` block.
14. End-to-end: run 11 messages; confirm `predictive-prep` fires; confirm next turn's system prompt contains `<predicted_next>`.

**Overall:**
- Test count target: 633 (existing) + ~35 new = ~668 tests
- No regressions on existing 633 tests
- All 21 active queued jobs in the bidirectionality map have confirmed read-back paths

---

## What Was Already Implemented (Owl Intelligence Tasks 1-15)

The following PostProcessor changes were implemented during the Owl Intelligence element and are **not** part of this element's implementation scope:

- `sleep-consolidation` job via `SleepTimeConsolidator` (Task 13) — wired via `session:ended` event bus in `core.ts`
- `IntelligenceReflexionEngine` wired as 8th PostProcessor constructor arg, driving `reflexion-write` job (Task 15)
- `SentimentProbe` constructor integration driving `sentiment-challenge-update` (Task 15)

These are complete and tested. Element 8 adds the `enqueueJob()` wrapper to existing jobs (including these three) for uniform error handling and telemetry.

---

## Frontier References

- [State of AI Agent Memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026) — storage without retrieval is dead weight; 26% recall improvement from proper read-back
- [Agent.xpu dual-queue architecture](https://arxiv.org/html/2506.24045v1/) — real-time vs best-effort queue separation; 40–60% p95 latency reduction for critical jobs
- [Agentic AI Production Cost: 6 Months of Real Data (Inventiple)](https://www.inventiple.com/blog/agentic-ai-production-cost-analysis) — LLM API calls = 60–80% of total agent cost; background jobs that don't change behavior waste budget
- [OpenClaw Dreaming: AI Memory Consolidation](https://xeroaiagency.com/blog/openclaw-dreaming-memory/) — sleep-time consolidation proven valuable when coupled with retrieval path
