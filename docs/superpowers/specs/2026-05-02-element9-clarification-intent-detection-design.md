# Element 9: Clarification & Intent Detection — Design Spec

**Date:** 2026-05-02
**Status:** Approved for implementation
**Authors:** PM (John), Architect (Winston), BA (Mary), Market Research
**Replaces:** `pre-execution-confirmer.ts`, `unclarity-surfacer.ts`, `ambiguity-detector.ts`

---

## Executive Summary

StackOwl's clarification pipeline is broken. Three of its five modules use hardcoded regex patterns and fixed numeric thresholds to decide when to ask the user a question. The result: a clear research request ("can you do research about zimaboard 2, tell me where i can use?") gets echoed back verbatim with "Confidence: 60%, Did I understand correctly?" — the worst possible experience for a personal AI assistant.

The fix is architectural, not cosmetic. This spec:
1. Deletes three regex-based modules and replaces them with a single LLM-based `IntentClassifier`
2. Establishes a unified `ClarificationGate` as the only decision authority for `gateway/core.ts`
3. Adds a per-user learning loop that adapts clarification behavior to each user's preference via `OwlDNA` and `trajectories`
4. Establishes the **Intelligence-First Principle** as a cross-element standard: no hardcoded classification, routing, or intent logic anywhere in the codebase

---

## Problem Statement

### Confirmed Production Bug

The owl's `PreExecutionConfirmer.calculateConfidence()` method applies a -0.4 confidence penalty to any message matching:

```regex
/\b(?:which|what|who|where|when|how)\b.*\?\s*$/i
```

"where i can use?" matches this pattern → confidence drops to 60% → falls below 65% execution threshold → owl echoes the user's message and asks for confirmation.

This is wrong in every dimension:
- The word "where" in a question is not a signal of ambiguity — it's normal English
- No context is consulted (recent messages, user history, DNA preferences)
- The regex fires synchronously, before any LLM reasoning
- Five independent modules can each interrupt the user with no coordination

### Root Cause Scope

| Module | Problem |
|--------|---------|
| `pre-execution-confirmer.ts` | Regex confidence scoring. **Root cause.** |
| `unclarity-surfacer.ts` | Regex pattern list for "user expressing confusion." Fires independently. |
| `ambiguity-detector.ts` | LLM-based but hardcoded threshold (0.75), no DNA/trajectory consultation |
| `clarification-coordinator.ts` | Word-overlap Jaccard similarity for dedup — brittle on paraphrasing |
| `ambient-collector.ts` | Scores user patterns with hardcoded weights, output never read by classifier |

### Industry Context

ChatGPT, Claude, and Gemini all shifted to **"proceed with best interpretation"** — they almost never ask for confirmation on clear requests. Production intent classification uses BERT + LLM hybrid, not regex. The white space: **no production AI assistant learns per-user clarification preferences.** StackOwl can own this.

---

## Intelligence-First Principle

> **Every decision about intent, routing, ambiguity, confirmation, or behavioral threshold in StackOwl MUST flow through `IntelligenceRouter`, `OwlDNA.learnedPreferences`, or the `trajectories` reward signal. Hardcoded keyword lists, regex classifiers, and fixed numeric thresholds are forbidden as decision gates.**

This principle applies retroactively to all elements (E1–E8) and must be upheld in all future elements (E10+). Element 9 is the first complete implementation of this principle. Cross-element violations identified during the E9 audit are catalogued in Appendix A and scheduled for remediation in their respective elements.

The three permitted sources of ground truth:
- **`IntelligenceRouter.resolve("classification")`** — cheap-tier LLM model (<400ms) for any intent/ambiguity classification
- **`OwlDNA.learnedPreferences`** — per-user, per-owl learned preferences that bias LLM prompts (not threshold gates)
- **`trajectories` reward signal** — outcome data from past interactions, read by evolution engine to update DNA

---

## Architecture

### Component Map

| Module | Action | Reason |
|--------|--------|--------|
| `pre-execution-confirmer.ts` | **DELETE** | Regex root cause |
| `unclarity-surfacer.ts` | **DELETE** | Regex pattern list |
| `ambiguity-detector.ts` | **DELETE** | Replaced by `IntentClassifier` |
| `clarification-coordinator.ts` | **REWRITE** | Remove Jaccard similarity; absorb confirmation lifecycle from deleted PreExecutionConfirmer |
| `pre-action-questioner.ts` | **EDIT** | Replace raw `ModelProvider.chat()` with `IntelligenceRouter.resolve("clarification")` |
| `ambient-collector.ts` | **EDIT** | Wire output to `IntentClassifier` context (currently ignored) |
| `types.ts` | **EXTEND** | Delete `PreExecutionConfirmation`; add `IntentVerdict`, `IntentClassification`, `PolicyDecision` |

### New Components

#### `src/clarification/intent-classifier.ts` — IntentClassifier

Single responsibility: classify a user message into a verdict using the cheap LLM tier.

```typescript
export type IntentVerdict =
  | "PROCEED"               // clear actionable request, execute immediately
  | "NARRATE"               // proceed but echo interpretation (mild uncertainty)
  | "CONFIRM"               // genuinely multi-path, ask before executing
  | "CONFIRM_IRREVERSIBLE"; // tool is destructive — always confirm

export interface IntentClassification {
  verdict: IntentVerdict;
  confidence: number;        // 0.0–1.0 from LLM
  interpretation: string;    // one-line restatement of what the user wants
  ambiguityReason?: string;  // why CONFIRM was chosen, if applicable
}

export class IntentClassifier {
  constructor(
    private router: IntelligenceRouter,
  ) {}

  async classify(
    message: string,
    context: string[],
    dna: OwlDNA,
  ): Promise<IntentClassification>;
}
```

**Classification prompt (≤150 tokens):**

```
Classify this user message for an AI assistant.

Message: "{message}"
Context (last 3 turns): {context}
User autonomy score (0=always ask, 1=never ask): {autonomy_score}

Reply with JSON only:
{"verdict":"PROCEED|NARRATE|CONFIRM","confidence":0.0-1.0,"interpretation":"one sentence","ambiguityReason":null}

PROCEED = clear actionable request.
NARRATE = proceed but echo your interpretation.
CONFIRM = genuinely multi-path with no safe default.
```

`autonomy_score` is read from `dna.learnedPreferences["clarification_autonomy_score"]` (float 0–1, default 0.5 if absent). This injects the user's learned preference directly into the LLM context — no hardcoded threshold gate.

#### `src/clarification/policy-engine.ts` — ClarificationPolicyEngine

Stateless pure function. Translates `IntentClassification + OwlDNA + isIrreversibleTool` into an action. The **only** place clarification thresholds live.

```typescript
export interface PolicyDecision {
  action: "PROCEED" | "NARRATE" | "ASK";
  narrateWith?: string;   // populated when action = NARRATE
  question?: string;      // populated when action = ASK
}

export class ClarificationPolicyEngine {
  decide(
    classification: IntentClassification,
    autonomyScore: number,
    isIrreversibleTool: boolean,
  ): PolicyDecision;
}
```

Decision table (evaluated top-to-bottom, first match wins):

| Condition | Action |
|-----------|--------|
| `isIrreversibleTool = true` AND `verdict ≠ PROCEED` | **ASK** — always confirm destructive actions |
| `verdict = PROCEED` AND `confidence ≥ 0.7` | **PROCEED** |
| `verdict = PROCEED` AND `confidence < 0.7` | **NARRATE** — act with interpretation echoed |
| `verdict = NARRATE` | **NARRATE** |
| `verdict = CONFIRM` AND `autonomyScore ≥ 0.7` | **NARRATE** — high-autonomy users: act anyway |
| `verdict = CONFIRM` AND `autonomyScore < 0.7` | **ASK** |

ZimaBoard case path: `PROCEED, confidence ~0.95` → row 2 → **PROCEED**, no question asked.

**How `isIrreversibleTool` is determined:** Tool definitions carry an `ExecutionPolicy.irreversible?: boolean` flag (added in Element 7's Tool Quality pass). `ClarificationGate.evaluate()` receives `activeToolNames: string[]` from the gateway, looks up each tool's `ExecutionPolicy` in `ToolRegistry`, and sets `isIrreversibleTool = true` if any active tool has `irreversible: true`. No hardcoded tool name lists. Tools self-declare their destructiveness in their definition.

#### `src/clarification/gate.ts` — ClarificationGate

Thin facade. The **only** public entry point from `gateway/core.ts`. Replaces four separate module calls.

```typescript
export class ClarificationGate {
  constructor(
    private classifier: IntentClassifier,
    private policyEngine: ClarificationPolicyEngine,
    private coordinator: ClarificationCoordinator,
    private preActionQuestioner: PreActionQuestioner,
  ) {}

  async evaluate(
    message: string,
    context: string[],
    dna: OwlDNA,
    activeTools: string[],   // to detect irreversible tools
  ): Promise<PolicyDecision>;
}
```

`evaluate()` calls `classifier.classify()`, then `policyEngine.decide()`, then checks `coordinator.shouldSuppressQuestion()` (dedup window). Returns a single `PolicyDecision`.

---

### Async Integration

`gateway/core.ts` currently calls four separate clarification modules (lines 1689–1735). Replaced with a single `await`:

```typescript
// BEFORE (multiple sync/async calls, scattered):
const ambiguityResult = await this.ambiguityDetector.detectAmbiguity(message.text);
const confirmation    = this.preExecutionConfirmer.assessRequest(message.text); // sync, regex
// ...

// AFTER (single await, before ReAct loop):
const policyDecision = await this.clarificationGate.evaluate(
  message.text,
  session.messages.slice(-3).map(m => m.content),
  this.ctx.owl.dna,
  this.ctx.activeToolNames ?? [],
);

if (policyDecision.action === "ASK") {
  await this.db.trajectories.markClarificationAsked(trajectoryId);
  return this.buildResponse(policyDecision.question!, owlMeta);
}
if (policyDecision.action === "NARRATE") {
  // prepend interpretation to response — handled post-execution
  session.pendingNarration = policyDecision.narrateWith;
}
// fall through to tool selection and ReAct loop
```

Latency: `IntelligenceRouter.resolve("classification")` targets <400ms. This is the only new await in the hot path. The gateway is already `async` — no architectural change required.

---

### Per-User Learning Loop

**Signal source:** `trajectories` table, new column `clarification_asked INTEGER DEFAULT 0`.

Set to `1` when `policyDecision.action === "ASK"` is returned, `0` when proceeding.

**Algorithm** (runs in existing `OwlEvolutionEngine` batch cycle):

```typescript
// In evolution.ts, called after existing DNA mutation:
async function updateClarificationAutonomy(owlName: string, db: StackOwlDB) {
  const recent = db.trajectories.getRecent(owlName, 50);
  const askedReward    = avg(recent.filter(t => t.clarification_asked).map(t => t.reward));
  const proceededReward = avg(recent.filter(t => !t.clarification_asked).map(t => t.reward));
  const delta = proceededReward - askedReward; // positive = user prefers proceeding
  const current = owl.dna.learnedPreferences["clarification_autonomy_score"] ?? 0.5;
  const next = clamp(current + 0.1 * Math.sign(delta), 0.1, 0.9);
  owl.dna.learnedPreferences["clarification_autonomy_score"] = next;
}
```

**Cold start:** `learnedPreferences["clarification_autonomy_score"]` absent → defaults to 0.5 (balanced). No special path needed. After ≥5 trajectories with `clarification_asked` data, the first evolution batch adjusts it.

**Decay:** `OwlEvolutionEngine` already applies preference decay. Clarification autonomy score inherits this — the owl forgets old patterns naturally.

---

### Schema — Version 19

```sql
-- v19: track clarification decisions for learning loop
ALTER TABLE trajectories ADD COLUMN clarification_asked INTEGER DEFAULT 0;
-- 1 = owl returned a question instead of executing
-- 0 = owl proceeded (with or without narration)
```

Bump `SCHEMA_VERSION` in `src/memory/db.ts`: `17 → 18` (E8, already done) → **`18 → 19`** (E9).

Add `markClarificationAsked(trajectoryId: string): void` helper to `TrajectoriesRepo`.

---

## File Map

| File | Action | Description |
|------|--------|-------------|
| `src/clarification/intent-classifier.ts` | **CREATE** | LLM classifier via IntelligenceRouter; reads OwlDNA autonomy score |
| `src/clarification/policy-engine.ts` | **CREATE** | Stateless decision table; 6 rows, no hardcoded thresholds |
| `src/clarification/gate.ts` | **CREATE** | Single facade entry point for gateway; orchestrates classifier + policy |
| `src/clarification/coordinator.ts` | **REWRITE** | Remove Jaccard word overlap; absorb confirmation lifecycle from deleted PreExecutionConfirmer; keep 5-min dedup window |
| `src/clarification/pre-action-questioner.ts` | **EDIT** | Replace raw `ModelProvider.chat()` with `IntelligenceRouter.resolve("clarification")` |
| `src/clarification/ambient-collector.ts` | **EDIT** | Wire ambient context output into `IntentClassifier.classify()` context param (currently ignored) |
| `src/clarification/types.ts` | **EDIT** | Delete `PreExecutionConfirmation`; add `IntentVerdict`, `IntentClassification`, `PolicyDecision` |
| `src/clarification/index.ts` | **EDIT** | Remove deleted exports; add `ClarificationGate`, `IntentClassifier`, `ClarificationPolicyEngine` |
| `src/clarification/pre-execution-confirmer.ts` | **DELETE** | Regex root cause |
| `src/clarification/unclarity-surfacer.ts` | **DELETE** | Regex pattern list |
| `src/clarification/ambiguity-detector.ts` | **DELETE** | Replaced by IntentClassifier |
| `src/gateway/core.ts` | **EDIT** | Lines 1689–1735: replace 4 module calls with `await this.clarificationGate.evaluate(...)` |
| `src/memory/db.ts` | **EDIT** | Bump to v19; add v19 migration; add `markClarificationAsked()` to TrajectoriesRepo |
| `src/owls/evolution.ts` | **EDIT** | Add `updateClarificationAutonomy()` in batch cycle |
| `__tests__/clarification/intent-classifier.test.ts` | **CREATE** | ZimaBoard PROCEED, destructive CONFIRM_IRREVERSIBLE, cold-start, high-autonomy paths |
| `__tests__/clarification/policy-engine.test.ts` | **CREATE** | All 6 decision rows; autonomy gradient tests |
| `__tests__/clarification/gate.test.ts` | **CREATE** | Integration: coordinator dedup, irreversible tool override, narration path |

---

## User Stories (MVP)

| Priority | Story |
|----------|-------|
| P0 | Clear research/action requests execute immediately — no echo, no confirmation |
| P0 | Destructive tool calls (file delete, send message) always confirm before executing |
| P1 | Genuinely ambiguous requests get one focused question (not an echo summary) |
| P1 | Clarification question suppressed if semantically identical to one asked <5 min ago |
| P2 | Per-user autonomy score adapts within one evolution batch (≤N conversations) |
| P3 | `/status` shows clarification rate (last 30 days) |
| P3 | `/quiet` command sets `clarificationStyle = 'silent'` in DNA |

---

## Acceptance Criteria

**AC-1 — No regex in classification paths.** `grep -r "ambiguousPatterns\|/\\\\b.*\\\\b/i" src/clarification/` returns zero results post-merge.

**AC-2 — ZimaBoard test is green.**

```typescript
it("does not ask for research requests", async () => {
  const result = await gate.evaluate(
    "can you do research about zimaboard 2, tell me where i can use?",
    [], mockDna, []
  );
  expect(result.action).toBe("PROCEED");
});
```

**AC-3 — Destructive tool always confirms.** `evaluate("delete all logs", [], dna, ["WriteFile"])` with `isIrreversibleTool=true` returns `{ action: "ASK" }`.

**AC-4 — High-autonomy user never blocked on CONFIRM.** With `autonomy_score = 0.9`, even a `CONFIRM` verdict returns `NARRATE`, not `ASK`.

**AC-5 — `clarification_asked` column populates.** After a session where owl asks one question, `trajectories WHERE clarification_asked = 1` has ≥1 row.

**AC-6 — Evolution batch updates autonomy score.** Seed 20 trajectories (15 with `clarification_asked=0, reward=0.9` and 5 with `clarification_asked=1, reward=0.4`). Run evolution batch. Assert `learnedPreferences["clarification_autonomy_score"] > 0.5`.

**AC-7 — No regression.** All existing tests pass (633 baseline).

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Clarification rate | < 5% of turns |
| False positive rate (clear requests asked) | < 1% |
| Task completion rate without interruption | > 95% |
| Clarification autonomy score adaptation | Within first evolution batch after 5+ interactions |

---

## Non-Goals (Element 9)

- BERT/embedding-based intent classification (LLM via IntelligenceRouter is sufficient for MVP)
- Multi-turn clarification dialogue (one exchange max)
- Voice/audio UX adaptations
- Rewriting instincts engine, heartbeat, or other elements (tracked in Appendix A)

---

## Appendix A: Intelligence-First Cross-Element Audit

Full codebase audit conducted during Element 9 design. **66 hardcoded thresholds, 4 unwired components, and 3 dead code instances** found across 13 files. Each violation is classified by severity and assigned to its owning element for remediation.

### Summary by Module

| Module | Hardcoded Thresholds | Dead Code | Unwired | Severity |
|--------|---------------------|-----------|---------|----------|
| `src/clarification/` (all 5 files) | 15 | 0 | 1 | HIGH — **fixed in E9** |
| `src/engine/router.ts` + `runtime.ts` | 22 | 0 | 2 | CRITICAL — E7/E10 |
| `src/owls/decision-layer.ts` | 9 | 0 | 0 | HIGH — E10 |
| `src/heartbeat/proactive.ts` | 9 | 3 | 0 | MEDIUM — E11 |
| `src/owls/evolution.ts` | 7 | 0 | 0 | MEDIUM — E10 |
| `src/instincts/engine.ts` | 2 | 0 | 0 | MEDIUM — E10 |
| `src/parliament/orchestrator.ts` | 2 | 0 | 0 | LOW — E12 |
| `src/tools/registry.ts` | 0 | 0 | 1 | MEDIUM — E7 |
| **TOTAL** | **66** | **3** | **4** | |

### Critical Unwired Components

These components exist, produce output, and have their output **ignored by all consumers**:

| Component | Written By | Read By | Impact |
|-----------|-----------|---------|--------|
| `OwlDNA.delegationPreference` | `evolution.ts` | **NOWHERE** | User's confirmed delegation style never affects clarification decisions |
| `FallbackSequencer` | `runtime.ts:1518` | **NOWHERE** | Learned fallback sequences never consulted — `TOOL_FALLBACKS` hardcoded map used instead |
| `FallbackDiscoverer` | `runtime.ts:1521` | **NOWHERE** | Discovered new fallback paths are recorded and discarded |
| `ToolIntentRouter` | `registry.ts` | **NOWHERE** | Registered but never called during tool execution |

### IntelligenceRouter Underuse

`IntelligenceRouter` has **9 task types defined** but is only used in one location (`gateway/core.ts` for model selection). Task types `"classification"` and `"clarification"` exist in the router definition but have **zero call sites**. Element 9 fixes this for clarification. Future elements must route through the router rather than adding new hardcoded logic.

### Key Violations by Element

**E9 (this element) — clarification/ — FIXED:**
- `pre-execution-confirmer.ts`: regex confidence scoring, 3 hardcoded thresholds → **DELETED**
- `unclarity-surfacer.ts`: 8 hardcoded regex patterns → **DELETED**
- `ambiguity-detector.ts`: hardcoded `AMBIGUITY_THRESHOLD = 0.75`, raw provider instead of IntelligenceRouter → **DELETED**
- `coordinator.ts`: Jaccard word-overlap similarity `>= 0.7` → **REWRITTEN**
- `OwlDNA.delegationPreference`: never read → **WIRED** via `clarification_autonomy_score`

**E7 (Tool Cortex) — runtime.ts + registry.ts — BACKLOG:**
- `runtime.ts:417-442`: `isFailureDetected()` uses 9 hardcoded failure string prefixes — should use `IntelligenceRouter.resolve("classification")`
- `runtime.ts:450-487`: `classifyToolError()` uses 18 hardcoded patterns (transient vs non-retryable) — should read `trajectories` per-tool success rates
- `runtime.ts:1123-1132`: `TOOL_FALLBACKS` hardcoded 8-entry map — should read from `FallbackSequencer`/`FallbackDiscoverer` (already unwired, see above)
- `runtime.ts:1447-1452`: `DESTRUCTIVE_TOOLS` hardcoded set — should use `ToolDefinition.executionPolicy.irreversible` flag (E7 adds this flag)
- `registry.ts`: `ToolIntentRouter` registered but never called during execution

**E10 (DNA Decision Layer) — decision-layer.ts, evolution.ts, instincts — BACKLOG:**
- `decision-layer.ts`: 9 hardcoded numeric thresholds for DNA trait scoring (token budgets, temperature adjustments, risk gates, expertise level buckets) — should read `OwlDNA.learnedPreferences` dynamically
- `evolution.ts`: 7 hardcoded thresholds controlling decay rate, trigger timing, min session length, trajectory quality gates
- `engine/router.ts`: `HEAVY_PATTERNS`, `SIMPLE_PATTERNS` regex + `scoreComplexity()` word-count heuristics → should use `IntelligenceRouter` task-type scoring
- `instincts/engine.ts:62`: `inst.keywords?.some(kw => lower.includes(kw))` hardcoded matching

**E11 (Proactive / Heartbeat) — proactive.ts — BACKLOG:**
- 9 hardcoded intervals, quiet hour times, and job cadences → should read from `OwlDNA.proactivity` + `trajectories` user response timing patterns
- 3 dead code instances: `SkillEvolver`, `PatternMiner`, `KnowledgeCouncil` — disabled and never re-enabled

**E12 (Parliament) — orchestrator.ts — BACKLOG:**
- Hardcoded search limits and static perspective assignment — should use `IntelligenceRouter("parliament")` task type

### Trajectories Asymmetry

`trajectories` data is **written by runtime** and **read by evolution** but **never consulted during the current session's decision cycle**:
- Runtime writes tool outcomes to `trajectories` on every turn ✓
- Evolution reads `trajectories` to mutate DNA ✓ (but only runs every N conversations)
- Runtime **never reads** `trajectories` reward signals to adjust current retry/fallback behavior ✗

The full feedback loop `runtime → trajectories → evolution → DNA → runtime` exists architecturally but **breaks within a single session**. Mid-session learning is an E10 backlog item.

### Remediation Priority Order

1. **E9 (immediate):** Wire `delegationPreference` + IntelligenceRouter into clarification — **this element**
2. **E7 (next):** Wire `FallbackSequencer`/`Discoverer` into runtime fallback; replace `DESTRUCTIVE_TOOLS` list with `executionPolicy.irreversible` flag
3. **E10:** Replace 9 `decision-layer.ts` numeric thresholds with DNA-driven values; fix `instincts/engine.ts` keyword matching
4. **E11:** Replace proactive.ts hardcoded intervals with trajectory-learned cadences
5. **E12:** Route parliament decisions through `IntelligenceRouter("parliament")`

---

## Implementation Notes

1. **Start with the delete.** Removing `pre-execution-confirmer.ts`, `unclarity-surfacer.ts`, `ambiguity-detector.ts` first makes the test suite fast-fail on every remaining call site — surfacing all integration points before new code lands.

2. **`IntelligenceRouter` task type.** Use `"classification"` task type — already defined at LOW tier. Do not create a new task type.

3. **LLM parse failure fallback.** If the classifier returns unparseable JSON, `ClarificationGate` must default to `{ action: "PROCEED" }` — fail open, not closed. Never block execution on a classifier failure.

4. **Schema v19 runs in all three migration paths** (`fresh`, `upgrade`, `reset`) in `db.ts`. Required by convention from E8.

5. **Do not mock `IntelligenceRouter` in production tests.** Use real cheap-tier calls in integration tests, or a deterministic stub that returns `PROCEED` for clear messages and `CONFIRM` for edge cases — not hardcoded responses.
