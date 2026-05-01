# OwlEngine v2 — Core Loop Redesign + Self-Healing Design Spec

**Element:** 6a (OwlEngine — ReAct Loop)  
**Date:** 2026-04-30  
**Status:** Approved for implementation  
**Deferred:** Element 6b (Multimodal + Vision Routing) — separate spec  

---

## 1. Problem Statement

The current `src/engine/runtime.ts` is a 3,300-line monolith that does everything: context assembly, tool dispatch, loop control, self-check, history compression, provider resilience, and gap detection — all in one class. The consequences:

- **No pre-execution planning.** The owl dives into tool calls immediately. For complex tasks this means the first 5–10 iterations are exploratory meandering rather than purposeful execution.
- **Self-check that doesn't work.** `runSelfAssessment()` returns PIVOT/SYNTHESIZE verdicts that inject a vague hint but do not structurally change loop behavior. The loop continues regardless.
- **500 iterations, zero token budget.** The agent can silently overflow the context window mid-task with no recovery.
- **Sequential tool execution.** The model emits multiple tool calls in one response; the engine executes them one by one. 2–3× latency wasted every iteration.
- **`EXHAUSTION_MARKER` leaks to users.** Non-technical users see raw internal markers, error codes, and tool names.
- **No HITL mechanism.** The engine runs to completion or exhaustion. No way to pause mid-run for user approval.
- **Intelligence systems underused.** Kuzu, GoalGraph, instinct caching, Reflexion — all exist or are implied in the codebase but are wired loosely or not at all.

**The vision:** a personal AI assistant that behaves like a capable human assistant — it plans before acting, knows when to stop and ask, recovers from failures silently, gets smarter on every interaction, and never exposes its internal machinery to non-technical users.

---

## 2. Scope — Element 6a

**In scope:**
- Two-layer architecture: `OwlOrchestrator` + `OwlEngine`
- OwlOrchestrator with 7 explicit phases (state machine)
- Intelligence Growth Loop (4 horizons: before, during, after, background)
- Six self-healing components: `HealthMonitor`, `RecoveryOrchestrator`, `QualityEvaluator`, `OutcomeJournal`, `ImprovementScheduler`, `UserFacingStatusNarrator`
- HITL interrupt mechanism with checkpoint/resume
- Parallel tool execution (`Promise.all`)
- Token budget tracking and enforcement
- Clean `TurnResult` (no text markers, typed signals)
- Schema v14: SQLite extensions + Kuzu graph nodes/edges
- Integration with ContextPipeline, Parliament, OwlEvolution, Instincts, Kuzu

**Out of scope (Element 6b):**
- Typed multimodal tool results (`{type: "image" | "audio"}`)
- Vision model routing
- Image/audio input handling in channel adapters

---

## 3. Architecture Overview — Two-Layer Model

```
src/engine/
  orchestrator.ts           ← NEW: OwlOrchestrator — intelligence layer
  runtime.ts                ← MODIFIED: OwlEngine — single-turn execution layer
  health-monitor.ts         ← NEW
  recovery-orchestrator.ts  ← NEW
  quality-evaluator.ts      ← NEW
  outcome-journal.ts        ← NEW
  improvement-scheduler.ts  ← NEW
  user-facing-narrator.ts   ← NEW
  task-ledger.ts            ← NEW
```

**OwlOrchestrator** owns intelligence: plans, monitors health, decides recovery actions, handles HITL, evaluates quality, degrades gracefully, drives the intelligence growth loop.

**OwlEngine** owns execution: one reasoning turn + tool dispatch + provider resilience. It has no opinions about whether to continue, stop, or replan. All existing resilience code stays here untouched.

**Gateway change — one line:**
```typescript
// Before
const response = await engine.run(userMessage, engineContext);
// After
const response = await orchestrator.run(userMessage, gatewayContext);
```

**Contract between layers:**

```typescript
interface TurnRequest {
  messages: ChatMessage[];
  tools: ToolDefinition[];
  model: ModelRoute;
  provider: AIProvider;
  sessionId: string;
  turnBudget: TokenBudget;
  onStreamEvent?: StreamCallback;
}

interface TurnResult {
  content: string;              // clean — no markers
  toolCalls: ToolCall[];
  toolResults: ToolResult[];
  tokensUsed: number;
  doneSignal: boolean;          // was [DONE] text marker
  budgetExhausted: boolean;     // was implicit overflow
  pendingCapabilityGap?: string;
  failedTools: FailedToolCall[];
  providerUsed: string;
  modelUsed: string;
}
```

The Engine never returns `EXHAUSTION_MARKER`, never surfaces error codes, never decides to stop. It reports facts. The Orchestrator decides what they mean.

---

## 4. OwlOrchestrator — The 7-Phase State Machine

```
PLAN → EXECUTE → ASSESS → DECIDE → [REPLAN | HITL | SYNTHESIZE | DEGRADE]
  ↑___________________________|
  (loop back if REPLAN)
```

All seven phases are named methods with clear contracts. No `if/else` spaghetti. Every intelligence decision lives in Phase 4 (DECIDE).

```typescript
async run(userMessage: string, ctx: GatewayContext): Promise<OrchestratorResponse> {
  const ledger = await this.plan(userMessage, ctx);          // Phase 1

  while (this.health.shouldContinue()) {
    const turn = await this.engine.runTurn(                  // Phase 2
      this.buildTurnRequest(ledger, ctx)
    );
    this.health.observe(turn, ledger, this.iteration++);     // Phase 3
    const decision = this.recovery.decide(                   // Phase 4
      this.health, turn, ledger, this.approachLibrary, owl.dna
    );

    if (decision === "REPLAN")   { await this.replan(ledger, turn); continue; }  // Phase 5
    if (decision === "HITL")     { return await this.hitl(ledger, turn, ctx); }
    if (decision === "CONTINUE") { continue; }
    break; // SYNTHESIZE or DEGRADE
  }

  const raw = this.compile(ledger, decision);               // Phase 6
  return this.narrate(raw, ctx);                            // Phase 7
}
```

### Phase 1 — PLAN

For any task above `simple` complexity, calls the LLM once to produce a `TaskLedger`. Skipped entirely for simple tasks (greetings, quick facts) — zero overhead.

```typescript
interface TaskLedger {
  id: string;
  goal: string;                     // restated user intent
  subGoals: SubGoal[];              // 3–7 items, stored as Kuzu DAG
  expectedOutput: string;           // what "done" looks like
  complexity: "simple" | "medium" | "complex" | "unbounded";
  estimatedTurns: number;
  behavioralConstraints: string[];  // from instinct evaluation
  parliamentContext?: string;       // from Parliament if convened
  approachPatterns: string[];       // from Kuzu graph query
  reflexionContext?: string;        // from matching Reflexion Pellets
  revisions: TaskLedgerRevision[];
  createdAt: number;
}

interface SubGoal {
  id: string;
  description: string;
  status: "pending" | "in_progress" | "done" | "blocked" | "skipped";
  dependsOn: string[];              // other SubGoal ids — enforced via Kuzu DAG
  result?: string;
}
```

At PLAN phase, the Orchestrator:
1. Queries Kuzu for `ApproachPatterns` matching this task category → injects as planning context
2. Queries ContextPipeline output for Reflexion Pellets tagged with this task category
3. Evaluates instincts (heuristic-first, LLM only if ambiguous, cached for session)
4. Convenes Parliament if `topicWorthiness.score > threshold` and Parliament enabled
5. Calls LLM to produce `TaskLedger` with full context
6. Writes SubGoal DAG to Kuzu
7. Persists ledger to SQLite `task_ledgers`

The Engine receives the ledger as a compact prompt block every turn:
```
[Current Plan]
Goal: research EVs under $40k
Progress: step 1/4 complete (model list gathered)
Current step: fetch specs for each model
Expected output: comparison table with range, charge time, price
```

### Phase 2 — EXECUTE

Assembles `TurnRequest` from current messages, tools, model route, remaining token budget, and calls `OwlEngine.runTurn()`. Returns `TurnResult`. No decisions made here.

Before assembling the `TurnRequest`, the Orchestrator queries the Kuzu DAG for all sub-goals that are currently unblocked (no incomplete dependencies). These become the active sub-goals for this turn — injected into the system prompt so the Engine knows which goals to pursue. Sub-goal parallelism happens at the tool-call level *within* the single Engine turn (via `Promise.all` on tool calls), not by running multiple Engine turns simultaneously.

```cypher
MATCH (sg:SubGoal {ledgerId: $id, status: "pending"})
WHERE NOT EXISTS {
  MATCH (blocker:SubGoal)-[:BLOCKS]->(sg)
  WHERE blocker.status <> "done"
}
RETURN sg
```

### Phase 3 — ASSESS

`HealthMonitor.observe(turn, ledger, iteration)` — updates health state, emits signals:

| Signal | Condition |
|---|---|
| `spinning` | TrajectoryStore similarity ≥ 0.7 for 2+ consecutive turns |
| `tool_blackout` | All available tools attempted and all failed |
| `budget_critical` | >80% token budget consumed, no done signal |
| `provider_unstable` | >1 provider switch in this run |
| `stall` | Same sub-goal stuck for 3+ consecutive turns |

### Phase 4 — DECIDE

`RecoveryOrchestrator.decide(health, turn, ledger, approachLibrary)` — returns exactly one of five decisions. This is the single place where all control-flow decisions live.

| Decision | When |
|---|---|
| `CONTINUE` | No signals, making progress |
| `REPLAN` | `stall` or `spinning` detected |
| `HITL` | Irreversible action pending, credential required, or ambiguity after planning |
| `SYNTHESIZE` | `doneSignal`, `budget_critical`, or `tool_blackout` with partial results |
| `DEGRADE` | All approaches exhausted, no partial results worth delivering |

DNA traits influence DECIDE thresholds:
```typescript
if (owl.dna.riskTolerance === "cautious")  hitlThreshold = "low";
if (owl.dna.challengeLevel === "high")     maxReplansBeforeDegrade = 3;
if (owl.dna.challengeLevel === "low")      maxReplansBeforeDegrade = 1;
```

### Phase 5 — REPLAN

Targeted LLM call that receives: original goal, current ledger (what was tried), failure summary (one sentence per failed approach — no raw errors), what remains unknown. Returns revised `TaskLedger`. Old ledger preserved in `revisions[]` — replanner never regenerates a failed plan.

### Phase 6 — SYNTHESIZE or DEGRADE

**SYNTHESIZE:** compile all sub-goal results from `TaskLedger` into a coherent answer. Passes through `QualityEvaluator.evaluateSync()`. If score < 0.3 → escalate to DEGRADE.

**DEGRADE:** `UserFacingStatusNarrator.narrateDegradation(tier, partialResult, obstacle, nextStep)` builds the response at the appropriate tier:

| Tier | Condition | What user sees |
|---|---|---|
| 1 | score > 0.6 | Answer + optional soft check-in |
| 2 | score 0.3–0.6 | What I got + named gap + one next step |
| 3 | scope failure or credential required | What I understood + what I need from you |
| 4 | all approaches exhausted | What you can do instead + precise steps |

### Phase 7 — NARRATE

`UserFacingStatusNarrator.postProcess(response, qualitySignal)` runs on every response:
1. Strip all internal markers (`EXHAUSTION_MARKER`, `[CAPABILITY_GAP:...]`, `[SYSTEM:...]`)
2. Translate jargon (`"HTTP 429"` → deleted, `"tool failed"` → `"ran into a snag"`)
3. Apply owl persona tone from DNA
4. Write to `OutcomeJournal`

Non-technical users never see: error codes, tool names, provider names, iteration counts, token limits. This contract is enforced here — not upstream.

---

## 5. Intelligence Growth Loop — 4 Horizons

### Horizon 1 — Before Every Run

**Instincts (redesigned — near-zero overhead):**
```
1. Keyword scoring (0ms)   → catches ~80% of cases
2. Cosine similarity (5ms) → catches nuanced cases  
3. LLM classification      → only when confidence 40–70% (~5% of turns)
4. Cached for entire session
```

Active instincts injected into `TaskLedger.behavioralConstraints`. Travel with the plan, not just the first prompt.

**ApproachPatterns from Kuzu at PLAN phase:**
```cypher
MATCH (tc:TaskCategory {name: $category})-[:SUCCEEDS_WITH]->(ts:ToolSequence)
WHERE ts.successRate > 0.6 AND ts.observationCount > 3
RETURN ts ORDER BY ts.successRate DESC LIMIT 3
```

**DNA as first-class Orchestrator input:** not just a style directive in text, but actual thresholds driving DECIDE behavior.

### Horizon 2 — During Every Run

- **Skills** from `EvolutionEngine` are registered in `ToolRegistry` — available as tools without wiring changes
- **Pellets** re-queried per iteration (existing) + queried at PLAN phase (new)
- **Inner monologue** (ContextPipeline, Element 5) used at PLAN phase — owl continues from last-turn thoughts
- **TaskLedger block** in every Engine turn system prompt — owl always knows the plan

### Horizon 3 — After Every Run (non-blocking, fires after gateway responds)

**Reflexion Critique:**
One async LLM call (cheap/fast model). Input: goal + TaskLedger + outcome + quality score. Output: one paragraph — what worked, what failed, what to do differently. Stored as a Reflexion Pellet tagged `#reflexion #[task-category]`.

The `CONTRADICTS` Kuzu edge handles outdated lessons: when a new reflexion contradicts an old one, the edge is written. ContextPipeline queries only pellets with no outgoing `CONTRADICTS` edge (most recent valid lessons only).

**OwlEvolutionEngine — quality-driven DNA mutation:**
```
qualityScore > 0.8  → reinforce current traits
qualityScore 0.4–0.8 → minor drift toward what worked
qualityScore < 0.4  → mutate away from what failed
followUpSentiment = "correction" → immediate tone/verbosity mutation
followUpSentiment = "positive"   → lock current tone as reinforced preference
```

**EpisodicMemory + FactExtractor:** triggered as before (Element 3). Orchestrator tags which moments are worth episodic storage: quality spike (breakthrough), user correction, task completion.

**PelletGenerator:** triggered for sessions with `qualityScore > 0.85` and `complexity != "simple"`.

### Horizon 4 — Background Idle

`ImprovementScheduler` — registered at bootstrap, runs only when no session is active, respects `quietHours`:

| Job | Cadence | LLM calls | What it does |
|---|---|---|---|
| Journal Review | Every 15 min (if ≥5 new entries) | 0 | Aggregates failures → writes `ApproachPattern` nodes in Kuzu |
| Approach Pruning | Every hour | 0 | Archives stale patterns; promotes proven ones |
| APO Trigger | Every 24h | 6–10 | Runs `PromptOptimizer` against worst 3 trajectories |

APO output: winning system prompt candidate written to `owl.dna.systemPromptOverride`. Base prompt gets better over time without any user action.

---

## 6. Self-Healing Components

### HealthMonitor — `src/engine/health-monitor.ts`

Stateful per-run. Called at Phase 3 after every Engine turn. Wraps existing `TrajectoryStore` and `AttemptLog` into one observable health object.

```typescript
class HealthMonitor {
  observe(turn: TurnResult, ledger: TaskLedger, iteration: number): HealthSignal[];
  shouldContinue(): boolean;
  getHealth(): RunHealth;
}

interface RunHealth {
  iteration: number;
  tokensConsumed: number;
  tokenBudget: number;
  consecutiveFailures: number;
  uniqueToolsAttempted: Set<string>;
  allToolsFailed: boolean;
  spinningDetected: boolean;
  providerSwitchCount: number;
  stuckOnSubGoalId: string | null;
  signals: HealthSignal[];
}
```

### RecoveryOrchestrator — `src/engine/recovery-orchestrator.ts`

Pure function. No state. Takes health + turn result + ledger + approach library, returns one of the 5 DECIDE outcomes. Replaces all scattered `if/else` in `runtime.ts`. ~20 lines of explicit decision logic, fully testable.

```typescript
function decide(
  health: RunHealth,
  turn: TurnResult,
  ledger: TaskLedger,
  approachLibrary: ApproachLibrary,
  dna: OwlDNA,
): Decision
```

### QualityEvaluator — `src/engine/quality-evaluator.ts`

**Sync** (< 1ms, runs before delivery, no LLM):

Score starts at 1.0:
- `loopExhausted` → −0.30
- response contains raw error patterns → −0.30
- response < 50 chars for non-trivial task → −0.25
- response > 2,000 chars for short question → −0.15
- contains `EXHAUSTION_MARKER` → −0.40, stripped immediately
- all tools succeeded → +0.10
- structured table/list matching task type → +0.10
- ends with clear next action → +0.05

**Async** (post-delivery, non-blocking, only for scores < 0.5):
Single cheap LLM call: did the response address the user's core need? FULLY / PARTIALLY / MINIMALLY / NOT_AT_ALL. Updates `OutcomeJournal`.

**Follow-up update** (on next user message):
Keywords `"that's not"`, `"not what I"`, `"wrong"`, `"actually I meant"` → `followUpSentiment = "correction"`. Natural continuation or `"thanks"` → `followUpSentiment = "positive"`. Both update the journal retrospectively.

### OutcomeJournal — `src/engine/outcome-journal.ts`

Extends existing `trajectories` SQLite table. One record per run — success and failure alike. Source of truth for `ImprovementScheduler`.

New fields: `quality_score`, `quality_flags`, `task_category`, `task_complexity`, `degradation_tier`, `recovery_actions`, `follow_up_sentiment`, `follow_up_updated_at`.

### ImprovementScheduler — `src/engine/improvement-scheduler.ts`

Three background jobs (see Horizon 4 above). Registered once at bootstrap via `improvementScheduler.start()`. No changes to existing config structure. Total daily budget: ~20 LLM calls (equivalent to 3–5 normal conversations).

### UserFacingStatusNarrator — `src/engine/user-facing-narrator.ts`

Three responsibilities:

**1. Real-time progress** (emitted via `onStreamEvent`):
```typescript
const STATUS: Record<InternalState, string[]> = {
  tool_executing:       ["Looking into this...", "On it..."],
  tool_failed_retrying: ["Let me try another way...", "Checking a different source..."],
  switching_approach:   ["Taking a fresh approach..."],
  provider_switching:   ["Just a moment..."],   // never mention provider
  compiling_results:    ["Putting this together...", "Almost there..."],
};
```

**2. Degradation templates** (Tiers 1–4 — see Section 4, Phase 6).

**3. `postProcess()`** — runs on every response before delivery:
- Strip all internal markers
- Jargon translation map (configurable defaults): `"HTTP 4xx"` → deleted, `"API"` → `"the service"`, `"tool failed"` → `"ran into a snag"`, `"timeout"` → `"took too long to respond"`, `"429"` → deleted
- Apply owl DNA verbosity + tone

---

## 7. HITL — Human-in-the-Loop Interrupt

### HitlChannel contract

```typescript
interface HitlChannel {
  pause(request: HitlRequest): Promise<HitlResponse>;
}

interface HitlRequest {
  kind: "approval" | "clarification" | "choice";
  memo: {
    whatIDid: string;
    whatINeed: string;
    options?: string[];
    recommendation?: string;
  };
  ledgerSnapshot: TaskLedger;
  pendingAction: string;
}

interface HitlResponse {
  approved: boolean;
  choice?: string;
  freeText?: string;
  timedOut: boolean;
}
```

### Four HITL triggers (decided by RecoveryOrchestrator)

| Trigger | Example | Timeout behavior |
|---|---|---|
| Irreversible action | Delete files, send email, git push | Block — do not proceed |
| Credential required | Login wall, API key | Offer handoff instructions |
| Ambiguous fork | Two equal interpretations | Proceed with safest, note assumption |
| Low confidence after REPLAN | Scope still unclear | Proceed with narrowest scope |

### Checkpoint/resume

On HITL pause, full `TaskLedger` + pending action persisted to `hitl_checkpoints` SQLite table. On user response (any time, even days later), Orchestrator restores ledger and resumes from exact decision point. Enables multi-day long-horizon tasks.

### Per-adapter implementation

Each channel adapter implements one method: `createHitlChannel()`. Orchestrator is channel-agnostic.

```
CLI         → readline prompt mid-stream
Telegram    → sends message, waits for reply on same chat
Web         → SSE pause event → client shows modal → POST response
Slack       → interactive button message → action webhook
```

---

## 8. OwlEngine Changes

### Change 1 — Parallel tool execution

```typescript
// Sequential → parallel (Promise.all)
const results = await Promise.all(
  toolCalls
    .filter(tc => !tc.sequential)     // most tools
    .map(tc => toolRegistry.execute(tc.name, tc.arguments, ctx))
);
// Sequential tools (computer_use, edit_file chains) batched separately
```

`sequential: boolean` flag added to `ToolDefinition`. Default: `false`.

### Change 2 — Token budget enforcement

`TurnRequest` carries `TokenBudget`. Engine tracks spend. Two injection points:

- **50% budget:** system prompt addendum — *"Be concise in reasoning, prioritize essential tool calls."*
- **85% budget:** system prompt addendum — *"Budget nearly exhausted. Synthesize what you have and emit [DONE]."*
- **100% budget:** Engine stops LLM calls, returns `TurnResult` with `budgetExhausted: true`. Orchestrator handles as SYNTHESIZE — never a crash.

### Change 3 — Clean TurnResult

All text markers stripped at Engine boundary. Signals become typed fields on `TurnResult` (see Section 3). `EXHAUSTION_MARKER` is removed from the codebase entirely.

### What stays untouched

- `withProviderResilience()` — 3-layer failover
- `DiagnosticEngine` integration — multi-hypothesis error analysis
- `TOOL_FALLBACKS` graph — well-tested fallback chains
- Cross-provider hot-swap mid-turn
- `ApproachLibrary` injection at session start (also now at PLAN phase)
- History sanitization (stale tool refs)
- Context compression
- DNA-driven tool prioritization (now validated against actual registry)

### Lines removed from Engine

- Outer `while` loop and control-flow decisions (move to Orchestrator)
- `runSelfAssessment()` (replaced by HealthMonitor + ASSESS phase)
- `EXHAUSTION_MARKER` generation
- Inline PIVOT/SYNTHESIZE hint injections
- Post-loop gap detection (moves to Orchestrator post-run)
- `loopExhausted` flag (replaced by `TurnResult.budgetExhausted`)

Estimated result: runtime.ts shrinks from ~3,300 lines to ~2,000.

---

## 9. Data Model — Schema v14

### SQLite additions

**`trajectories` table — extended (nullable columns, safe migration):**
```sql
ALTER TABLE trajectories ADD COLUMN quality_score REAL DEFAULT NULL;
ALTER TABLE trajectories ADD COLUMN quality_flags TEXT DEFAULT '[]';
ALTER TABLE trajectories ADD COLUMN task_category TEXT DEFAULT NULL;
ALTER TABLE trajectories ADD COLUMN task_complexity TEXT DEFAULT NULL;
ALTER TABLE trajectories ADD COLUMN degradation_tier INTEGER DEFAULT 1;
ALTER TABLE trajectories ADD COLUMN recovery_actions TEXT DEFAULT '[]';
ALTER TABLE trajectories ADD COLUMN follow_up_sentiment TEXT DEFAULT NULL;
ALTER TABLE trajectories ADD COLUMN follow_up_updated_at TEXT DEFAULT NULL;
```

**New: `task_ledgers`**
```sql
CREATE TABLE task_ledgers (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  sub_goals TEXT NOT NULL,       -- JSON: SubGoal[]
  expected_output TEXT NOT NULL,
  complexity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  revisions TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_ledgers_session ON task_ledgers(session_id);
CREATE INDEX idx_ledgers_user_status ON task_ledgers(user_id, status);
```

**New: `hitl_checkpoints`**
```sql
CREATE TABLE hitl_checkpoints (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  ledger_id TEXT NOT NULL,
  pending_action TEXT NOT NULL,
  request_kind TEXT NOT NULL,
  memo_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting',
  response_json TEXT DEFAULT NULL,
  created_at TEXT NOT NULL,
  resolved_at TEXT DEFAULT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX idx_hitl_session ON hitl_checkpoints(session_id, status);
```

**New: `approach_patterns`** (denormalized Kuzu cache — fast read on hot path)
```sql
CREATE TABLE approach_patterns (
  id TEXT PRIMARY KEY,
  task_category TEXT NOT NULL,
  lesson TEXT NOT NULL,
  successful_sequences TEXT NOT NULL DEFAULT '[]',
  conditions TEXT NOT NULL DEFAULT '[]',
  observation_count INTEGER NOT NULL DEFAULT 0,
  success_rate REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL DEFAULT 'tentative',
  last_used_at TEXT DEFAULT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_patterns_category_status ON approach_patterns(task_category, status);
```

### Kuzu additions

**New node types:**
```
TaskCategory   { name: STRING, observationCount: INT64 }
ToolSequence   { id: STRING, tools: STRING[], successRate: DOUBLE, observationCount: INT64 }
SubGoal        { id: STRING, ledgerId: STRING, description: STRING, status: STRING, result: STRING }
ReflexionPellet{ id: STRING, pelletId: STRING, taskCategory: STRING, createdAt: INT64 }
Trajectory     { id: STRING, sessionId: STRING, outcome: STRING, qualityScore: DOUBLE }
```

**New edge types:**
```
SUCCEEDS_WITH  (TaskCategory → ToolSequence)
FAILS_ON       (TaskCategory → ToolSequence)
USES           (ToolSequence → Tool)
HAS_OUTCOME    (ToolSequence → { successRate: DOUBLE })
BLOCKS         (SubGoal → SubGoal)
APPLIES_TO     (ReflexionPellet → TaskCategory)
APPLIES_TO     (ReflexionPellet → Tool)
GENERATED_FROM (ReflexionPellet → Trajectory)
CONTRADICTS    (ReflexionPellet → ReflexionPellet)
LEARNED_FROM   (TaskCategory → Trajectory)
```

**Reflexion Pellets** use the existing `pellets` SQLite table with tag `#reflexion`. The Kuzu `ReflexionPellet` node references `pelletId` for rich relationship queries. `CONTRADICTS` edges enable ContextPipeline to query only the most recent valid lessons.

---

## 10. Integration Points

| System | Change | Detail |
|---|---|---|
| `core.ts` | One line | `engine.run()` → `orchestrator.run()` |
| ContextPipeline (E5) | Consumed at PLAN phase | Reflexion pellets + UserPersona inform the plan |
| Parliament | Planning tool | Convened at PLAN phase if topic worthy; synthesis → `TaskLedger.parliamentContext` |
| Instincts | Heuristic-first, session-cached | Evaluated once at PLAN; cached until session end |
| OwlEvolutionEngine | Quality-driven mutation | Reads `evolutionSignals` from `OrchestratorResponse` |
| PelletGenerator | Reflexion pellets | Triggered post-run for `qualityScore > 0.85` + `complexity != "simple"` |
| ImprovementScheduler | Bootstrap registration | `improvementScheduler.start()` — three background jobs |
| Channel adapters | `createHitlChannel()` | One new method per adapter |
| Kuzu | ApproachPattern graph + SubGoal DAG | Finally used for graph-shaped data |
| Element 6b | Clean boundary | `TurnResult` typed — multimodal extension requires no Orchestrator changes |

---

## 11. What Is Explicitly Not Built Here

- Multimodal typed tool results (Element 6b)
- Vision model routing (Element 6b)
- Genetic crossover between owls (Evolution element)
- Synthetic self-practice scenarios (deferred — needs 30+ days OutcomeJournal data first)
- Multi-agent quality review for every response (Parliament is already expensive; reserve for scheduled reviews)
- Fine-grained per-user trust scoring (UserPersonaSynthesizer from Element 5 is sufficient)

---

## 12. Success Criteria

| Metric | Target |
|---|---|
| Complex task planning phase | TaskLedger generated for >95% of non-simple tasks |
| Tool execution latency | 2–3× improvement on multi-tool responses (parallel execution) |
| EXHAUSTION_MARKER user exposure | Zero — stripped at Engine boundary |
| Jargon leakage to users | Zero — enforced by UserFacingStatusNarrator |
| HITL checkpoint/resume | Full ledger state restored after any-length pause |
| OutcomeJournal coverage | 100% of runs (success + failure) |
| ImprovementScheduler | Zero LLM calls for Journal Review + Pruning jobs |
| Test coverage on new components | ≥85% — each component independently testable |
| runtime.ts line count | ≤2,000 (from 3,300) |
