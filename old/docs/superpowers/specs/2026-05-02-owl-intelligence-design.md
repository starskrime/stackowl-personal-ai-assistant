# Owl Intelligence — Design Spec

**Date:** 2026-05-02
**Status:** Approved
**Authors:** Mary (Analyst), John (PM), Winston (Architect)

---

## Problem Statement

StackOwl's owl personas have the right architecture for a deeply personal AI assistant — evolving DNA, Pellet memory, Parliament, tool use — but three production failure modes prevent users from experiencing it as professional and human-like:

1. **The owl gives up.** Multi-step tasks fail halfway, the TaskLedger dies on restart, and there is no path from "I'm stuck" to "let me ask." The owl either hallucinates completion or goes silent.
2. **The owl doesn't learn from failure.** PostProcessor runs 21 tasks after every response, but none of them write a self-critique when a task fails or retrieve past lessons before a similar task starts. Each attempt is amnesia.
3. **Tool selection is noisy and memory goes stale.** The LLM sees all tools every turn, picks wrong ones, and FactStore accumulates contradicted facts silently.

**Competitive context:** Pi feels human but can't act. ChatGPT acts but hallucinates and flatters. No personal AI assistant ships all three: task persistence, self-correcting learning, and felt continuity. This design closes all three gaps.

---

## North Star Positioning

> **The only assistant that grows with you, acts for you, and never pretends.**

- **Grows with you** — Reflexion loop, sleep-time consolidation, skill template learning
- **Acts for you** — TaskLedger persistence across restarts, HITL escalation with narration
- **Never pretends** — Honest failure narration, temporal fact invalidation, observable owl state

---

## User Stories (acceptance criteria for the whole spec)

**Story A — Task resumption:**
> "I gave my owl a 3-hour task. It failed halfway. Next week I asked again — it remembered where it got stuck, tried a different approach, told me what it was doing, and finished."

**Story B — Preference persistence:**
> "I corrected my owl twice about response format. After that, it just knew. I never had to say it again."

**Story C — Trustworthy challenge:**
> "My owl pushed back on my idea. It disagreed and explained why. I thought about it — it was right. I trust it more than any AI I've used."

---

## Architecture

### The `src/intelligence/` module — B+C pattern

New module with nine focused files. Communicates only through `ToolRegistry`, `ContextPipeline`, and `GatewayEventBus`. No direct dependencies on channels or providers.

**Synchronous (on critical path — must complete before LLM receives context):**
- `SemanticToolGate` — filters tool list to top-K per query
- `CritiqueRetriever` — injects past lessons into context before tool list
- `HITLEscalator` — narrates struggle, emits structured ask after N blocked attempts
- `OwlStateReporter` — surfaces observable owl state on demand

**Asynchronous (EventBus subscribers — fire-and-forget, never block response):**
- `ReflexionEngine` — subscribes `task:failed` → writes self-critique
- `SkillTemplateLayer` — subscribes `outcome:success` → generates NL template (extends `pattern-miner.ts`)
- `SentimentProbe` — subscribes `message:received` post-task → backpropagates signal
- `SleepTimeConsolidator` — subscribes `session:ended` → surfaces cross-session insights
- `FactInvalidator` — subscribes `fact:extracted` → marks contradicted facts invalid

### Dependency rule

```
intelligence/ ──reads──▶ DB (SQLite)
intelligence/ ──emits──▶ GatewayEventBus
intelligence/ ──called by──▶ ToolRegistry, ContextPipeline, OwlOrchestrator
intelligence/ ──never calls──▶ channels, providers, gateway
```

---

## Phase A — Foundation

### A1. TaskLedger SQLite Persistence

**Existing:** `src/engine/task-ledger.ts` — TaskLedger is in-memory only. Survives nothing.

**Change:** Add `persist(db: Database)` and `static resume(db, taskId): TaskLedger | null` to `TaskLedger`. Called at every subgoal transition inside `OwlOrchestrator`.

**New table** `owl_task_ledger` (schema v19):
```sql
CREATE TABLE owl_task_ledger (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  user_id     TEXT NOT NULL,
  task_id     TEXT NOT NULL,
  subgoal_index INTEGER NOT NULL,
  subgoal_text  TEXT NOT NULL,
  state_json    TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'in_progress',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  resumed_at    TEXT
);
CREATE INDEX idx_task_ledger_user ON owl_task_ledger(user_id, status);
```

**Session start behavior:** `OwlOrchestrator` queries `owl_task_ledger` for incomplete tasks for this user. If found, injects into context narration: *"Picking up your task from [date] — I was on step [N]: [subgoal_text]. Continuing now."* User can dismiss with "start fresh."

**Files modified:** `src/engine/task-ledger.ts`, `src/engine/orchestrator.ts`, `src/memory/db.ts`

---

### A2. HITL Escalator — narration-first, ask-second

**Existing:** `hitlChannel` field exists in `OrchestratorDeps` but is never called in production. `GatewayEventBus` emits `tool:goal_blocked` but nothing acts on it.

**New file:** `src/intelligence/hitl-escalator.ts`

```typescript
export class HITLEscalator {
  private blockedAttempts = 0;
  private attemptSummaries: string[] = [];

  onBlocked(toolName: string, reason: string, subgoal: string): void {
    this.blockedAttempts++;
    this.attemptSummaries.push(`${toolName}: ${reason}`);
  }

  shouldEscalate(challengeLevel: number): boolean {
    const threshold = Math.max(1, Math.min(5, Math.round(challengeLevel / 2)));
    return this.blockedAttempts >= threshold;
  }

  buildNarration(): string {
    return [
      `I've tried ${this.blockedAttempts} approach${this.blockedAttempts > 1 ? 'es' : ''}:`,
      ...this.attemptSummaries.map((s, i) => `  ${i + 1}. ${s}`),
      `I'm genuinely stuck. Let me ask you one focused question.`
    ].join('\n');
  }

  buildQuestion(alternatives: string[]): string {
    if (alternatives.length < 2) return "How should I proceed?";
    return `Should I try (A) ${alternatives[0]} or (B) ${alternatives[1]}?`;
  }

  reset(): void {
    this.blockedAttempts = 0;
    this.attemptSummaries = [];
  }
}
```

**Behavior:** When `shouldEscalate()` is true, `OwlOrchestrator`:
1. Emits `tool:narration` with `buildNarration()` text → CLI/Telegram renders in real time
2. Calls `hitlChannel.ask(buildQuestion(alternatives))` and awaits reply
3. Continues with chosen path, resets escalator

`challengeLevel` DNA field (0–10, already exists) controls threshold: level 2 → ask after 1 failure, level 6 (default) → ask after 3, level 10 → ask after 5.

**Files modified:** `src/engine/orchestrator.ts`
**Files created:** `src/intelligence/hitl-escalator.ts`

---

### A3. SemanticToolGate

**Existing:** `ToolRegistry.getAllDefinitions()` returns all active tools. `OwlOrchestrator` passes them all to the LLM every turn. `UserMemoryStore` already uses fastembed for vector search.

**New method:** `ToolRegistry.getRelevantTools(query: string, limit = 8): ToolDefinition[]`

At `ToolRegistry` initialization: embed each non-deprecated tool description using fastembed. Cache as `Map<string, Float32Array>` in memory. Rebuild on `tool:registered` events.

Per-query: embed user message (reuse `UserMemoryStore.embed()` to share the fastembed instance), cosine similarity against cached tool embeddings, return top-`limit` sorted by score. MCP tools included. Deprecated tools excluded.

**Latency:** p99 < 5ms (in-memory cosine, no network, no LLM).

**Wire-in:** `OwlOrchestrator` calls `toolRegistry.getRelevantTools(userMessage)` before building the LLM system prompt. Replaces `getAllDefinitions()` on the tool-list injection path.

**Files modified:** `src/tools/registry.ts`, `src/engine/orchestrator.ts`

---

### A4. CritiqueRetriever — ContextPipeline layer

**Existing:** `ContextPipeline` has a priority-ordered layer system. `reflexion_critiques` table does not exist yet (created in Phase B).

**New file:** `src/intelligence/critique-retriever.ts`

New `ContextLayer` registered at priority 9 (just before tool definitions). At inference:
1. Query `reflexion_critiques` for top-2 rows by `(task_category, complexity_tier)` similarity to current message embedding
2. If similarity > 0.70 AND `used_count < 20`: inject as `<past_lessons>` block, increment `used_count`
3. If no matching critiques (new user, new task type): layer is a no-op — no token cost

Injected format:
```xml
<past_lessons>
When doing research tasks: I searched too broadly. Now I use specific terms first.
When doing multi-step file tasks: I forgot to check file existence. Now I verify first.
</past_lessons>
```

**Files created:** `src/intelligence/critique-retriever.ts`
**Files modified:** `src/context/pipeline.ts`

---

## Phase B — Learning Loop

### B1. ReflexionEngine

**Existing:** `OutcomeJournal` records `outcome`, `toolsUsed`, `qualityScore` per session. `ImprovementScheduler` reviews journals every 15min but generates generic lesson strings, not task-specific critiques.

**New file:** `src/intelligence/reflexion-engine.ts`

Subscribes to GatewayEventBus event `task:failed` (emitted by `OwlOrchestrator` when `outcome === "failure"` or `outcome === "partial"`).

On event: calls cheap-tier model via `IntelligenceRouter.resolve("classification")` with prompt:
```
Task: {taskDescription}
Tool sequence attempted: {toolSequence}
Final error: {errorSummary}
Write exactly 2 sentences: (1) why this failed, (2) what to try differently next time.
```

Stores result in `reflexion_critiques` table (schema v19):
```sql
CREATE TABLE reflexion_critiques (
  id               TEXT PRIMARY KEY,
  task_category    TEXT NOT NULL,
  complexity_tier  TEXT NOT NULL,
  tool_sequence    TEXT NOT NULL,
  critique_text    TEXT NOT NULL,
  embedding        BLOB NOT NULL,
  used_count       INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL
);
CREATE INDEX idx_critiques_category ON reflexion_critiques(task_category, complexity_tier);
```

**Skip rules:** Skip if `qualityScore < 0.3` (too noisy to learn from). Skip if identical tool sequence + task category already has a critique (dedup). Max 500 critiques total — evict lowest `used_count` when full.

**Files created:** `src/intelligence/reflexion-engine.ts`
**Files modified:** `src/memory/db.ts`, `src/gateway/handlers/post-processor.ts`

---

### B2. SentimentProbe

**Existing:** `OutcomeJournal.updateSentiment()` exists but is never called. `PostProcessor` does not read the following user message.

**New file:** `src/intelligence/sentiment-probe.ts`

Subscribes to `message:received` on GatewayEventBus. Fires only when `OutcomeJournal` has an entry in `pending_sentiment` state for this user (set by PostProcessor after task completion).

Classification — heuristic, no LLM:
```typescript
function classify(text: string): 'positive' | 'correction' | 'neutral' {
  const lower = text.toLowerCase();
  const correctionSignals = ['no,', 'wrong', 'actually', "that's not", 'incorrect', 'not right', 'try again'];
  const positiveSignals = ['thanks', 'perfect', 'exactly', 'great', 'worked', 'yes!', '👍'];
  if (correctionSignals.some(s => lower.includes(s))) return 'correction';
  if (positiveSignals.some(s => lower.includes(s))) return 'positive';
  return 'neutral';
}
```

On `correction`: calls `OutcomeJournal.updateSentiment('negative')`, increments `challenge_instances` — this is feedback the owl over-agreed.
On `positive`: calls `OutcomeJournal.updateSentiment('positive')`.

**Schema change:** Add `challenge_instances INTEGER DEFAULT 0` to `outcome_journal` table.

**Files created:** `src/intelligence/sentiment-probe.ts`
**Files modified:** `src/gateway/handlers/post-processor.ts`, `src/memory/db.ts`

---

### B3. Skill Template Layer — extend existing, don't replace

**Existing:** `src/skills/pattern-miner.ts` extracts skill patterns from conversation history. `ClawHub` (`src/skills/clawhub.ts`) is the external marketplace client with `search()` and `install()`. `/skills` command exists in CLI and Telegram via `src/skills/wizard.ts`.

**What we add — three targeted extensions:**

**Extension 1: Auto-template generation on success** — add `onOutcomeSuccess(toolSequence, taskDescription, qualityScore)` to `PatternMiner`. When `qualityScore > 0.8`, synthesizes an NL template: *"To [task type]: [tool1(action)] → [tool2(action)] → [result]."* Stored in a new `skill_templates` table.

```sql
CREATE TABLE skill_templates (
  id               TEXT PRIMARY KEY,
  name             TEXT UNIQUE NOT NULL,
  source           TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'marketplace' | 'user'
  template_text    TEXT NOT NULL,
  trigger_desc     TEXT NOT NULL,
  embedding        BLOB NOT NULL,
  success_count    INTEGER NOT NULL DEFAULT 0,
  installed_at     TEXT NOT NULL,
  last_used_at     TEXT
);
```

**Extension 2: ContextPipeline retrieval layer** — new `src/intelligence/skill-template-layer.ts` registered at priority 8. Queries `skill_templates` for top-1 match when similarity > 0.75. Injects as `<proven_approach>` hint. No-op if no match.

**Extension 3: Marketplace browse** — extend `wizard.ts` with `browse` sub-command that calls `clawhub.search("")` with category filter. No new client. Wired into existing `/skills` CLI + Telegram command — no new command.

**Extension 4: `invoke_skill` tool** — new `src/tools/invoke-skill.ts` (15 lines) wrapping `SkillExecutor.executeStructuredSkill()`. Gives LLM a formal tool call to explicitly invoke a named skill. Closes the "skills are only prompt context" gap.

**Files created:** `src/intelligence/skill-template-layer.ts`, `src/tools/invoke-skill.ts`
**Files modified:** `src/skills/pattern-miner.ts`, `src/skills/wizard.ts`, `src/memory/db.ts`

---

### B4. Anti-Sycophancy — DNA-controlled injection

**Existing:** `owl.DNA.challengeLevel` (0–10) exists but only controls conversation challenge behavior. System prompt injection is done in `ContextPipeline`.

**Change:** Add new `ContextLayer` at priority 2 (early, before skills/memory) that reads `owl.DNA.challengeLevel` and injects a matching behavioral directive:

```typescript
const CHALLENGE_DIRECTIVES = {
  low:    "Be supportive and encouraging in your responses.",
  medium: "Be honest, including when you disagree. State disagreement diplomatically with reasoning.",
  high:   "Challenge the user's assumptions when you have good reason to. Be direct and assertive. A trusted advisor, not a yes-man.",
};

function getDirective(level: number): string {
  if (level <= 3) return CHALLENGE_DIRECTIVES.low;
  if (level <= 6) return CHALLENGE_DIRECTIVES.medium;
  return CHALLENGE_DIRECTIVES.high;
}
```

**Evolution signal:** `OwlEvolutionEngine` gains a new input: if `challenge_instances > 2` in last 10 sessions AND average sentiment stayed `positive` → nudge `challengeLevel` +1 (user accepts pushback). If `correction` rate > 30% → nudge `challengeLevel` -1 (pushing back too hard).

**Files modified:** `src/context/pipeline.ts`, `src/owls/evolution.ts`

---

## Phase C — Memory Depth

### C1. Temporal Fact Invalidation

**Existing:** `FactStore` appends new facts, deduplicates at 0.88 cosine similarity, but does not handle contradiction (old fact coexists with contradicting new one).

**New file:** `src/intelligence/fact-invalidator.ts`

Subscribes to `fact:extracted` on GatewayEventBus.

```typescript
const TEMPORAL_TRIGGERS = [
  'moved to', 'now at', 'now works at', 'switched to',
  'no longer', 'changed to', 'actually', 'left', 'quit',
  'joined', 'starting at', 'used to'
];

async function checkAndInvalidate(newFact: string, db: Database): Promise<void> {
  const newEmbedding = await embed(newFact);
  const candidates = await searchFacts(db, newEmbedding, { limit: 3, excludeInvalidated: true });

  for (const candidate of candidates) {
    const hasTrigger = TEMPORAL_TRIGGERS.some(t => newFact.toLowerCase().includes(t));
    const isContradiction = candidate.similarity > 0.85 && entityOverlap(candidate.text, newFact) > 0.7;
    if (hasTrigger && isContradiction) {
      await db.run(`UPDATE facts SET invalidated_at = ? WHERE id = ?`, [new Date().toISOString(), candidate.id]);
    }
  }
}
```

**Schema change:** Add `invalidated_at TEXT` to `facts` table. All existing queries gain `WHERE invalidated_at IS NULL` filter.

**Files created:** `src/intelligence/fact-invalidator.ts`
**Files modified:** `src/memory/db.ts`, `src/memory/fact-store.ts`

---

### C2. `memory_write` and `memory_invalidate` Tools

**Existing:** `memory_unified` tool has `search | store | get` actions. The owl can store facts via `store` action. But it cannot explicitly invalidate a stale fact, and cannot write a fact it inferred without user stating it.

**Change:** Extend `memory_unified` tool with two new actions:

- `memory_write(content, category, confidence)` — owl writes an inferred fact ("you mentioned you prefer TypeScript"). Tagged `source: "owl_inferred"`.
- `memory_invalidate(query)` — owl marks matching facts invalid when user corrects it. Semantic search → `invalidated_at = now` on top match above 0.80 threshold.

**No new tool file.** Extend `src/tools/memory-unified.ts` action dispatch.

**Files modified:** `src/tools/memory-unified.ts`

---

### C3. SleepTimeConsolidator

**Existing:** `GatewayEventBus` emits `session:ended`. `PelletStore` stores and retrieves pellets. `PelletRetriever` is already a ContextPipeline layer. Heartbeat proactive messages use a different trigger (scheduled, not event-driven).

**New file:** `src/intelligence/sleep-time-consolidator.ts`

Subscribes to `session:ended`. Debounced — maximum one run per 60 minutes per user.

On trigger:
1. Reads last 5 session digests + recent pellets for this user
2. Cheap-tier LLM call: *"Given these recent sessions, what 1-3 new patterns or insights about this user can you infer that aren't yet explicitly stored as facts or pellets?"*
3. Writes output as pellets tagged `source: "sleep_consolidation"`, `confidence: 0.7`
4. These surface naturally next session via existing `PelletRetriever` layer

**Cost:** One cheap-tier call per user per session (max). Zero cost if no new sessions since last consolidation. Completely invisible to the user — surfaces as natural owl knowledge next conversation.

**Files created:** `src/intelligence/sleep-time-consolidator.ts`
**Files modified:** `src/gateway/handlers/post-processor.ts`

---

### C4. Observable Owl State — `/owl status`

**Existing:** No `/owl` command exists in CLI or Telegram. Owl DNA, memory counts, and task state are in DB but not surfaced.

**New file:** `src/intelligence/owl-state-reporter.ts`

`OwlStateReporter.report(userId, db): string` — reads directly from DB, no LLM call:

```
Owl: Aria  |  challengeLevel=6 · verbosity=4 · expertise[code]=high
Memory: 142 facts · 23 pellets · last updated 2h ago
Active task: "Research TypeScript 5.5" — step 2/4 (started 3h ago)
Recent learning: "You prefer concise responses for technical questions"
DNA last mutated: Yesterday — verbosity decreased after 3 short-response requests
```

**Wire-in:** Add `/owl` command to `src/cli/commands.ts` and `/owl status` handler to `src/gateway/adapters/telegram.ts`. Both call `OwlStateReporter.report()`. No new command router needed — these are simple single-verb commands.

**Files created:** `src/intelligence/owl-state-reporter.ts`
**Files modified:** `src/cli/commands.ts`, `src/gateway/adapters/telegram.ts`

---

## Data Model — Schema v19

**New tables (3):**
- `owl_task_ledger` — task resumption (Phase A)
- `reflexion_critiques` — self-critiques with embeddings (Phase B)
- `skill_templates` — NL task templates (Phase B)

**Modified tables (2):**
- `outcome_journal` — add `challenge_instances INTEGER DEFAULT 0`
- `facts` — add `invalidated_at TEXT`

**New EventBus events (if not already present):**
- `task:failed` — emitted by OwlOrchestrator on failure/partial outcome
- `fact:extracted` — emitted by FactStore after each fact write
- `session:ended` — emitted by gateway core on session close (verify exists)

---

## File Summary

**New files (10):**
```
src/intelligence/semantic-tool-gate.ts      Phase A
src/intelligence/critique-retriever.ts      Phase A
src/intelligence/hitl-escalator.ts          Phase A
src/intelligence/reflexion-engine.ts        Phase B
src/intelligence/skill-template-layer.ts    Phase B
src/intelligence/sentiment-probe.ts         Phase B
src/intelligence/sleep-time-consolidator.ts Phase C
src/intelligence/fact-invalidator.ts        Phase C
src/intelligence/owl-state-reporter.ts      Phase C
src/tools/invoke-skill.ts                   Phase B
```

**Modified files (13):**
```
src/tools/registry.ts            add getRelevantTools() + startup embedding
src/engine/task-ledger.ts        add persist() + resume()
src/engine/orchestrator.ts       HITL wiring + SemanticToolGate + ledger persist
src/context/pipeline.ts          add CritiqueRetriever + SkillTemplateLayer + challengeLevel layer
src/gateway/handlers/post-processor.ts  wire ReflexionEngine + SentimentProbe + SleepTimeConsolidator
src/owls/evolution.ts            add challenge_instances signal
src/memory/db.ts                 schema v19
src/memory/fact-store.ts         invalidated_at filter + fact:extracted event
src/tools/memory-unified.ts      add memory_write + memory_invalidate actions
src/skills/pattern-miner.ts      add onOutcomeSuccess() NL template generation
src/skills/wizard.ts             add browse sub-command
src/cli/commands.ts              add /owl command
src/gateway/adapters/telegram.ts add /owl status handler
src/index.ts                     register InvokeSkillTool + wire intelligence module
```

---

## Testing Strategy

**Phase A tests (new):**
- `TaskLedger.persist()` survives process restart and `resume()` reloads correct state
- `HITLEscalator.shouldEscalate()` fires at correct threshold per DNA challengeLevel
- `SemanticToolGate.getRelevantTools()` returns ≤ 8 tools, correct tools for semantic queries, p99 < 10ms
- `CritiqueRetriever` injects nothing when table is empty, injects correctly after ReflexionEngine writes

**Phase B tests (new):**
- `ReflexionEngine` writes critique on `task:failed`, skips on clean success
- `SentimentProbe` classifies "no that's wrong" as correction, "perfect!" as positive, "ok" as neutral
- `SkillTemplateLayer` injects correct template on semantic match, no-op below 0.75 threshold
- `invoke_skill` tool calls SkillExecutor with correct params
- Anti-sycophancy directive scales correctly at levels 1, 5, 10

**Phase C tests (new):**
- `FactInvalidator` marks "lives in London" invalid when "moved to Tokyo" extracted
- `FactInvalidator` does NOT invalidate when no temporal trigger present
- `memory_invalidate` action correctly marks top match invalid
- `SleepTimeConsolidator` debounce: fires once, skips second call within 60min
- `/owl status` returns correct counts from DB, renders in < 200ms

**Regression:** All 633 existing tests continue passing.

---

## Phase Gates

**Phase A → B:** All A tests green. Run 3 end-to-end tasks in CLI — confirm narration fires, HITL asks exactly one focused question, SemanticToolGate reduces visible tool list to ≤ 8.

**Phase B → C:** ReflexionEngine has written ≥ 5 critiques from real usage OR synthetic test data. SentimentProbe has classified ≥ 10 post-task messages. Confirm CritiqueRetriever injects past lessons into a matching task.

**Ship gate:** Story A, B, C scenarios pass manual walkthrough. `/owl status` renders correctly. No regressions.

---

## Sources

- Mem0 paper (memory contradiction): arxiv.org/abs/2504.19413
- Reflexion (self-critique pattern): arxiv.org/abs/2303.11366
- RAG-MCP (semantic tool filtering): arxiv.org/abs/2505.03275
- Zep temporal memory: arxiv.org/abs/2501.13956
- Letta sleep-time compute: letta.com/blog/sleep-time-compute
- Galileo production agent failures: galileo.ai/blog/agent-failure-modes-guide
- a16z State of Consumer AI 2025: a16z.com/state-of-consumer-ai-2025
