# Element 10 — Parliament: Sparse Debate, Full Wiring, Intelligence-First

**Date:** 2026-05-02
**Status:** Approved for implementation
**References:**
- Market research: `docs/superpowers/research/market-multi-agent-debate-parliament-competitive-research-2026-05-02.md`
- Architecture audit: Phase 2 of E10 brainstorm session

---

## Goal

Fix Parliament's three critical failures in one targeted intervention:

1. **Intelligence-First violations** — keyword list, hardcoded model strings, numeric threshold gates
2. **Dead wires to E5-E9** — Parliament output bypasses IntelligenceRouter, ContextPipeline, GoalVerifier, IntentClarifier, DNA evolution
3. **Context pollution in Round 2** — all positions broadcast to all owls, early errors compound

This is NOT a rewrite. Two new files. Ten modified files.

---

## Architecture

### Execution Flow

```
User message
  ↓
IntentClarifier.evaluate() [E9 — already wired to gateway]
  ├─ verdict == CLARIFY → skip Parliament entirely
  └─ verdict == PROCEED/NARRATE →
       ↓
ParliamentAutoTrigger.check()
  → TopicWorthinessEvaluator.evaluate()
     → IntelligenceRouter.resolve("classification") [FIXED from raw provider.chat()]
  ├─ isWorthy == false → single-owl path
  └─ isWorthy == true →
       ↓
       Round 1: Promise.all([owl1.respond(), owl2.respond(), owl3.respond()])
         All owls respond in parallel with NO cross-visibility
         Each call: IntelligenceRouter.resolve("classification")
       ↓
       DiversityFilter.selectDivergingPair(positions[]) [NEW FILE]
         → IntelligenceRouter cheap-tier (single ~100-token call)
         → returns top-2 most disagreeing positions
         → falls back to positions[0] + positions[N-1] on error
       ↓
       Round 2: challenger sees ONLY the diverging pair (not all positions)
         Eliminates context pollution; forces engagement with real disagreement
       ↓
       Round 3: synthesizer sees all positions + challenge + diversity filter reasoning
         Synthesizer: mentor perspective > Noctua > architect > first owl
       ↓
       POST-SESSION (in parallel, all non-fatal):
         ├─ PelletStore.save(debatePellet) [already wired]
         ├─ ContextPipeline.setShortTermLayer('parliament_synthesis', content, {priority: 9, ttlTurns: 3})
         ├─ GoalVerifier.verify({toolName: 'parliament', result: synthesis, subGoal: activeSubGoal})
         │    ADVANCES → trigger DNA update
         │    BLOCKED  → no DNA update (no false reinforcement)
         ├─ updateParliamentDNA(participants, verdict, topicCategory, db)
         ├─ db.owlLearnings.add() for all participants [already wired]
         └─ db.parliamentVerdicts.record() [already wired]
```

### Round 1: Parallel Independent Positions

All owls fire simultaneously via `Promise.allSettled()`. No owl sees any other owl's response before submitting their own. This is the S²-MAD finding: independence in Round 1 produces higher-quality divergence and prevents premature convergence.

Timed out owls (>30s) get a neutral fallback position — existing pattern from `ParallelParliamentRunner`.

### Diversity Filter (new)

A single IntelligenceRouter cheap-tier call receives all Round 1 positions and identifies which two disagree most substantially. This is not a semantic embedding search — it's a direct LLM prompt: "Given these N positions on [topic], identify the two that most fundamentally disagree. Reply with only two indices (0-based)."

The filter output determines who the Round 2 challenger engages with. Even the Round 3 synthesizer's prompt includes the diversity filter's reasoning: "The most fundamental disagreement was between [owl A] and [owl B], specifically around [filter_reasoning]."

### ContextPipeline Injection

After Parliament completes, inject a short-term layer into the ContextPipeline:

```
[Parliament concluded on "{topic}"] Verdict: {verdict}
The council's synthesis: {synthesis — first 300 chars}
Key dissent: {minority_position — first 150 chars}
```

Priority **117** — after `RelevantPelletsLayer` (115) but before `ProfileLayer` (120). This places Parliament synthesis adjacent to other retrieved knowledge, ensuring the owl sees it in context when formulating the next response. TTL = 3 turns. The layer expires automatically after 3 user turns so it does not clutter the context window for the remainder of the session.

Existing priority scale for reference: Identity=10, UserMemory=35-45, UserPersona=50, Behavioral=80-95, Knowledge/Pellets=110-115, Profile=120-125, Ambient=140-145, Calibration=150-170.

### DNA Evolution Signal

`updateParliamentDNA()` in `evolution.ts` fires only when GoalVerifier returns ADVANCES (the debate helped the user). Uses same proportional-delta pattern as `updateClarificationAutonomy()`:

```typescript
const LEARNING_RATE = 0.05;

// Synthesizer: domain expertise reinforced
synthesizer.dna.expertiseGrowth[topicCategory] = clamp(
  (synthesizer.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
  0.1, 0.9
);

// Challenger: critical thinking reinforced
challenger.dna.expertiseGrowth['critical_thinking'] = clamp(
  (challenger.dna.expertiseGrowth['critical_thinking'] ?? 0.5) + LEARNING_RATE * 0.5,
  0.1, 0.9
);

// All participants: delegationPreference nudged toward 'collaborative'
for (const owl of participants) {
  if (owl.dna.delegationPreference === 'autonomous') {
    // nudge toward collaborative — the owl benefited from the group
    owl.dna.delegationAutonomy = clamp(
      owl.dna.delegationAutonomy - LEARNING_RATE,
      0.1, 0.9
    );
  }
}
```

On GoalVerifier BLOCKED: no DNA change. On GoalVerifier error: no DNA change (fail-open, non-fatal).

---

## Files

### New Files (2)

**`src/parliament/diversity-filter.ts`**
```typescript
export class DiversityFilter {
  async selectDivergingPair(
    positions: OwlPosition[],
    router: IntelligenceRouter,
  ): Promise<[OwlPosition, OwlPosition]>
}
```
Single method. Calls IntelligenceRouter.resolve("classification") with a prompt listing all positions, asking for the two most diverging indices. Falls back to `[positions[0], positions[positions.length - 1]]` on any error. No state. Fully testable with mock positions.

---

### Modified Files (10)

**`src/parliament/parallel-runner.ts`**
- DELETE: `static shouldTrigger(topic: string, owlConfidence?: number): boolean` — the entire method including the keyword array. Any callers switch to `TopicWorthinessEvaluator.evaluate()`.
- The `runPositions()`, `runConvergence()`, and `run()` methods stay — they provide the parallel execution skeleton that `MultiRoundDebateManager` will call.

**`src/parliament/topic-worthiness.ts`**
- DELETE: `export const THRESHOLD = 0.6` constant.
- CHANGE: `evaluate()` return — remove `score` synthesis from `THRESHOLD`. Return `isWorthy` directly from parsed LLM JSON. Keep `confidence` for logging only.
- CHANGE: `TopicWorthinessEvaluator` constructor — accept `IntelligenceRouter` as optional second parameter. When present, use `router.resolve("classification")` for the evaluation call instead of raw `this.provider.chat()`.

**`src/parliament/lite.ts`**
- CHANGE: All 3 occurrences of `this.config.providers?.anthropic?.defaultModel ?? "claude-haiku-4-5-20251001"` → accept `router: IntelligenceRouter` in constructor and call `router.resolve("classification")` for each provider.chat() invocation.
- Constructor signature: `constructor(private provider: ModelProvider, private config: StackOwlConfig, private db?: MemoryDatabase, private router?: IntelligenceRouter)`

**`src/parliament/routing-wirer.ts`**
- CHANGE: `classifyWithParliament()` — remove `confidenceThreshold` from options interface and default `{...options}` spread. Decision to override to PARLIAMENT is now pure: if `ParallelRunner triggered + LLM check confirmed → PARLIAMENT`. No numeric gate.
- DELETE: body of `prepareParliamentContext()` — replace with `return [];` and deprecation comment pointing to orchestrator's inline injection.

**`src/parliament/orchestrator.ts`**
- DELETE: `private async runRound1()`, `runRound2()`, `runRound3()` — these are exact duplicates of `MultiRoundDebateManager`. Replace `convene()` body to delegate to `MultiRoundDebateManager.runDebate(session)`.
- KEEP: `convene()` entry point, constructor, `formatSessionMarkdown()`, post-session Pellet save, `parliamentVerdicts.record()`, `owlLearnings.add()`.
- ADD: post-session ContextPipeline injection and GoalVerifier check (see gateway/core.ts changes).

**`src/parliament/multi-round-debate.ts`**
- CHANGE: `runRound1()` — convert sequential `for` loop to `Promise.allSettled()` (parallel execution, no cross-visibility).
- ADD: after `runRound1()` completes, call `DiversityFilter.selectDivergingPair()` and store result on session as `session.diversePair`.
- CHANGE: `runRound2()` — challenger prompt reads only `session.diversePair` positions, not all `session.positions`.
- CHANGE: `runRound3()` — synthesizer prompt includes `session.diversePair` context and the diversity filter reasoning.
- ADD: `DiversityFilter` import; inject `IntelligenceRouter` via constructor.

**`src/owls/evolution.ts`**
- ADD: `export async function updateParliamentDNA(participants: OwlInstance[], verdict: string, topicCategory: string, db: MemoryDatabase, goalVerifierResult: 'ADVANCES' | 'PARTIAL' | 'BLOCKED' | 'NEUTRAL'): Promise<void>`
- Follows exact same pattern as existing `updateClarificationAutonomy()`. Non-fatal wrapper around DNA field updates + `db.owls.updateDNA()`.

**`src/context/pipeline.ts`**
- ADD: `setShortTermLayer(key: string, content: string, opts: {priority: number, ttlTurns: number}): void` — stores layer in a `shortTermLayers: Map<string, {content, priority, ttlTurns, insertedAt}>` field on the pipeline instance.
- CHANGE: `buildContext()` — includes short-term layers where `ttlTurns > 0`, then decrements `ttlTurns`. Expired layers (ttlTurns === 0) are evicted.
- Short-term layers render with their priority in the same layer ordering as permanent layers.

**`src/memory/db.ts`**
- Schema v20: Add `parliament_session_id TEXT` column to `trajectory_turns` table.
- `applyV20Migration()` pattern matching existing v17-v19 migrations.
- No new tables — Parliament sessions already tracked via `parliament_verdicts` table.

**`src/gateway/core.ts`**
- CHANGE: Parliament post-session block (around line 1890-1900) — after `debateSession.synthesis` is set:
  1. `await this.contextPipeline?.setShortTermLayer('parliament_synthesis', formattedSynthesis, {priority: 9, ttlTurns: 3})`
  2. `const verifierResult = await this.goalVerifier?.verify({toolName: 'parliament', toolResult: debateSession.synthesis, subGoal: this.ctx.engineContext?.activeSubGoal, userMessage: message.text})`
  3. `await updateParliamentDNA(participants, debateSession.verdict, topicCategory, this.db, verifierResult?.verdict ?? 'NEUTRAL')`
- All three calls non-fatal (catch + log).

---

## What We're Not Doing

- **Not merging KnowledgeCouncil with Parliament** — different triggers (on-demand vs scheduled), different output format. They share Pellet generation but have separate purposes.
- **Not replacing ParallelParliamentRunner entirely** — its `runPositions()` / `runConvergence()` execution model is reused. Only `shouldTrigger()` is deleted.
- **Not changing the streaming UX** — `ParliamentCallbacks.onPositionReady()` / `onChallengeReady()` / `onSynthesisReady()` unchanged.
- **Not adding a Parliament frequency controller** — the combination of `TopicWorthinessEvaluator` + IntentClarifier pre-check is sufficient rate limiting.
- **Not adding a Parliament marketplace** — deferred.

---

## Acceptance Criteria

1. **AC-1: No Intelligence-First violations remain**
   `grep -r "shouldTrigger\|THRESHOLD\|confidenceThreshold\|claude-haiku-4-5" src/parliament/` returns zero matches after implementation.

2. **AC-2: Round 1 is parallel**
   Unit test: mock provider that records call timestamps. All 3 owl calls in Round 1 overlap (start time delta < 50ms).

3. **AC-3: Round 2 sees only diverging pair**
   Unit test with 4 owls: mock DiversityFilter returns positions[1] and positions[3]. Assert Round 2 prompt contains only those two owl names, not positions[0] or positions[2].

4. **AC-4: ContextPipeline receives synthesis**
   Integration test: run Parliament session with mock owls. Assert `pipeline.shortTermLayers.has('parliament_synthesis')` is true after session. Assert layer content contains synthesis text and verdict.

5. **AC-5: GoalVerifier is called**
   Unit test: mock GoalVerifier. Run Parliament session with `engineContext.activeSubGoal` set. Assert `goalVerifier.verify()` was called with `toolName === 'parliament'`.

6. **AC-6: DNA update fires on ADVANCES**
   Unit test: mock GoalVerifier returning ADVANCES. Run Parliament. Assert synthesizer owl's `expertiseGrowth` for topic category increased by `LEARNING_RATE`.

7. **AC-7: DNA update skips on BLOCKED**
   Unit test: mock GoalVerifier returning BLOCKED. Assert no DNA field changes.

8. **AC-8: Pellet saved after session**
   Integration test: Parliament session completes → `pelletStore.save()` called → Pellet contains `session.synthesis` text → Pellet has `parliament` tag.

9. **AC-9: Schema v20 migration**
   Test: fresh DB + migrate → `trajectory_turns` has `parliament_session_id` column. Existing test DB + migrate → column added without data loss.

10. **AC-10: IntentClarifier pre-check blocks Parliament on CLARIFY**
    Integration test: mock IntentClarifier returning CLARIFY verdict. Assert Parliament does NOT fire (no LLM calls to ParliamentAutoTrigger).

11. **AC-11: DiversityFilter fallback**
    Unit test: DiversityFilter.selectDivergingPair() with router that throws. Assert returns `[positions[0], positions[positions.length-1]]` without throwing.

12. **AC-12: All existing Parliament tests still pass**
    `npx vitest run __tests__/parliament/` — zero regressions.

---

## Test Count Estimate

- 3 tests for `DiversityFilter` (success, error fallback, edge case: 2 positions)
- 4 tests for `evolution.updateParliamentDNA()` (advances, blocked, partial, error)
- 3 tests for `topic-worthiness.ts` refactor (no threshold, isWorthy trusted, IntelligenceRouter path)
- 3 tests for `parallel-runner.ts` (shouldTrigger deleted — callers test, evaluate() still works)
- 3 tests for `multi-round-debate.ts` sparse round integration
- 3 tests for `context/pipeline.ts` short-term layers (set, expire, priority ordering)
- 2 tests for schema v20 migration
- 5 integration tests (AC-4 through AC-10)

**~26 new tests total.**

---

## Constraints Summary

| Constraint | How Met |
|---|---|
| No hardcoded keywords/regex/thresholds | `shouldTrigger()` keyword list deleted; `THRESHOLD` deleted; `confidenceThreshold` deleted |
| All model selection via IntelligenceRouter | All `provider.chat()` in Parliament → `router.resolve("classification")` |
| Parliament → Pellets | Already wired, unchanged |
| Parliament → ContextPipeline | Short-term layer, priority 9, TTL 3 turns |
| Parliament → DNA evolution | `updateParliamentDNA()` on GoalVerifier ADVANCES |
| Do not over-engineer | 2 new files total |
| Synthesis citable in future turns | ContextPipeline layer injected after each session |
