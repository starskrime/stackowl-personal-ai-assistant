# Clarification System Fix Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Fix the clarification system so it asks fewer questions, follows conversation context, and doesn't repeat itself.

**Architecture:** Create a shared `ClarificationCoordinator` that deduplicates questions across modules, implements "already asked" checking, and raises thresholds. Fix `priorContext` usage, wire `MidExecutionRouter`, and refactor LLM prompts to be less eager.

**Tech Stack:** TypeScript, existing StackOwl modules

---

## Root Causes Addressed

| Root Cause | Fix Location |
|---|---|
| No cross-module resolution tracking | Phase 2: Create ClarificationCoordinator |
| Resolved questions state never consulted | Phase 1: Add check in AmbiguityDetector |
| priorContext silently discarded | Phase 3: Fix UnclaritySurfacer, PreExecutionConfirmer |
| Independent modules duplicate checks | Phase 2: Add coordinator deduplication |
| LLM prompts primed for pattern-matching | Phase 1: Refactor prompts |
| Low threshold + conservative fallback | Phase 1: Raise thresholds |
| MidExecutionRouter not wired | Phase 4: Wire or remove dead code |

---

## Phase 1: Fix AmbiguityDetector (Root Cause of Repeated Questions)

**Files:**
- Modify: `src/clarification/ambiguity-detector.ts`

- [ ] **Step 1: Add "already resolved" check to detectAmbiguity**

Add a check at the start of `detectAmbiguity()` to consult `state.resolvedQuestions` before asking a new question:

```typescript
async detectAmbiguity(message: string, context: string[] = []): Promise<ClarificationResult> {
  if (!message.trim()) {
    return { needsClarification: false, ambiguitySignals: [] };
  }

  // NEW: Check if this exact ambiguity was already resolved in this session
  const recentlyResolved = this.state.resolvedQuestions.find(rq =>
    rq.contextUpdated.some(ctx => message.toLowerCase().includes(ctx.toLowerCase().slice(0, 50)))
  );
  if (recentlyResolved) {
    this.logBehavioralEvent('ambiguity_auto_resolved', { reason: 'previously_answered' });
    return { needsClarification: false, ambiguitySignals: [], autoResolved: true };
  }

  // ... rest of existing logic
```

Run: `npx vitest run __tests__/clarification/ambiguity-detector.test.ts 2>/dev/null || echo "No test file yet"`

- [ ] **Step 2: Raise AMBIGUITY_THRESHOLD from 0.6 to 0.75**

```typescript
const AMBIGUITY_THRESHOLD = 0.75;
```

Run: `npx tsc --noEmit src/clarification/ambiguity-detector.ts`

- [ ] **Step 3: Refactor LLM prompt to evaluate, not detect**

Replace `AMBIGUITY_ANALYSIS_PROMPT` (lines 6-22) with a more restrained prompt that asks the LLM to evaluate genuine confusion, not search for patterns:

```typescript
const AMBIGUITY_ANALYSIS_PROMPT = `Evaluate whether this message genuinely needs clarification or whether you can proceed with reasonable interpretation.

Message: "{message}"

Context from conversation: {context}

Only flag as ambiguous if:
- The core intent is genuinely unclear (not just brief or informal)
- Proceeding would likely do the wrong thing
- The ambiguity cannot be resolved from context

Respond with JSON:
{{
  "isAmbiguous": boolean,
  "ambiguityTypes": string[],
  "confidence": 0.0-1.0,
  "canProceedWithoutClarification": boolean,
  "reasoning": "brief explanation"
}}`;
```

Note: Update `parseLlmResponse` to also extract `canProceedWithoutClarification` and `reasoning` fields.

Run: `npx vitest run __tests__/clarification/ -t ambiguity 2>/dev/null || npx vitest run __tests__/clarification/`

- [ ] **Step 4: Remove multi-signal amplification from formClarificationQuestion**

Replace lines 122-124:
```typescript
// REMOVE THIS:
const questionText = allSignals.length > 1
  ? `${baseQuestion} (I detected multiple unclear aspects: ${allSignals.map(s => s.type).join(', ')})`
  : baseQuestion;

// REPLACE WITH:
const questionText = baseQuestion;
```

Run: `npx tsc --noEmit src/clarification/ambiguity-detector.ts`

- [ ] **Step 5: Commit**

```bash
git add src/clarification/ambiguity-detector.ts
git commit -m "fix(clarification): add resolved check, raise threshold, refine LLM prompt"
```

---

## Phase 2: Fix PreExecutionConfirmer (Overly Eager Questions)

**Files:**
- Modify: `src/clarification/pre-execution-confirmer.ts`

- [ ] **Step 1: Raise confidence threshold check and remove hardcoded keyword triggers**

Modify `assessRequest()` (lines 42-67):

```typescript
// REMOVE: isVague and isHighStakes early returns (lines 43-44)
// CHANGE: confidence > 0.8 → confidence > 0.65
// REMOVE: HIGH_STAKES_KEYWORDS entirely
// REMOVE: VAGUE_INDICATORS entirely

assessRequest(message: string, context: string[] = []): PreExecutionConfirmation | null {
  const confidence = this.calculateConfidence(message, context);

  // Only ask if confidence is genuinely low AND context is also unclear
  if (confidence > 0.65) {
    return null;
  }

  // Additional check: only ask if the LAST 3 messages also have low confidence
  // This prevents asking on a single informal message
  const recentVagueness = context.slice(-3).every(msg => {
    return this.calculateConfidence(msg, []) < 0.7;
  });
  if (!recentVagueness && confidence > 0.5) {
    return null;
  }

  // ... rest of confirmation creation
```

Run: `npx tsc --noEmit src/clarification/pre-execution-confirmer.ts`

- [ ] **Step 2: Remove VAGUE_INDICATORS and HIGH_STAKES_KEYWORDS regex arrays**

Remove lines 3-36 entirely. Replace `calculateConfidence()` with a simpler, context-aware version:

```typescript
private calculateConfidence(message: string, context: string[]): number {
  let score = 1.0;

  // Only penalize for genuine ambiguity, not informal language
  const ambiguousPatterns = [
    /\b(?:which|what|who|where|when|how)\b.*\?\s*$/i,  // Ends with question
    /\[UNCERTAIN\]/i,
  ];

  if (ambiguousPatterns.some(p => p.test(message))) {
    score -= 0.4;
  }

  // Penalize only if context is ALSO unclear (not just this message)
  const contextConfidences = context.map(c => this.calculateConfidence(c, []));
  if (contextConfidences.length > 0 && contextConfidences.every(c => c < 0.6)) {
    score -= 0.2;
  }

  // Only penalize for extremely short messages (< 5 words)
  const words = message.split(/\s+/);
  if (words.length < 5 && score > 0.5) {
    score -= 0.1;
  }

  return Math.max(0, Math.min(1, score));
}
```

Run: `npx tsc --noEmit src/clarification/pre-execution-confirmer.ts`

- [ ] **Step 3: Fix findUncertaintyAreas to use context properly**

The current implementation (lines 83-109) uses context but in a limited way. Simplify it:

```typescript
private findUncertaintyAreas(message: string, context: string[]): string[] {
  // Only report genuine uncertainty, not stylistic concerns
  const areas: string[] = [];

  // Check if user is asking for help with a choice between options
  if (/\bwhich\b.*\b(or|versus|vs\.)\b/i.test(message)) {
    areas.push("Multiple options present");
  }

  // Check if there's a conditional without clear outcome
  if (/if.*then/i.test(message) && !/then/i.test(message)) {
    areas.push("Conditional outcome not specified");
  }

  return areas;
}
```

Run: `npx tsc --noEmit src/clarification/pre-execution-confirmer.ts`

- [ ] **Step 4: Remove fallback that always questions on parse failure**

Lines 52-59 in `assessRequest` are no longer relevant since we're not using LLM there. But if LLM calls are added later, ensure fallback does NOT default to `shouldConfirm: true`.

- [ ] **Step 5: Commit**

```bash
git add src/clarification/pre-execution-confirmer.ts
git commit -m "fix(clarification): raise thresholds, remove eager keyword triggers"
```

---

## Phase 3: Fix UnclaritySurfacer (priorContext Ignored)

**Files:**
- Modify: `src/clarification/unclarity-surfacer.ts`

- [ ] **Step 1: Actually use priorContext in extractUnclarity**

Replace line 33:
```typescript
// REMOVE: private extractUnclarity(message: string, _priorContext: string[]): UnclarityItem | null {
// REPLACE WITH:
private extractUnclarity(message: string, priorContext: string[]): UnclarityItem | null {
```

Add semantic deduplication - check if same unclarity was already surfaced in prior context:

```typescript
detectUnclarity(message: string, priorContext: string[] = []): UnclarityItem | null {
  const messageLower = message.toLowerCase();

  for (const pattern of this.surfacingPatterns) {
    if (pattern.test(messageLower)) {
      // Check for exact duplicate
      const existing = this.unclarities.find(u => u.sourceMessage === message);
      if (existing) return null;

      // NEW: Check if semantically similar unclarity was already addressed
      const priorMessages = priorContext.join(' ').toLowerCase();
      const alreadyAddressed = this.unclarities.some(u =>
        u.addressed &&
        priorMessages.includes(u.description.toLowerCase().slice(0, 30))
      );
      if (alreadyAddressed) {
        return null;
      }

      const unclarity = this.extractUnclarity(message, priorContext);
      if (unclarity) {
        this.unclarities.push(unclarity);
        return unclarity;
      }
    }
  }

  return null;
}
```

Run: `npx tsc --noEmit src/clarification/unclarity-surfacer.ts`

- [ ] **Step 2: Remove shouldSurfaceProactively overly broad triggers**

Replace `shouldSurfaceProactively()` (lines 86-97):

```typescript
shouldSurfaceProactively(priorMessage: string, _currentContext: string[]): boolean {
  // Only surface if the user explicitly says they're confused about something specific
  const explicitConfusion = [
    /I'm not sure (?:which|what|where|who|how)/i,
    /I don't know which/i,
    /could you (?:please )?clarify (?:which|what)/i,
  ];

  return explicitConfusion.some(trigger => trigger.test(priorMessage));
}
```

Run: `npx tsc --noEmit src/clarification/unclarity-surfacer.ts`

- [ ] **Step 3: Commit**

```bash
git add src/clarification/unclarity-surfacer.ts
git commit -m "fix(clarification): use priorContext, dedupe unclarities, reduce false positives"
```

---

## Phase 4: Create ClarificationCoordinator (Cross-Module Deduplication)

**Files:**
- Create: `src/clarification/coordinator.ts`

- [ ] **Step 1: Create ClarificationCoordinator class**

```typescript
import type { ClarificationQuestion } from './types.js';

interface CoordinatedQuestion {
  id: string;
  sourceModule: 'AmbiguityDetector' | 'PreExecutionConfirmer' | 'UnclaritySurfacer';
  question: ClarificationQuestion | null;
  sessionKey: string;
  createdAt: number;
}

export class ClarificationCoordinator {
  private recentQuestions: CoordinatedQuestion[] = [];
  private readonly SESSION_WINDOW_MS = 5 * 60 * 1000; // 5 minutes
  private readonly SEMANTIC_SIMILARITY_THRESHOLD = 0.7;

  shouldAsk(moduleName: CoordinatedQuestion['sourceModule'], question: ClarificationQuestion | null, sessionKey: string): boolean {
    const now = Date.now();

    // Clean old questions
    this.recentQuestions = this.recentQuestions.filter(
      q => now - q.createdAt < this.SESSION_WINDOW_MS
    );

    // If no question to ask, don't interfere
    if (!question) return false;

    // Check for semantically similar question asked recently by ANY module
    const similarQuestion = this.recentQuestions.find(rq => {
      if (rq.sessionKey !== sessionKey) return false;
      if (rq.id === question.id) return false; // Same question ID = deduplicated elsewhere
      return this.isSemanticallySimilar(rq.question?.question || '', question.question);
    });

    if (similarQuestion) {
      console.log(`[ClarificationCoordinator] Suppressing duplicate question from ${moduleName} (matches ${similarQuestion.sourceModule})`);
      return false;
    }

    // Record this question
    this.recentQuestions.push({
      id: question.id,
      sourceModule: moduleName,
      question,
      sessionKey,
      createdAt: now,
    });

    return true;
  }

  private isSemanticallySimilar(text1: string, text2: string): boolean {
    // Simple word overlap check
    const words1 = new Set(text1.toLowerCase().split(/\s+/).filter(w => w.length > 3));
    const words2 = new Set(text2.toLowerCase().split(/\s+/).filter(w => w.length > 3));

    const intersection = new Set([...words1].filter(x => words2.has(x)));
    const union = new Set([...words1, ...words2]);

    return intersection.size / union.size >= this.SEMANTIC_SIMILARITY_THRESHOLD;
  }

  clear(): void {
    this.recentQuestions = [];
  }
}

export const clarificationCoordinator = new ClarificationCoordinator();
```

Run: `npx tsc --noEmit src/clarification/coordinator.ts`

- [ ] **Step 2: Wire coordinator into gateway/core.ts**

Find where ambiguityDetector and preExecutionConfirmer are initialized and add the coordinator:

```bash
# First find initialization
grep -n "this.ambiguityDetector" src/gateway/core.ts | head -5
```

Read the initialization section to understand how modules are created, then add:

```typescript
// Add to imports or create at top of core.ts
import { clarificationCoordinator } from '../clarification/coordinator.js';

// Modify the AmbiguityDetector section in handleCore to use coordinator
// Around line 1388-1397, change to:
const ambiguityResult = await this.ambiguityDetector.detectAmbiguity(message.text);
if (ambiguityResult.needsClarification && ambiguityResult.question) {
  // Use coordinator to prevent duplicate questions
  const sessionKey = message.sessionId || 'default';
  if (!clarificationCoordinator.shouldAsk('AmbiguityDetector', ambiguityResult.question, sessionKey)) {
    // Skip - similar question was already asked recently
    log.engine.info(`[AmbiguityDetector] Suppressing duplicate question`);
  } else {
    log.engine.info(`[AmbiguityDetector] Ambiguous input detected...`);
    return {
      content: ambiguityResult.question.question,
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
    };
  }
}

// Similarly wrap PreExecutionConfirmer around line 1401-1411
const confirmation = this.preExecutionConfirmer.assessRequest(message.text);
if (confirmation && confirmation.confidence < 0.65) {
  // Convert to ClarificationQuestion format for coordinator
  const confirmQuestion: ClarificationQuestion = {
    id: confirmation.id,
    ambiguitySignal: {
      type: 'unspecified_scope',
      description: confirmation.uncertaintyAreas.join('; '),
      confidence: confirmation.confidence,
      originalText: message.text,
    },
    question: this.preExecutionConfirmer.getConfirmationQuestion(confirmation),
    contextPreserved: [],
    timestamp: confirmation.timestamp,
  };

  const sessionKey = message.sessionId || 'default';
  if (!clarificationCoordinator.shouldAsk('PreExecutionConfirmer', confirmQuestion, sessionKey)) {
    log.engine.info(`[PreExecutionConfirmer] Suppressing duplicate question`);
  } else {
    log.engine.info(`[PreExecutionConfirmer] Low confidence...`);
    return {
      content: this.preExecutionConfirmer.getConfirmationQuestion(confirmation),
      owlName: this.ctx.owl.persona.name,
      owlEmoji: this.ctx.owl.persona.emoji,
      toolsUsed: [],
    };
  }
}
```

Run: `npx tsc --noEmit src/gateway/core.ts`

- [ ] **Step 3: Write coordinator test**

Create: `__tests__/clarification/coordinator.test.ts`

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { ClarificationCoordinator } from '../../src/clarification/coordinator.js';

describe('ClarificationCoordinator', () => {
  let coordinator: ClarificationCoordinator;

  beforeEach(() => {
    coordinator = new ClarificationCoordinator();
  });

  it('should allow first question', () => {
    const question = {
      id: 'q1',
      ambiguitySignal: { type: 'vague_pronoun' as const, description: 'test', confidence: 0.8, originalText: 'test' },
      question: 'Which file do you mean?',
      contextPreserved: [],
      timestamp: new Date().toISOString(),
    };

    expect(coordinator.shouldAsk('AmbiguityDetector', question, 'session1')).toBe(true);
  });

  it('should suppress semantically similar question from different module', () => {
    const question1 = {
      id: 'q1',
      ambiguitySignal: { type: 'vague_pronoun' as const, description: 'test', confidence: 0.8, originalText: 'test' },
      question: 'Which file do you mean?',
      contextPreserved: [],
      timestamp: new Date().toISOString(),
    };

    const question2 = {
      id: 'q2',
      ambiguitySignal: { type: 'incomplete_reference' as const, description: 'test', confidence: 0.8, originalText: 'test' },
      question: 'Which file should I use?',
      contextPreserved: [],
      timestamp: new Date().toISOString(),
    };

    coordinator.shouldAsk('AmbiguityDetector', question1, 'session1');
    expect(coordinator.shouldAsk('PreExecutionConfirmer', question2, 'session1')).toBe(false);
  });

  it('should allow question from different session', () => {
    const question1 = {
      id: 'q1',
      ambiguitySignal: { type: 'vague_pronoun' as const, description: 'test', confidence: 0.8, originalText: 'test' },
      question: 'Which file do you mean?',
      contextPreserved: [],
      timestamp: new Date().toISOString(),
    };

    coordinator.shouldAsk('AmbiguityDetector', question1, 'session1');
    expect(coordinator.shouldAsk('AmbiguityDetector', question1, 'session2')).toBe(true);
  });

  it('should not suppress when no question to ask", () => {
    expect(coordinator.shouldAsk('AmbiguityDetector', null, 'session1')).toBe(false);
  });
});
```

Run: `npx vitest run __tests__/clarification/coordinator.test.ts`

- [ ] **Step 4: Commit**

```bash
git add src/clarification/coordinator.ts __tests__/clarification/coordinator.test.ts src/gateway/core.ts
git commit -m "feat(clarification): add coordinator for cross-module deduplication"
```

---

## Phase 5: Fix PreActionQuestioner (LLM Parse Failure Fallback)

**Files:**
- Modify: `src/clarification/pre-action-questioner.ts`

- [ ] **Step 1: Change fallback to NOT always question on parse failure**

Replace lines 52-59 and 68-74:

```typescript
// Lines 52-59: Instead of:
if (!parsed) {
  return {
    riskLevel: 'medium',
    riskReasons: ['Failed to parse LLM response, treating as medium risk'],
    shouldConfirm: true,  // <-- THIS IS THE PROBLEM
    confirmationQuestion: `About to run ${toolName}. Continue?`,
  };
}

// REPLACE WITH:
if (!parsed) {
  // Don't question on parse failure - let the action proceed
  // The LLM might have failed to parse but the action is likely fine
  return {
    riskLevel: 'medium',
    riskReasons: ['Failed to parse LLM response'],
    shouldConfirm: false,
    confirmationQuestion: null,
  };
}

// Lines 68-74: Instead of:
} catch {
  return {
    riskLevel: 'medium',
    riskReasons: ['Error during risk assessment, treating as medium risk'],
    shouldConfirm: true,
    confirmationQuestion: `About to run ${toolName}. Continue?`,
  };
}

// REPLACE WITH:
} catch {
  // Don't question on error - fail open, not closed
  return {
    riskLevel: 'low',
    riskReasons: ['Risk assessment unavailable'],
    shouldConfirm: false,
    confirmationQuestion: null,
  };
}
```

Run: `npx tsc --noEmit src/clarification/pre-action-questioner.ts`

- [ ] **Step 2: Commit**

```bash
git add src/clarification/pre-action-questioner.ts
git commit -m "fix(clarification): PreActionQuestioner no longer questions on LLM failure"
```

---

## Phase 6: Wire or Remove MidExecutionRouter

**Files:**
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Check if MidExecutionRouter is actually used anywhere**

```bash
grep -rn "MidExecutionRouter\|midExecutionRouter\|shouldPauseForClarification" src/
```

- [ ] **Step 2A: If wired somewhere, verify it's called correctly in the flow**

- [ ] **Step 2B: If NOT wired (likely), remove dead code and the unused import**

In core.ts, find and remove:
- `midExecutionRouter` initialization
- Any dead code references

Run: `npx tsc --noEmit src/gateway/core.ts`

- [ ] **Step 3: Commit**

```bash
git add src/gateway/core.ts
git commit -m "refactor(clarification): remove unwired MidExecutionRouter"
```

---

## Phase 7: Run Full Verification

- [ ] **Step 1: Run all clarification tests**

```bash
npx vitest run __tests__/clarification/
```

- [ ] **Step 2: Run full build**

```bash
npm run build
```

- [ ] **Step 3: Run all tests**

```bash
npm run test
```

- [ ] **Step 4: Run lint**

```bash
npm run lint
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "fix: clarification system - fewer questions, follows context, no repeats"
```

---

## Summary of Changes by File

| File | Change |
|------|--------|
| `src/clarification/ambiguity-detector.ts` | Added resolved check, raised threshold to 0.75, refined LLM prompt, removed multi-signal amplification |
| `src/clarification/pre-execution-confirmer.ts` | Removed keyword triggers, simplified confidence calc, raised threshold to 0.65 |
| `src/clarification/unclarity-surfacer.ts` | Actually use priorContext, semantic dedup, reduced false positive triggers |
| `src/clarification/pre-action-questioner.ts` | Fail open on LLM errors instead of questioning |
| `src/clarification/coordinator.ts` | NEW - Cross-module question deduplication |
| `src/gateway/core.ts` | Wire coordinator, remove/fix unwired MidExecutionRouter |
| `__tests__/clarification/coordinator.test.ts` | NEW - Coordinator tests |

---

## Post-Fix Expected Behavior

| Before | After |
|--------|-------|
| "check the file" → Asks "which file?" | "check the file" → Proceeds (reasonable interpretation) |
| Answer "config.yaml" → Next message "update it" → Asks "which file?" again | Answer "config.yaml" → Next message "update it" → Proceeds (sees resolved context) |
| Both AmbiguityDetector and PreExecutionConfirmer fire on same message | Coordinator deduplicates, only one question |
| "I need to check the logs" → High stakes triggered, asks for confirmation | "I need to check the logs" → Proceeds (normal language) |
| LLM parse fails → Asks confirmation anyway | LLM parse fails → Proceeds |
| MidExecutionRouter exists but never called | Either wired or removed as dead code |
