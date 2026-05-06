# Element 13 — Learning Engine Design Spec

**Date:** 2026-05-05  
**Status:** Boss-approved  
**Inputs:** element13-learning-engine-audit-2026-05-05.md, market-element13-learning-engine-research-2026-05-05.md, element13-learning-engine-architecture-2026-05-05.md  
**Spec target:** `docs/superpowers/specs/2026-05-05-element13-learning-engine-design.md`  
**Plan target:** `docs/superpowers/plans/2026-05-05-element13-learning-engine.md`

---

## §0 — Summary

Element 13 fixes 11 gaps in StackOwl's adaptive learning subsystem. No new files are created. Three dead-code files are deleted. Nine existing files are modified. The net change is −3 files and ~1,116 lines removed.

All learning remains inference-time only — no model fine-tuning.

**What ships:**
1. Twin-engine collapse — one learning pipeline, not two
2. Proactive session that actually fires (evidence-based, not random)
3. Tool-failure feedback loop — GoalVerifier BLOCKED/PARTIAL → owl_learnings
4. Memory admission + eviction — inflation stopped
5. Approach-library effectiveness scoring — live, not dead code
6. MistakePatternDetector Jaccard — ported into `ApproachLibraryRepo`, predecessor files deleted
7. Minor fixes: G5 (domain expertise hardcoded `true`), G6 (style/temporal signals), behavioral.ts injection ordering

---

## §1 — Locked Architectural Decisions (non-negotiable)

| # | Decision |
|---|---|
| D1 | DELETE `self-study.ts` — twin-engine collapse; idle-engine migrates to orchestrator |
| D2 | IN-ORCHESTRATOR proactive trigger; callers build + pass `ProactiveContext` struct |
| D3 | `post-processor.ts` new job `"learning-failure-critique"` reads `trajectory_turns` BLOCKED/PARTIAL → stores to `owl_learnings` via IntelligenceRouter cheap-tier |
| D4 | `OwlLearningsRepo.admitIfWorthy()` + `evictStale()` methods; eviction called from `SleepTimeConsolidator.onSessionEnded()` via raw SQL |
| D5 | Effectiveness scoring via SQL aggregation on `approach_library` — NO schema change |
| D6 | DELETE `mistake-detector.ts` + `approach-library.ts`; Jaccard ported to `ApproachLibraryRepo` |

---

## §2 — File Delta Plan

### Deleted (3 files, ~1,116 lines)

| File | Lines | Reason |
|------|-------|--------|
| `src/learning/self-study.ts` | ~647 | Twin-engine collapse (D1). `runStudySession()` logic migrates to `orchestrator.runProactiveSession()`. |
| `src/learning/mistake-detector.ts` | ~401 | Dead code. Jaccard + `warnForTask()` ported to `ApproachLibraryRepo`. |
| `src/learning/approach-library.ts` | ~195 | Dead code. In-memory class fully superseded by `ApproachLibraryRepo` in `db.ts`. |

### New Files: NONE. Net delta: −3 files.

### Modified (9 files)

| File | Changes |
|------|---------|
| `src/learning/orchestrator.ts` | Add `ProactiveContext` interface; replace `runProactiveSession()` no-op with evidence-based trigger hierarchy |
| `src/memory/db.ts` | Add `admitIfWorthy()` + `evictStale()` to `OwlLearningsRepo`; add `getEffectivenessScore()` + `getRepeatFailureWarning()` (with ported Jaccard) to `ApproachLibraryRepo`; add `getFailureDensityTopics()` to `TrajectoryTurnsRepo` |
| `src/intelligence/sleep-time-consolidator.ts` | Add raw SQL eviction call at start of `onSessionEnded()` |
| `src/gateway/handlers/post-processor.ts` | Add `"learning-failure-critique"` background job |
| `src/gateway/core.ts` | Fix `core.ts:2353`: pass actual success/failure to `recordToolExecution()` |
| `src/heartbeat/idle-engine.ts` | Replace `learningEngine` with `learningOrchestrator` + `db` in callbacks; migrate `runAnticipatoryResearch()` and `runKnowledgeRefresh()` |
| `src/index.ts` | Remove `learningEngineFactory` + `LearningEngine` import; update `IdleActivityEngine` construction |
| `src/learning/micro-learner.ts` | Emit `style` + `temporal` signals in `processMessage()` |
| `src/context/layers/behavioral.ts` | Change `slice(0,5)` → `slice(0,6)`; reorder session population (failure category first) |

---

## §3 — Data Contracts

### 3.1 `ProactiveContext` interface (new, added to `orchestrator.ts`)

```typescript
export interface ProactiveContext {
  /** Tool names with ≥ minOccurrences failures in the last daysBack days */
  failureDensityTopics?: string[];
  /** Patterns from ToolOutcomeStore or pattern miner (optional enrichment) */
  upcomingPatterns?: string[];
  /** Topics from owl_learnings where confidence < 0.5 (optional enrichment) */
  lowConfidenceTopics?: string[];
  /** Cap on topics to study. Default: 3 */
  maxTopics?: number;
}
```

### 3.2 `runProactiveSession` updated signature

```typescript
async runProactiveSession(context?: ProactiveContext): Promise<LearningCycle>
```

**Quality gate (evaluated in order):**
1. If `context` is undefined → check `graphManager.getStudyQueue(1)`. If empty → return zeroed `LearningCycle` immediately.
2. If `context` provided but all arrays empty AND `maxTopics` is undefined → check KG queue. If empty → return zeroed cycle.
3. Otherwise proceed with trigger hierarchy below.

**Topic selection priority (highest to lowest):**
1. `context.failureDensityTopics` (study the tools/domains that have been failing)
2. `graphManager.getStudyQueue(maxTopics ?? 3)` (KG frontier from reactive conversations)
3. `context.upcomingPatterns` (pattern miner hints)
4. `context.lowConfidenceTopics` (reinforce shaky knowledge)

Deduplicate across all sources. Pass top `maxTopics ?? 3` to `this.synthesizer.synthesize()`. Record result in `LearningCycle` as `trigger: 'scheduled'`.

### 3.3 `TrajectoryTurnsRepo.getFailureDensityTopics` (new method in `db.ts`)

```typescript
getFailureDensityTopics(daysBack: number, minOccurrences: number): string[]
```

SQL:
```sql
SELECT tool_name
FROM trajectory_turns
WHERE verification_result IN ('BLOCKED', 'PARTIAL')
  AND created_at > datetime('now', '-' || ? || ' days')
  AND tool_name IS NOT NULL
GROUP BY tool_name
HAVING COUNT(*) >= ?
ORDER BY COUNT(*) DESC
LIMIT 10
```

Returns array of `tool_name` strings. Returns `[]` if table doesn't exist yet (wrap in try/catch).

### 3.4 `OwlLearningsRepo` new methods (in `db.ts`)

#### `admitIfWorthy`

```typescript
admitIfWorthy(
  owlName: string,
  learning: string,
  category: string,
  confidence: number
): { id: number } | null
```

**Logic:**
1. Fetch recent entries: `SELECT learning FROM owl_learnings WHERE owl_name = ? AND created_at > datetime('now', '-30 days')`
2. For each recent entry, compute Jaccard word-overlap similarity with `learning` using the ported `computeSimilarity()` function.
3. If any similarity ≥ 0.6 → return `null` (duplicate detected, entry rejected).
4. Otherwise call `this.add(owlName, learning, category, '', confidence)` and return `{ id }`.

Returns `null` on rejection (not an error). Callers must handle `null` silently.

#### `evictStale`

```typescript
evictStale(): number
```

SQL:
```sql
DELETE FROM owl_learnings
WHERE confidence < 0.3
  AND reinforcement_count <= 1
  AND created_at < datetime('now', '-14 days')
```

Returns `changes` (count of deleted rows). Idempotent — safe to call on every session end.

### 3.5 `ApproachLibraryRepo` new methods (in `db.ts`)

#### `getEffectivenessScore`

```typescript
getEffectivenessScore(
  owlName: string,
  toolName: string
): number
```

**Logic:**
1. SQL:
   ```sql
   SELECT
     COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
     COUNT(*) FILTER (WHERE outcome = 'failure') AS failure_count,
     MAX(created_at) FILTER (WHERE outcome = 'success') AS last_success
   FROM approach_library
   WHERE owl_name = ? AND tool_name = ?
   ```
2. If no rows → return `0.5` (neutral prior, no history).
3. `baseScore = success_count / (success_count + failure_count)`
4. `ageMs = Date.now() - new Date(last_success ?? 0).getTime()`
5. `decayFactor = Math.pow(0.5, ageMs / (14 * 24 * 60 * 60 * 1000))` — 14-day half-life
6. Return `baseScore * decayFactor + (1 - decayFactor) * 0.5` — blend toward neutral as decay deepens

#### `getRepeatFailureWarning`

```typescript
getRepeatFailureWarning(
  toolName: string,
  taskKeywords: string[]
): string | null
```

**Logic:**
1. Check in-memory cooldown map (`Map<string, number>`): if `Date.now() - cooldown.get(toolName) < 3_600_000` → return `null`.
2. SQL:
   ```sql
   SELECT task_description, failure_reason
   FROM approach_library
   WHERE tool_name = ? AND outcome = 'failure'
   ORDER BY created_at DESC
   LIMIT 20
   ```
3. For each row, compute `computeSimilarity(taskKeywords, tokenize(task_description))`.
4. If any similarity ≥ 0.6 → set cooldown, return:
   `"Warning: similar task failed previously with ${toolName}. Past failure: ${failure_reason}. Consider an alternative approach."`
5. If no match → return `null`.

**`computeSimilarity` (ported from `mistake-detector.ts:64-75`):**
```typescript
function computeSimilarity(setA: string[], setB: string[]): number {
  const a = new Set(setA.map(w => w.toLowerCase()));
  const b = new Set(setB.map(w => w.toLowerCase()));
  const intersection = [...a].filter(w => b.has(w)).length;
  const union = new Set([...a, ...b]).size;
  return union === 0 ? 0 : intersection / union;
}

function tokenize(text: string): string[] {
  return text.toLowerCase().split(/\W+/).filter(w => w.length > 2);
}
```

Both `computeSimilarity` and `tokenize` are module-private functions in `db.ts` (not exported). Reused by both `admitIfWorthy` and `getRepeatFailureWarning`.

### 3.6 `IdleEngineCallbacks` updated interface

```typescript
export interface IdleEngineCallbacks {
  onResult: (result: IdleActivityResult) => void;
  patternMiner?: PatternMiner;
  capabilityScanner?: CapabilityScanner;
  learningOrchestrator?: LearningOrchestrator;  // replaces learningEngine
  db?: MemoryDatabase;                           // for ProactiveContext building
  toolOutcomeStore?: ToolOutcomeStore;
}
```

`runAnticipatoryResearch()` migration:
```typescript
private async runAnticipatoryResearch(): Promise<IdleActivityResult> {
  if (!this.callbacks.learningOrchestrator) {
    return { activity: 'anticipatory_research', success: false };
  }
  const failureDensityTopics = this.callbacks.db
    ? (this.callbacks.db.trajectoryTurns?.getFailureDensityTopics(7, 2) ?? [])
    : [];
  await this.callbacks.learningOrchestrator.runProactiveSession({
    failureDensityTopics,
    maxTopics: 3,
  });
  return { activity: 'anticipatory_research', success: true };
}
```

`runKnowledgeRefresh()` migration:
```typescript
private async runKnowledgeRefresh(): Promise<IdleActivityResult> {
  if (!this.callbacks.learningOrchestrator) {
    return { activity: 'knowledge_refresh', success: false };
  }
  await this.callbacks.learningOrchestrator.runProactiveSession({ maxTopics: 1 });
  return { activity: 'knowledge_refresh', success: true };
}
```

### 3.7 Failure critique job (D3, in `post-processor.ts`)

New job `"learning-failure-critique"` added after existing `"learning-orchestrator"` job. Runs in background (non-blocking).

**Critique prompt template:**
```
You are a learning assistant. In exactly two sentences:
Sentence 1: What went wrong when the assistant called "{tool_name}" and received a "{verdict}" result? (Context: "{verifier_reason}")
Sentence 2: In one concrete action, how should the assistant approach this differently next time?
Write only the two sentences. No headers, no explanation.
```

**Job logic:**
```typescript
// Read BLOCKED/PARTIAL turns from this session
const failedTurns = ctx.db.trajectoryTurns?.getSessionFailures(ctx.sessionId) ?? [];
if (failedTurns.length === 0) return;

for (const turn of failedTurns.slice(0, 3)) {  // cap at 3 per session
  const prompt = CRITIQUE_PROMPT_TEMPLATE
    .replace('{tool_name}', turn.tool_name ?? 'unknown')
    .replace('{verdict}', turn.verification_result)
    .replace('{verifier_reason}', turn.verifier_reason ?? '');
  
  try {
    const critique = await ctx.intelligenceRouter.classify(prompt, 'cheap');
    const admitted = ctx.db.owlLearnings.admitIfWorthy(
      ctx.owlName, critique, 'failure', 0.6
    );
    if (admitted) {
      log.evolution.info(`[Critique] Stored failure learning for ${turn.tool_name}`);
    }
  } catch (err) {
    log.evolution.warn(`[Critique] Failed to generate critique: ${err}`);
    // non-blocking — continue loop
  }
}
```

Requires adding `TrajectoryTurnsRepo.getSessionFailures(sessionId)` method:
```typescript
getSessionFailures(sessionId: string): Array<{
  tool_name: string | null,
  verification_result: string,
  verifier_reason: string | null
}>
```
SQL: `SELECT tool_name, verification_result, verifier_reason FROM trajectory_turns WHERE session_id = ? AND verification_result IN ('BLOCKED', 'PARTIAL')`

### 3.8 `SleepTimeConsolidator` eviction hook

In `onSessionEnded()`, before the LLM pellet step:
```typescript
// SCM-style eviction: prune stale learnings before consolidation
try {
  const evicted = this.raw.prepare(`
    DELETE FROM owl_learnings
    WHERE confidence < 0.3
      AND reinforcement_count <= 1
      AND created_at < datetime('now', '-14 days')
  `).run().changes;
  if (evicted > 0) {
    log.memory.info(`[SleepConsolidator] Evicted ${evicted} stale owl_learnings`);
  }
} catch {
  // owl_learnings table may not exist yet — silently skip
}
```

### 3.9 `micro-learner.ts` G6 signal emissions

After line 212 (end of the style-profile update block), add:
```typescript
// Emit style signals for SignalBus → UserPreferenceModel
signals.push({ timestamp, type: 'style', key: 'verbosity', value: Math.min(len / 300, 1) });
signals.push({ timestamp, type: 'style', key: 'question_rate', value: isQuestion ? 1 : 0 });
signals.push({ timestamp, type: 'style', key: 'command_rate', value: isCommand ? 1 : 0 });
// Emit temporal signal
signals.push({ timestamp, type: 'temporal', key: 'hour', value: now.getHours() / 23 });
```

### 3.10 `core.ts` G5 fix

`src/gateway/core.ts:2353` — change:
```typescript
// before
this.domainExpertise!.recordToolExecution(domain, true);

// after
this.domainExpertise!.recordToolExecution(domain, (result as any)?.success !== false);
```

`(result as any)?.success !== false` evaluates to `true` when `success` is `undefined` (legacy tools that don't set it) and `false` only when `success` is explicitly `false`. Preserves existing behavior for all legacy tool output shapes.

### 3.11 `behavioral.ts` injection ordering

Gateway session-setup: change the `owl_learnings` query to order failure category first:
```sql
SELECT * FROM owl_learnings
WHERE owl_name = ?
ORDER BY
  CASE category WHEN 'failure' THEN 0 ELSE 1 END,
  confidence DESC,
  reinforcement_count DESC
LIMIT 6
```

In `OwlLearningsLayer.build()` (line 52): change `learnings.slice(0, 5)` → `learnings.slice(0, 6)`.

---

## §4 — Test Strategy

### Test files

| Test file | What it covers |
|-----------|---------------|
| `__tests__/memory-db-learning.test.ts` | New `db.ts` methods: `admitIfWorthy`, `evictStale`, `getEffectivenessScore`, `getRepeatFailureWarning`, `getFailureDensityTopics`, `getSessionFailures` |
| `__tests__/learning-orchestrator-proactive.test.ts` | `runProactiveSession()`: quality gate, topic priority, maxTopics cap |
| `__tests__/post-processor-critique.test.ts` | D3 failure critique job: no failures → skip; one failure → admits; duplicate → null handled; router throws → non-blocking |
| `__tests__/idle-engine-orchestrator.test.ts` | `runAnticipatoryResearch()` and `runKnowledgeRefresh()` after callback migration |
| `__tests__/micro-learner.test.ts` (extend) | Style + temporal signal emission in `processMessage()` |

### Key test cases per file

**`memory-db-learning.test.ts`:**
- `admitIfWorthy`: admits novel entry; rejects near-duplicate (Jaccard ≥ 0.6, within 30 days); admits same text if prior entry > 30 days old
- `evictStale`: deletes all 3 criteria; keeps entry failing any single criterion; idempotent second call returns 0
- `getEffectivenessScore`: returns 0.5 on no history; returns > 0.5 for 100% success; applies recency decay (older successes score lower)
- `getRepeatFailureWarning`: null when no similar failures; warning string when Jaccard ≥ 0.6; null on second call within 1 hour; new repo instance resets cooldown
- `getFailureDensityTopics`: returns tools meeting threshold; excludes tools below min; respects daysBack window; returns `[]` gracefully on missing table
- `getSessionFailures`: returns only BLOCKED/PARTIAL for the given session_id

**`learning-orchestrator-proactive.test.ts`:**
- Empty context + empty KG queue → no synthesizer call, returns zeroed cycle
- `failureDensityTopics` present → synthesizer called with those topics
- Empty context + non-empty KG queue → synthesizer called with KG topics
- Failure topics take precedence over KG topics when both present
- `maxTopics: 1` → synthesizer called with exactly 1 topic

**`post-processor-critique.test.ts`:**
- No BLOCKED/PARTIAL turns → job returns without calling IntelligenceRouter
- One BLOCKED turn → IntelligenceRouter called once, `admitIfWorthy` called with result
- `admitIfWorthy` returns null → no error, no rethrow
- IntelligenceRouter throws → caught, logged, job completes successfully

**`idle-engine-orchestrator.test.ts`:**
- `runAnticipatoryResearch()` calls `getFailureDensityTopics(7, 2)` then `runProactiveSession({ failureDensityTopics, maxTopics: 3 })`
- `runKnowledgeRefresh()` calls `runProactiveSession({ maxTopics: 1 })` — no DB query
- Missing `db` in callbacks → `failureDensityTopics: []`, orchestrator still called
- Missing `learningOrchestrator` → `{ success: false }` returned immediately

**`micro-learner.test.ts` additions:**
- `processMessage()` returns signals including at least one `type:'style'` and one `type:'temporal'`
- Verbosity value ≤ 1.0 for any message length
- Temporal `key` is `'hour'`, value is `0–1` (spot check: hour 0 → 0, hour 23 → 1)

---

## §5 — Implementation Order (safe deletion sequence)

Tasks must execute in this order to avoid broken compile states:

1. **Task 1:** Add helper functions `computeSimilarity` + `tokenize` to `db.ts`. Add `getFailureDensityTopics` + `getSessionFailures` to `TrajectoryTurnsRepo`. Add `admitIfWorthy` + `evictStale` to `OwlLearningsRepo`. Add `getEffectivenessScore` + `getRepeatFailureWarning` to `ApproachLibraryRepo`. Write all tests. Commit.

2. **Task 2:** Delete `src/learning/approach-library.ts` and `src/learning/mistake-detector.ts` (confirm zero import sites first). Commit.

3. **Task 3:** Add `ProactiveContext` interface to `orchestrator.ts`. Replace `runProactiveSession()` stub body with evidence-based trigger hierarchy. Write tests. Commit.

4. **Task 4:** Add failure critique job to `post-processor.ts`. Wire `IntelligenceRouter` cheap-tier. Write tests. Commit.

5. **Task 5:** Update `IdleEngineCallbacks` (replace `learningEngine` with `learningOrchestrator` + `db`). Migrate `runAnticipatoryResearch()` and `runKnowledgeRefresh()`. Write tests. Commit.

6. **Task 6:** Update `src/index.ts`: remove `learningEngineFactory`, update `IdleActivityEngine` construction. Confirm compile passes. Commit.

7. **Task 7:** Delete `src/learning/self-study.ts` (all callers already migrated in Tasks 5–6). Confirm compile. Commit.

8. **Task 8:** Add eviction call to `SleepTimeConsolidator.onSessionEnded()`. Fix `core.ts:2353` G5. Update `behavioral.ts` slice + injection order. Emit signals in `micro-learner.ts`. Commit.

---

## §6 — Standing Invariants

1. **No hardcoded keyword arrays** — `computeSimilarity` operates on word sets passed by caller, no embedded keyword lists. Critique prompt uses no regex or keyword matching.
2. **Channel parity** — `owl_learnings` injected via `OwlLearningsLayer` in ContextPipeline, identical across CLI/Telegram/Slack/Voice/Web.
3. **`MemoryReflexionEngine` is not replaced** — it captures conversation facts (preferences, decisions, project context). D3 failure critiques are tool-level failures only. Both run; neither duplicates the other.
4. **`admitIfWorthy()` is not a mandatory gate for existing callers** — `compressor.ts`, `parliament/orchestrator.ts`, `tools/remember.ts` continue using `db.owlLearnings.add()` directly. No breaking changes.
5. **Proactive sessions default to no-op** — zero qualifying signals = zeroed `LearningCycle` immediately. Token conservation is the default; study is the exception.
6. **Failure critiques are confidence-0.6 seeds** — they must be reinforced (`reinforce()`) to gain full injection weight. Low-quality critiques decay naturally via `evictStale()`.
7. **No schema migration** — D5 uses SQL aggregation on existing `approach_library` columns. No `ALTER TABLE`, no DB version bump.

---

## §7 — Out of Scope (Deferred)

- `SleepTimeConsolidator` cross-session pattern synthesis over `owl_learnings` (only eviction added here)
- `MemoryFeedback.process()` trigger wiring (G8) — requires message handler change
- `Anticipator.generateProactiveContent()` for `content_prep` (G10)
- `DomainExpertiseTracker` persistence (in-memory, lost on restart) — G5 fix (correct success/failure signal) is in scope; persistence is not
- `runDeepResearch()` upgrade from Q&A alias (G2)
- `runDocumentDigest()` hardcoded paths (G9)
- Existing hardcoded keyword arrays in `micro-learner.ts` (`POSITIVE_SIGNALS`, `NEGATIVE_SIGNALS`, `TOPIC_PATTERNS`) — pre-existing, not introduced by Element 13

---

*Spec complete. Awaiting Boss review before writing-plans.*
