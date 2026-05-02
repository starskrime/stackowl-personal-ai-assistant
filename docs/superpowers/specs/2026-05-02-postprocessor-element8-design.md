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

**Supporting evidence from 2026 production research:** Mem0's architecture shows a 26% recall improvement over OpenAI built-in memory — but only when extraction is coupled with retrieval into the system prompt. Storage without retrieval is dead weight. Agent.xpu's dual-queue architecture demonstrates that separating real-time (CRITICAL) from background (BACKGROUND) jobs reduces p95 latency by 40–60% for the jobs that matter.

---

## Design Decisions

### Decision 1 — Three-tier TaskQueue

Every `enqueue()` call gains an optional `priority: "critical" | "standard" | "background"` parameter (default: `"standard"`). The queue maintains three internal sub-queues and drains in strict order: CRITICAL → STANDARD → BACKGROUND.

The runtime awaits `taskQueue.drainCritical()` before calling `provider.chat()` for the next turn. This ensures `digest-update` and `cost-record` always complete before the LLM sees the next message — the feedback loop from the previous turn is guaranteed to have closed.

| Tier | Jobs | Guarantee |
|------|------|-----------|
| `CRITICAL` | `digest-update`, `sentiment-challenge-update` | Drains before next LLM call |
| `STANDARD` | `fact-extract`, `success-recipe`, `learning-orchestrator` / `learning`, `reflexion-write`, `quality-reflexion`, `compress`, `gap-feedback`, `sleep-consolidation`, `memory-decay`, `coordinator-save` | Completes within 5 s on p95 |
| `BACKGROUND` | `dna-evolve`, `inner-life-dna-sync`, `dna-preference-feedback`, `anticipation`, `pattern-save`, `trust-save`, `predictive-prep`, `knowledge-extract` (re-enabled) | Runs opportunistically, never blocks |

Note: cost tracking (`costTracker.record()`), event bus emission, and `owlPerf.record()` are already synchronous direct calls in PostProcessor — they are not queued and already satisfy the pre-LLM-call guarantee without needing CRITICAL tier treatment.

### Decision 2 — Structured telemetry, not EventBus noise

On job failure: `log.warn` + write one row to `post_processor_job_runs`. No EventBus events (avoids alert spam). DB rows are queryable for future monitoring dashboards. The counter accumulates — a spike in `success=0` rows for a specific `job_name` is immediately visible via SQL.

### Decision 3 — Zombie jobs: kill enqueue calls, keep code

Remove 3 enqueue call sites from PostProcessor:
- `knowledge-extract` (every 5 messages): removed. `knowledge-extract` is re-added as a BACKGROUND job every 10 messages once `KnowledgeContextLayer` is properly wired (Decision 5).
- `timeline-snapshot`: removed. `TimelineManager` code preserved — PostProcessor no longer drives it.
- `goal-extraction` (`maybeExtractGoals`): method deleted, `setGoalExtractor()` removed. `GoalExtractor` code preserved.

`predictive-prep` stays — restructured as BACKGROUND tier, now feeds `PredictiveContextLayer` (Decision 6).

### Decision 4 — Universal job wrapper `enqueueJob()`

All 23 job call sites are replaced with a private helper:

```typescript
private enqueueJob(
  name: string,
  tier: "critical" | "standard" | "background",
  fn: () => Promise<void>,
): void {
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
  }, tier);
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
2. Rewrite `KnowledgeGraphLayer.build()` to call `req.deps.knowledgeGraph?.queryContext(triage.userMessage)`.
3. Wire `ctx.knowledgeGraph` into the `ContextDependencies` object in `src/gateway/core.ts`.
4. Re-enable `knowledge-extract` as a BACKGROUND-tier job (every 10 messages) so the layer has data to read.

`KnowledgeGraph.queryContext(userMessage: string): string` — new method on `KnowledgeGraph` that returns the top-3 graph nodes relevant to the user message as a formatted string.

### Decision 6 — PredictiveContextLayer (new)

**Current state:** `predictive-prep` calls `generatePredictions()` and `prepareTask()` — storing `PredictedTask[]` in a JSON file. Nothing reads it back into any prompt.

**Fix:**
1. Add `getReady(n: number): PredictedTask[]` to `PredictiveQueue` — returns up to `n` tasks with `status === "ready"`, sorted by confidence desc.
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

Replace `sessionId ?? "unknown"` at both call sites (PostProcessor lines where `sessionId` is used as a DB key) with an early-return guard: if `sessionId` is `undefined`, skip the job. Logging `"unknown"` sessionId groups unrelated failures and pollutes the telemetry table.

Capture sessionId at top of `process()` alongside `_lastProcessUserId`:
```typescript
this._lastSessionId = sessionId ?? null;
```

### Decision 8 — sentimentProbe null guard

`ctx.db!.rawDb.prepare(...)` in the SentimentProbe callback uses a bare `!` assertion. If `ctx.db` is absent (e.g. test environments), this crashes the PostProcessor constructor. Replace with:
```typescript
this.ctx.db?.rawDb?.prepare(...)?.run(this._lastProcessUserId);
```

---

## Bidirectionality Map (post-implementation)

Every job now has a documented write destination and confirmed read-back path:

| Job | Tier | Writes to | Read back via |
|-----|------|-----------|---------------|
| `digest-update` | CRITICAL | `ConversationDigest` | `WorkingMemoryDigestLayer` |
| `sentiment-challenge-update` | CRITICAL | `outcome_journal` | → coordinator gate → DNA evolution |
| `fact-extract` | STANDARD | `FactStore` | `memoryBus` → `UserMemoryLayer` |
| `success-recipe` | STANDARD | `FactStore` | same |
| `learning-orchestrator` | STANDARD | `PelletStore` | Pellet context layers |
| `compress` | STANDARD | `summaries` table | `CompressionSummaryLayer` |
| `reflexion-write` | STANDARD | `intelligence_reflexions` | `CritiqueRetriever` → pre-task prompt |
| `quality-reflexion` | STANDARD | `evolution_reflexions` | `ReflexionEngine` behavioral patch |
| `gap-feedback` | STANDARD | `PelletStore` | Pellet context layers |
| `sleep-consolidation` | STANDARD | `PelletStore` | Pellet context layers |
| `memory-decay` | STANDARD | `FactStore` | Maintains retrieval quality |
| `coordinator-save` | STANDARD | coordinator JSON | Loaded at next boot |
| `dna-evolve` | BACKGROUND | owl DNA file | `buildSystemPrompt()` DNA directives |
| `inner-life-dna-sync` | BACKGROUND | owl DNA file | same |
| `dna-preference-feedback` | BACKGROUND | owl DNA file | same |
| `anticipation` | BACKGROUND | `LearningOrchestrator` | → pellets → context |
| `pattern-save` | BACKGROUND | `PatternAnalyzer` JSON | → coordinator enrichment |
| `trust-save` | BACKGROUND | `TrustChain` JSON | `/trust` CLI command |
| `predictive-prep` | BACKGROUND | `PredictiveQueue` JSON | **`PredictiveContextLayer`** (new) |
| `knowledge-extract` | BACKGROUND | `KnowledgeGraph` | **`KnowledgeGraphLayer`** (fixed) |

Removed (zombie): `timeline-snapshot`, `goal-extraction`

---

## Schema Migration — v19

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
| `src/queue/task-queue.ts` | Add `priority` param, three sub-queues, `drainCritical()` |
| `src/gateway/handlers/post-processor.ts` | `enqueueJob()` wrapper, zombie removal, sessionId fix, sentinel null guard, `_lastSessionId` field |
| `src/memory/db.ts` | Schema v19: `post_processor_job_runs` table + indexes |
| `src/context/layer.ts` | Add `knowledgeGraph?`, `predictiveQueue?` to `ContextDependencies` |
| `src/context/layers/knowledge.ts` | Rewrite `KnowledgeGraphLayer.build()` to use `req.deps.knowledgeGraph` |
| `src/context/layers/predictive.ts` | **New** — `PredictiveContextLayer` |
| `src/context/index.ts` | Register `PredictiveContextLayer` |
| `src/gateway/core.ts` | Wire `knowledgeGraph` + `predictiveQueue` into `ContextDependencies` |
| `src/knowledge/graph.ts` | Add `queryContext(userMessage: string): string` method |
| `src/predictive/queue.ts` | Add `getReady(n: number): PredictedTask[]` method |
| `src/engine/runtime.ts` | Await `taskQueue.drainCritical()` before each `provider.chat()` call |

---

## Verification Plan

**Phase A — Infrastructure (priority queue + telemetry + error handling):**
1. `npm test` — all existing 633 tests pass; new tests for priority drain order, job wrapper error recording, schema v19 migration.
2. Mock a failing job; confirm `post_processor_job_runs` row written with `success=0` and correct `error_code`.
3. Mock a BACKGROUND job that takes 3 s; confirm a subsequent CRITICAL job still completes first.
4. Confirm `digest-update` rows in `post_processor_job_runs` have `tier='critical'`.

**Phase B — Zombie removal:**
5. Confirm `maybeExtractGoals` method is gone; `setGoalExtractor()` is gone.
6. Confirm `timeline-snapshot` and `knowledge-extract` (old site) are not called.
7. `npm test` — no regressions.

**Phase C — KnowledgeContextLayer wiring:**
8. Seed `KnowledgeGraph` with 3 nodes; call `queryContext("test")`; confirm non-empty string returned.
9. Build a `ContextRequest` with `deps.knowledgeGraph` set; confirm `KnowledgeGraphLayer.build()` returns `<knowledge_graph>` block.
10. Run `knowledge-extract` BACKGROUND job; confirm rows appear in KnowledgeGraph after 10 messages.

**Phase D — PredictiveContextLayer:**
11. Seed `PredictiveQueue` with 2 `status="ready"` tasks; call `getReady(3)`; confirm correct ordering.
12. Build a `ContextRequest` with `deps.predictiveQueue` set; confirm `PredictiveContextLayer.build()` returns `<predicted_next>` block.
13. End-to-end: run 11 messages; confirm `predictive-prep` fires; confirm next turn's system prompt contains `<predicted_next>`.

**Overall:**
- Test count target: 633 (existing) + ~35 new = ~668 tests
- No regressions on existing 633 tests
- All 21 jobs in the bidirectionality map have confirmed read-back paths

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
- [Agent.xpu dual-queue architecture](https://arxiv.org/html/2506.24045v1/) — real-time vs best-effort queue separation; 40-60% p95 latency reduction for critical jobs
- [Agentic AI Production Cost: 6 Months of Real Data (Inventiple)](https://www.inventiple.com/blog/agentic-ai-production-cost-analysis) — LLM API calls = 60-80% of total agent cost; background jobs that don't change behavior waste budget
- [OpenClaw Dreaming: AI Memory Consolidation](https://xeroaiagency.com/blog/openclaw-dreaming-memory/) — sleep-time consolidation proven valuable when coupled with retrieval path
