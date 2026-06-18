# Element 9: Clarification & Intent Detection — Final Spec

**Date:** 2026-05-02
**Status:** Approved — v2 (post-BMAD review)
**Review team:** BA (Mary), PM (John), Architect (Winston), Code Reviewer
**Supersedes:** v1 of this file (same path)

---

## Executive Summary

StackOwl's clarification pipeline has one confirmed production bug and four systemic design failures. A clear research request — "can you do research about zimaboard 2, tell me where i can use?" — was echoed back with "Confidence: 60%, Did I understand correctly?" because `pre-execution-confirmer.ts` penalises any message containing the words "where", "what", "who", "which", "when", or "how".

**v1 of this spec fixed the root cause but introduced six new problems** (hardcoded thresholds it claimed to eliminate, missing question generation path, phantom session field, wrong hook point for tool risk, invented non-existent file, no continuation after clarification answer). This is the corrected spec.

**What changes:**

1. Delete `pre-execution-confirmer.ts` and `unclarity-surfacer.ts` (bad implementations)
2. Rename and rewrite `ambiguity-detector.ts` → `intent-clarifier.ts` (keep the good prompt, extend to 4-way verdict)
3. Keep `pre-action-questioner.ts` with minimal fixes (it is well-designed; hook it correctly)
4. Create `tool-risk-guard.ts` (wires `PreActionQuestioner` into `ToolRegistry.execute()`)
5. Create `session-autonomy-bias.ts` (in-session fast adaptation, no DNA mutation)
6. Rewrite `coordinator.ts` (correct Jaccard bug, semantic hash dedup)
7. Establish the **Intelligence-First Principle** as the codebase standard

---

## Intelligence-First Principle

> **Every decision about intent, routing, ambiguity, confirmation, or behavioral threshold in StackOwl MUST flow through `IntelligenceRouter`, `OwlDNA.learnedPreferences`, or the `trajectories` reward signal. Hardcoded keyword lists, regex classifiers, and fixed numeric thresholds are forbidden as decision gates.**

The three permitted sources of ground truth:
- **`IntelligenceRouter.resolve("classification")`** — cheap-tier LLM (<400ms) for intent/ambiguity/risk classification
- **`OwlDNA.learnedPreferences` + `OwlDNA.evolvedTraits`** — per-user learned preferences that bias LLM prompts (injected as natural language, not used as threshold gates)
- **`trajectories` reward signal** — past outcome data, read by evolution engine to update DNA

---

## Problem Statement

### Confirmed Bug

`pre-execution-confirmer.ts:calculateConfidence()` applies -0.4 confidence penalty to any message matching `/\b(?:which|what|who|where|when|how)\b.*\?\s*$/i`. "where i can use?" triggers → 60% confidence → confirmation echoed back.

Additional problems in the same file:
- `findUncertaintyAreas()` has a dead branch: `if (/if.*then/i.test(message) && !/then/i.test(message))` — the two conditions are mutually exclusive, so "Conditional outcome not specified" is never reachable
- Module-level singleton `preExecutionConfirmer` = shared mutable state across callers
- `summarizeUnderstanding()` truncates to 50 words and appends generic boilerplate — that IS the echo

### Four Distinct Clarification Modes

The existing code and v1 spec treat "clarification" as one problem. It is four distinct sub-problems with different trigger points, data sources, and correct responses:

| Mode | Question | Trigger point | Owner |
|------|----------|--------------|-------|
| **A — Intent clarity** | Is the user's REQUEST clear enough to act on? | Gateway entry, before ReAct loop | `IntentClarifier` |
| **B — Tool risk** | Is this specific TOOL CALL about to fire risky/destructive? | Inside `ToolRegistry.execute()`, after LLM chose the tool | `ToolRiskGuard` |
| **C — User confusion** | The user is expressing their own uncertainty — help them think through it | Gateway entry, same LLM call as Mode A | `IntentClarifier` (`USER_CONFUSED` verdict) |
| **D — Session preference** | This user just said "just do it" — adapt immediately for this session | Session-layer, instant | `SessionAutonomyBias` |

**v1's fatal flaw**: It put everything in a single pre-execution `ClarificationGate`, which cannot handle Mode B (tool name and params are unknown until mid-ReAct) and conflated Modes A and C.

---

## Architecture

### Component Disposition

| File | Disposition | Reason |
|------|-------------|--------|
| `pre-execution-confirmer.ts` | **DELETE** | Regex root cause. Dead code branch. Singleton mutable state. Nothing salvageable. |
| `unclarity-surfacer.ts` | **DELETE** | Wrong grammar (owl says it's confused when user expressed confusion). Regex patterns. The use-case (Mode C) is absorbed into `IntentClarifier`'s `USER_CONFUSED` verdict. Move user-confusion detection to `InstinctEngine` in E10 if deeper handling needed. |
| `ambiguity-detector.ts` | **RENAME → `intent-clarifier.ts` and rewrite** | The LLM prompt language is correct ("only flag if core intent genuinely unclear — not just brief or informal"). The class structure is not. Extend to 4-way verdict + question generation in one LLM call. |
| `pre-action-questioner.ts` | **EDIT only** | Well-designed. LLM risk assessment, fail-open, correct four-level taxonomy. Two bugs to fix (see §PreActionQuestioner). Wire to `IntelligenceRouter`. |
| `coordinator.ts` | **REWRITE** | Jaccard formula is mathematically wrong (O(n) array includes, incorrect union). Replace with semantic hash dedup. Keep 5-minute window concept. |
| `types.ts` | **EDIT** | Delete `PreExecutionConfirmation`. Add `IntentVerdict`, `SessionBias`. Keep everything else — `ExecutionCheckpoint`/`MidExecutionState` are orphaned but model a real future feature. |
| `index.ts` | **EDIT** | Remove deleted exports, add new ones. |

---

### New Component: `src/clarification/intent-clarifier.ts`

**Single responsibility:** One LLM call that classifies intent AND handles user confusion AND generates the clarification question text. Replaces `ambiguity-detector.ts` and absorbs `pre-execution-confirmer.ts` role.

```typescript
export type IntentVerdict =
  | 'PROCEED'        // clear request — act immediately
  | 'NARRATE'        // act, but begin response with interpretation
  | 'CLARIFY'        // genuinely multi-path — ask one focused question
  | 'USER_CONFUSED'; // user expressing their own uncertainty — help them

export interface IntentClassification {
  verdict: IntentVerdict;
  question: string | null;        // populated only when verdict === 'CLARIFY'
  interpretation: string | null;  // populated when verdict === 'NARRATE'
  reasoning: string;              // always populated — used for hash dedup in coordinator
}

export class IntentClarifier {
  constructor(
    private router: IntelligenceRouter,
    private coordinator: ClarificationCoordinator,
  ) {}

  async evaluate(
    message: string,
    history: ChatMessage[],
    dna: OwlDNA,
    bias: SessionAutonomyBias,
  ): Promise<IntentClassification>;
  // Fails open: on any LLM failure returns { verdict: 'PROCEED', question: null, interpretation: null, reasoning: '' }
}
```

**Classification prompt (≤200 tokens, one LLM call):**

```
You are classifying a message for a personal AI assistant.

Message: "{message}"
Recent context (last 3 turns): {context}
Owl character: {delegationPreference} (autonomous | collaborative | confirmatory)
Session signals: user has dismissed {dismissCount} clarification question(s) this session.

Classify as one of:
PROCEED — request is clear and actionable, proceed immediately
NARRATE — proceed but begin response with: "I'll [interpretation]..."
CLARIFY — genuinely multi-path with no safe default; ask exactly one focused question
USER_CONFUSED — user is expressing their own uncertainty ("not sure which", "I don't know if"); acknowledge and help

Only use CLARIFY if proceeding would likely execute the WRONG thing.
Brief or informal messages are NOT ambiguous.

Reply with JSON only:
{
  "verdict": "PROCEED|NARRATE|CLARIFY|USER_CONFUSED",
  "question": "one focused question if CLARIFY, otherwise null",
  "interpretation": "what you will do if NARRATE, otherwise null",
  "reasoning": "one sentence why"
}
```

**Key design decisions:**
- `delegationPreference` (DNA field that already exists, was UNWIRED) injected as natural language — no threshold gate
- `dismissCount` from `SessionAutonomyBias` injected — no DNA mutation needed for session-immediate adaptation  
- `question` is generated IN THE SAME LLM CALL — no second round-trip, no `'Could you clarify?'` fallback
- Fail-open on parse failure or LLM error: return `PROCEED`
- Coordinator checks dedup BEFORE making the LLM call (cheap O(1) hash) — avoids paying for duplicate classifications

---

### New Component: `src/clarification/session-autonomy-bias.ts`

**Single responsibility:** Track within-session dismissal signals. Inject as prompt context. Zero DNA writes, zero DB writes — pure session state.

```typescript
export class SessionAutonomyBias {
  private _dismissCount = 0;

  get dismissCount(): number { return this._dismissCount; }

  recordDismissal(): void { this._dismissCount++; }

  toPromptContext(): string {
    if (this._dismissCount === 0) return '';
    if (this._dismissCount === 1) return 'user dismissed 1 clarification question this session.';
    return `user dismissed ${this._dismissCount} clarification questions this session — prefer PROCEED.`;
  }
}
```

Created per-session in `gateway/core.ts`. Passed to `IntentClarifier.evaluate()`. When the user's next message contains dismissal signals (e.g. "just do it", "skip", "proceed anyway") — detected by `InstinctEngine` or a simple intent signal — `session.clarificationBias.recordDismissal()` is called.

---

### New Component: `src/clarification/tool-risk-guard.ts`

**Single responsibility:** Thin wrapper over `PreActionQuestioner`. Injectable into `ToolRegistry`. Handles Mode B (pre-action risk) at the correct hook point — inside `ToolRegistry.execute()` AFTER the LLM has decided which tool to call with which params.

```typescript
export interface RiskGateResult {
  allowed: true;
} | {
  allowed: false;
  confirmationId: string;
  userFacingMessage: string; // the question to ask the user
}

export class ToolRiskGuard {
  constructor(private questioner: PreActionQuestioner) {}

  async check(
    toolName: string,
    args: Record<string, unknown>,
    toolPolicy: ExecutionPolicy,
  ): Promise<RiskGateResult>;

  resolveConfirmation(
    id: string,
    userAnswer: string,
  ): 'confirmed' | 'cancelled' | 'not_found';
}
```

**Wiring in `ToolRegistry.execute()`:**

```typescript
// After schema validation, before tool.execute(args, context):
if (this._riskGuard) {
  const gate = await this._riskGuard.check(name, args, toolDef.executionPolicy ?? {});
  if (!gate.allowed) {
    // Suspend tool execution — return question as tool result string
    this._eventBus?.emit({ type: 'tool:awaiting_confirmation',
      confirmationId: gate.confirmationId, toolName: name });
    return { content: gate.userFacingMessage, isConfirmationRequest: true };
  }
}
```

---

### Rewritten: `src/clarification/coordinator.ts`

```typescript
export class ClarificationCoordinator {
  private recentReasoningHashes: Map<string, { sessionKey: string; ts: number }> = new Map();
  private readonly SESSION_WINDOW_MS = 5 * 60 * 1000;

  shouldSuppressDuplicate(reasoning: string, sessionKey: string): boolean {
    this.evictExpired();
    const hash = this.hashReasoning(reasoning);
    const existing = this.recentReasoningHashes.get(hash);
    if (existing && existing.sessionKey === sessionKey) return true;
    this.recentReasoningHashes.set(hash, { sessionKey, ts: Date.now() });
    return false;
  }

  private hashReasoning(reasoning: string): string {
    // Stable 8-char hash of first 60 chars (lowercased) — semantic dedup without embeddings
    const normalized = reasoning.toLowerCase().slice(0, 60);
    let h = 0;
    for (let i = 0; i < normalized.length; i++) {
      h = (Math.imul(31, h) + normalized.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  private evictExpired(): void {
    const cutoff = Date.now() - this.SESSION_WINDOW_MS;
    for (const [k, v] of this.recentReasoningHashes) {
      if (v.ts < cutoff) this.recentReasoningHashes.delete(k);
    }
  }

  clear(): void { this.recentReasoningHashes.clear(); }
}
```

**Why hash of reasoning, not word-overlap:** The coordinator deduplicates semantic content (is the owl about to ask the same logical question?). The LLM's `reasoning` field is the semantic content. Hashing it is O(1) and handles paraphrase: "Are you sure about that file?" vs "Should I delete that file?" — different surface words, same semantic reasoning → same hash.

---

### Edited: `src/clarification/pre-action-questioner.ts`

Two bugs to fix, two wiring changes:

**Bug 1 — Parse failure returns inconsistent risk level:**
```typescript
// BEFORE (line ~52):
return { riskLevel: 'medium', riskReasons: ['Failed to parse'], shouldConfirm: false, confirmationQuestion: null };
// AFTER:
return { riskLevel: 'low', riskReasons: ['Risk assessment unavailable'], shouldConfirm: false, confirmationQuestion: null };
// medium risk SHOULD confirm — fail open to low/no-confirm instead
```

**Bug 2 — Verify template substitution works:**
The prompt uses `.replace('{JSON.stringify(params)}', JSON.stringify(params))` — this IS correct string replacement (the literal text `{JSON.stringify(params)}` appears in the backtick template). No change needed on this line.

**Wiring change:** Replace `this.modelProvider.chat(...)` with `IntelligenceRouter.resolve("classification")` + provider call pattern. Constructor changes from `(modelProvider: ModelProvider)` to `(router: IntelligenceRouter)`.

---

### Gateway Integration (`src/gateway/core.ts`)

**Pre-execution gate (Mode A + C):**

```typescript
// At gateway entry, before runEngine():
const clarificationResult = await this.intentClarifier.evaluate(
  message.text,
  session.messages.slice(-3),
  this.ctx.owl.dna,
  session.clarificationBias,
);

// Mode C — user expressed confusion: acknowledge, short-circuit
if (clarificationResult.verdict === 'USER_CONFUSED') {
  return this.buildResponse(
    `Let me help you think through this. ${clarificationResult.reasoning}`,
    owlMeta
  );
}

// Mode A — need clarification: store original message, return question
if (clarificationResult.verdict === 'CLARIFY') {
  session.pendingExecution = { originalMessage: message.text, trajectoryId };
  await this.db.trajectories.markClarificationAsked(trajectoryId);
  return this.buildResponse(clarificationResult.question!, owlMeta);
}

// Mode A — narrate: pass interpretation prefix through EngineContext
if (clarificationResult.verdict === 'NARRATE') {
  engineContext.narrationPrefix = clarificationResult.interpretation ?? undefined;
}
// PROCEED: fall through to ReAct loop
```

**Continuation after clarification answer:**

```typescript
// At gateway entry, BEFORE calling intentClarifier:
if (session.pendingExecution) {
  const { originalMessage } = session.pendingExecution;
  session.pendingExecution = null;
  // Re-evaluate with combined context: originalMessage + user's answer
  const retryResult = await this.intentClarifier.evaluate(
    originalMessage,
    [...session.messages.slice(-3), message.text],
    this.ctx.owl.dna,
    session.clarificationBias,
  );
  // If STILL CLARIFY on the same original message: override to PROCEED
  if (retryResult.verdict === 'CLARIFY') retryResult.verdict = 'PROCEED';
  // Continue with retryResult
}
```

**NARRATE UX path — no phantom session fields:**

In `engine/runtime.ts`, the system prompt builder receives `engineContext.narrationPrefix`. If set:
```
// Appended to system prompt:
"Begin your response with: 'I'll [narrationPrefix]. ' then continue normally."
```
The LLM prepends it naturally. No response formatter change. No `pendingNarration` state.

---

### Learning Loop

**Layer 1 — Session-immediate (new):**
`SessionAutonomyBias.dismissCount` is passed to `IntentClarifier` as prompt context every turn. Instant effect. No DB writes. Dismissed when the session ends.

**Layer 2 — Batch evolution (edit to evolution.ts):**

```typescript
// In OwlEvolutionEngine batch cycle, after DNA trait mutation:
async function updateClarificationAutonomy(owlName: string, db: StackOwlDB, dna: OwlDNA) {
  const recent = await db.trajectories.getRecentWithClarification(owlName, 50);
  if (recent.length < 5) return; // cold start — no update

  const asked   = recent.filter(t => t.clarification_asked === 1);
  const skipped = recent.filter(t => t.clarification_asked === 0);
  if (asked.length === 0 || skipped.length === 0) return;

  const avgAsked   = asked.reduce((s, t) => s + t.reward, 0) / asked.length;
  const avgSkipped = skipped.reduce((s, t) => s + t.reward, 0) / skipped.length;
  const delta = avgSkipped - avgAsked; // positive = user prefers proceeding

  const LEARNING_RATE = 0.05;
  const current = (dna.learnedPreferences['clarification_autonomy_score'] as number) ?? 0.5;
  dna.learnedPreferences['clarification_autonomy_score'] =
    Math.max(0.1, Math.min(0.9, current + LEARNING_RATE * delta));
  // Note: uses delta magnitude, NOT Math.sign(delta) — preserves signal strength
}
```

**Cold start:** Fewer than 5 trajectories → no update → defaults to `delegationPreference` DNA trait (already set by evolution from conversation patterns).

---

### Schema — Version 19

```sql
-- v19: track whether clarification was asked per trajectory turn
ALTER TABLE trajectories ADD COLUMN clarification_asked INTEGER DEFAULT 0;
-- 1 = owl asked a clarification question (MODE A CLARIFY)
-- 0 = owl proceeded (PROCEED or NARRATE)
```

Bump `SCHEMA_VERSION` 18 → 19. Add v19 branch in all three migration paths (`fresh`, `upgrade`, `reset`).

New helper on `TrajectoriesRepo`:
- `markClarificationAsked(id: string): void`
- `getRecentWithClarification(owlName: string, limit: number): Array<Trajectory & { clarification_asked: number }>`

---

## Decision Table — Threshold-Free

The LLM receives `delegationPreference` + `dismissCount` as natural language prompt context. The LLM outputs the verdict. The policy layer has **four rows, zero numeric thresholds:**

| Verdict from LLM | Gateway action |
|-----------------|----------------|
| `PROCEED` | Execute immediately |
| `NARRATE` | Execute; pass `interpretation` as `narrationPrefix` through `EngineContext` |
| `CLARIFY` | Store `session.pendingExecution`, mark trajectory, return `question` to user |
| `USER_CONFUSED` | Return acknowledgement + reasoning; skip ReAct loop |

The `ToolRiskGuard` (Mode B) operates independently inside `ToolRegistry.execute()` — it is NOT in this table. Tool risk confirmation is orthogonal to intent classification.

---

## File Map

| File | Action | Description |
|------|--------|-------------|
| `src/clarification/intent-clarifier.ts` | **CREATE** | 4-way verdict, question generated in same LLM call; replaces `ambiguity-detector.ts` |
| `src/clarification/session-autonomy-bias.ts` | **CREATE** | Per-session dismiss counter; injects into LLM prompt context |
| `src/clarification/tool-risk-guard.ts` | **CREATE** | Wraps `PreActionQuestioner`; injectable into `ToolRegistry`; Mode B hook |
| `src/clarification/coordinator.ts` | **REWRITE** | Hash-of-reasoning dedup; drop Jaccard (wrong formula, wrong approach) |
| `src/clarification/pre-action-questioner.ts` | **EDIT** | Fix parse-failure inconsistency; wire to IntelligenceRouter |
| `src/clarification/types.ts` | **EDIT** | Delete `PreExecutionConfirmation`; add `IntentVerdict`, `IntentClassification`, `SessionBias`; keep `ExecutionCheckpoint`/`MidExecutionState` |
| `src/clarification/index.ts` | **EDIT** | Remove deleted exports; add `IntentClarifier`, `ToolRiskGuard`, `SessionAutonomyBias` |
| `src/clarification/pre-execution-confirmer.ts` | **DELETE** | Regex root cause |
| `src/clarification/unclarity-surfacer.ts` | **DELETE** | MODE C absorbed into `IntentClarifier` `USER_CONFUSED` verdict |
| `src/clarification/ambiguity-detector.ts` | **DELETE** (replaced by intent-clarifier.ts) | Keep prompt language in new file; discard class structure |
| `src/tools/registry.ts` | **EDIT** | Add `_riskGuard: ToolRiskGuard \| null`, `setRiskGuard()`; call `_riskGuard.check()` pre-execute |
| `src/gateway/core.ts` | **EDIT** | Add `intentClarifier.evaluate()` at entry; `pendingExecution` continuation path; `narrationPrefix` into `EngineContext` |
| `src/engine/runtime.ts` | **EDIT** | `EngineContext` add `narrationPrefix?: string`; system prompt builder appends narration instruction |
| `src/memory/db.ts` | **EDIT** | v19 migration; `markClarificationAsked()`; `getRecentWithClarification()` |
| `src/owls/evolution.ts` | **EDIT** | Add `updateClarificationAutonomy()` in batch cycle; proportional delta |
| `__tests__/clarification/intent-clarifier.test.ts` | **CREATE** | ZimaBoard PROCEED, genuine ambiguity CLARIFY, high-autonomy PROCEED, USER_CONFUSED, fail-open |
| `__tests__/clarification/tool-risk-guard.test.ts` | **CREATE** | Low risk allowed, high risk suspended, confirmation resolve/cancel |
| `__tests__/clarification/session-autonomy-bias.test.ts` | **CREATE** | Dismiss count, prompt context string, reset |
| `__tests__/clarification/coordinator.test.ts` | **REWRITE** | Hash dedup, 5-min window, different session keys |

**Note:** `ambient-collector.ts` — this file **does not exist** in the codebase. All references removed from spec. Not created, not edited.

---

## Acceptance Criteria

**AC-1 — ZimaBoard test passes:**
```typescript
it('does not ask for research requests', async () => {
  const result = await clarifier.evaluate(
    'can you do research about zimaboard 2, tell me where i can use?',
    [], mockDna, new SessionAutonomyBias()
  );
  expect(result.verdict).toBe('PROCEED');
});
```

**AC-2 — No regex anywhere in classification paths:**
`grep -r "/\\b.*\\b/i" src/clarification/` returns zero results post-merge.

**AC-3 — Question field is always populated when CLARIFY:**
If `result.verdict === 'CLARIFY'` then `result.question !== null` — never throws at runtime.

**AC-4 — `delegationPreference` DNA field is read:**
`IntentClarifier.evaluate()` includes `dna.evolvedTraits.delegationPreference` in the prompt. No numeric threshold gate.

**AC-5 — Tool risk guard fires at correct hook point:**
`ToolRegistry.execute('WriteFile', { path: '...', force: true }, context)` with `_riskGuard` injected returns a `confirmationId` before `WriteFile.execute()` is called.

**AC-6 — Continuation path prevents infinite loop:**
Session with `pendingExecution` set: next turn combines original message + answer, clears pending slot, executes. If CLARIFY returned again: overridden to PROCEED.

**AC-7 — Learning loop uses proportional delta:**
`delta = 0.6` → `next = clamp(0.5 + 0.05 * 0.6, 0.1, 0.9) = 0.53` (not `0.5 + 0.1 = 0.6`).

**AC-8 — No regression.** All 633 existing tests pass.

---

## Success Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| Clarification rate | < 5% of turns | Basic frequency |
| False positive rate (clear request → asked) | < 1% | ZimaBoard class of bug |
| **False negative rate** (ambiguous → executed wrong) | < 3% | Missing from v1. Measure: turns with `reward < 0.4` AND `clarification_asked = 0` |
| **Question single-exchange resolution** | > 80% | If user has to re-explain, question was bad |
| **NARRATE interpretation acceptance** | > 90% | Users correct interpretation infrequently |
| **Latency impact** | p99 < 500ms added | LLM call adds latency — must be measured |
| Autonomy score convergence | Stabilises within ±0.05 over 3 consecutive evolution batches | More precise than v1's "first batch" |

---

## Non-Goals (Element 9)

- BERT/embedding intent classification (LLM via IntelligenceRouter is sufficient)
- Multi-turn clarification dialogue (one exchange max; continuation handled via `pendingExecution`)
- Voice/audio UX
- User-confusion handling via dedicated `InstinctEngine` instinct (E10 backlog)
- Evolution batch learning (deferred to E10 for DNA Decision Layer overhaul — schema and `markClarificationAsked()` ship in E9; the `updateClarificationAutonomy()` function ships in E9 but is only exercised once enough trajectory data accumulates)
- `/quiet` command (Telegram/CLI channel command — belongs in channel element, not clarification)

---

## Appendix A: Cross-Element Violations (unchanged from v1)

See v1 Appendix A for the full cross-element audit: 66 hardcoded thresholds, 4 unwired components across 13 files. Remediation order: E9 → E7 → E10 → E11 → E12.

Critical additions from the BMAD review:

**`OwlDNA.delegationPreference` wired in E9** — this was UNWIRED (evolution writes it, nothing reads it). `IntentClarifier` now reads it. This closes the most important unwired loop.

**`trajectories` mid-session gap** — trajectories are written by runtime, read by evolution, but never consulted during the current session. The E9 `SessionAutonomyBias` provides an in-session proxy without the session latency. Full mid-session trajectory feedback is an E10 item.
