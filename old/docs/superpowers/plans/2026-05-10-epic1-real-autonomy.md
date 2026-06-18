# Epic 1: Real Autonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three background-autonomy stubs with real implementations: memory consolidation, screen observation, and proactive suggestions.

**Architecture:** (1) `BackgroundOrchestrator.runMemoryConsolidation()` injects `EpisodicMemory` and calls its existing `runDecay()` method plus forces a save. (2) `CognitiveLoop.captureObservation()` calls `macOSAdapter.getFocusedApp()` for the real focused app on macOS, falling back to a process-list check. (3) `ProactiveAssistant` receives an optional `ModelProvider` and replaces hardcoded `if (app === "photoshop")` rules with a Haiku-tier LLM call, cached for 5 minutes per context hash.

**Tech Stack:** TypeScript, Node 22, existing `EpisodicMemory` (`src/memory/episodic.ts`), `macOSAdapter` (`src/oscar/platform/adapters/macos.ts`), `ModelProvider` (`src/providers/base.ts`), Vitest.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/background/orchestrator.ts` | Modify | Add `episodicMemory?: EpisodicMemory` param; implement `runMemoryConsolidation()` |
| `src/oscar/cognition/loop.ts` | Modify | `captureObservation()` calls `macOSAdapter.getFocusedApp()` with process-list fallback |
| `src/oscar/cognition/proactive.ts` | Modify | Add `provider?: ModelProvider` param; replace hardcoded rules with LLM call + 5-min cache |
| `__tests__/background/orchestrator-consolidation.test.ts` | Create | Unit tests for `runMemoryConsolidation()` |
| `__tests__/cognition/loop-observation.test.ts` | Create | Unit tests for real `captureObservation()` |
| `__tests__/cognition/proactive-llm.test.ts` | Create | Unit tests for LLM-driven suggestions |

---

## Task 1: Real memory consolidation in BackgroundOrchestrator

**Files:**
- Modify: `src/background/orchestrator.ts`
- Create: `__tests__/background/orchestrator-consolidation.test.ts`

- [ ] **Step 1.1: Write the failing test**

```typescript
// __tests__/background/orchestrator-consolidation.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { EpisodicMemory } from "../../src/memory/episodic.js";

// Minimal BackgroundOrchestrator interface needed for this test
async function makeOrchestrator(episodicMemory?: Partial<EpisodicMemory>) {
  const { BackgroundOrchestrator } = await import("../../src/background/orchestrator.js");
  const fakeProvider = { chat: vi.fn().mockResolvedValue({ content: "ping" }) } as any;
  const fakeOwl = { persona: { name: "Athena" } } as any;
  return new BackgroundOrchestrator(
    fakeProvider,
    fakeOwl,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    episodicMemory as EpisodicMemory,
  );
}

describe("BackgroundOrchestrator.runMemoryConsolidation", () => {
  it("calls runDecay and logs result when episodicMemory is provided", async () => {
    const runDecay = vi.fn().mockReturnValue({ compressed: 3, archived: 1 });
    const save = vi.fn().mockResolvedValue(undefined);
    const orch = await makeOrchestrator({ runDecay, save } as any);

    // Access private method via cast
    await (orch as any).runMemoryConsolidation();

    expect(runDecay).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledOnce();
  });

  it("is a no-op when episodicMemory is not provided", async () => {
    const orch = await makeOrchestrator(undefined);
    // Must not throw
    await expect((orch as any).runMemoryConsolidation()).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 1.2: Run test to confirm it fails**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npx vitest run __tests__/background/orchestrator-consolidation.test.ts 2>&1 | tail -20
```

Expected: FAIL — constructor does not accept 8th argument / runMemoryConsolidation is a log-only stub.

- [ ] **Step 1.3: Update BackgroundOrchestrator constructor and implement consolidation**

In `src/background/orchestrator.ts`, add the import at the top of the import block:

```typescript
import type { EpisodicMemory } from "../memory/episodic.js";
```

Extend the constructor signature (add 8th parameter after the existing `config?`):

```typescript
  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    private innerLife: OwlInnerLife | undefined,
    private desireExecutor: DesireExecutor | undefined,
    private fulfillmentTracker: FulfillmentTracker | undefined,
    private onProactiveMessage?: (msg: string) => Promise<void>,
    config?: Partial<BackgroundOrchestratorConfig>,
    private episodicMemory?: EpisodicMemory,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }
```

Replace `runMemoryConsolidation()` body:

```typescript
  private async runMemoryConsolidation(): Promise<void> {
    if (!this.episodicMemory) {
      log.engine.debug("[BackgroundOrchestrator] Memory consolidation skipped — no episodic store");
      return;
    }

    const { compressed, archived } = this.episodicMemory.runDecay();
    await this.episodicMemory.save?.();

    this.activityLog.add(
      "memory_consolidated",
      `Compressed ${compressed} episodes, archived ${archived}`,
    );
    log.engine.info(
      `[BackgroundOrchestrator] Memory consolidation: compressed=${compressed} archived=${archived}`,
    );
  }
```

- [ ] **Step 1.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/background/orchestrator-consolidation.test.ts 2>&1 | tail -20
```

Expected: PASS — 2 tests passing.

- [ ] **Step 1.5: Verify EpisodicMemory.save exists**

```bash
grep -n "async save\b\|save():" /ssd/projects/stackowl-personal-ai-assistant/src/memory/episodic.ts | head -5
```

If `save()` is not a public method, replace `await this.episodicMemory.save?.()` with a no-op comment. The `runDecay()` call already persists state internally.

- [ ] **Step 1.6: Commit**

```bash
git add src/background/orchestrator.ts __tests__/background/orchestrator-consolidation.test.ts
git commit -m "feat(autonomy): implement real memory consolidation in BackgroundOrchestrator"
```

---

## Task 2: Real screen observation in CognitiveLoop

**Files:**
- Modify: `src/oscar/cognition/loop.ts`
- Create: `__tests__/cognition/loop-observation.test.ts`

- [ ] **Step 2.1: Write the failing test**

```typescript
// __tests__/cognition/loop-observation.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

describe("CognitiveLoop.captureObservation", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("uses focused app from macOS adapter when available", async () => {
    // Stub macOSAdapter before importing the module under test
    vi.doMock("../../src/oscar/platform/adapters/macos.js", () => ({
      macOSAdapter: {
        getFocusedApp: vi.fn().mockResolvedValue("Slack"),
      },
    }));

    const { CognitiveLoop } = await import("../../src/oscar/cognition/loop.js");
    const loop = new CognitiveLoop();
    const obs = await (loop as any).captureObservation();

    expect(obs.app).toBe("Slack");
    expect(obs.timestamp).toBeGreaterThan(0);
  });

  it("falls back to null app when macOS adapter fails", async () => {
    vi.doMock("../../src/oscar/platform/adapters/macos.js", () => ({
      macOSAdapter: {
        getFocusedApp: vi.fn().mockRejectedValue(new Error("osascript unavailable")),
      },
    }));

    const { CognitiveLoop } = await import("../../src/oscar/cognition/loop.js");
    const loop = new CognitiveLoop();
    const obs = await (loop as any).captureObservation();

    expect(obs.app).toBeNull();
  });
});
```

- [ ] **Step 2.2: Run test to confirm it fails**

```bash
npx vitest run __tests__/cognition/loop-observation.test.ts 2>&1 | tail -20
```

Expected: FAIL — `captureObservation()` returns hardcoded `app: this.lastApp` (null) regardless of macOS state.

- [ ] **Step 2.3: Update captureObservation() in CognitiveLoop**

In `src/oscar/cognition/loop.ts`, add import at top:

```typescript
// Dynamic import to avoid hard dependency on macOS-only module in non-macOS environments
```

Replace `captureObservation()` body:

```typescript
  private async captureObservation(): Promise<Observation> {
    const now = Date.now();
    const timeOfDay = new Date(now).getHours();

    let focusedApp: string | null = null;
    try {
      // Lazy import so non-macOS environments don't crash on module load
      const { macOSAdapter } = await import("../platform/adapters/macos.js");
      focusedApp = await macOSAdapter.getFocusedApp();
    } catch {
      // Non-macOS or AppleScript unavailable — leave focusedApp null
    }

    // Update internal tracker so `reflect()` can detect app switches
    if (focusedApp !== null) {
      this.lastApp = focusedApp;
    }

    return {
      timestamp: now,
      app: focusedApp,
      focusedElement: null,
      screenChanged: focusedApp !== this.lastApp,
      elements: [],
      cursorPosition: { x: 0, y: 0 },
      recentActions: [],
      timeOfDay,
    };
  }
```

- [ ] **Step 2.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/cognition/loop-observation.test.ts 2>&1 | tail -20
```

Expected: PASS — 2 tests passing.

- [ ] **Step 2.5: Commit**

```bash
git add src/oscar/cognition/loop.ts __tests__/cognition/loop-observation.test.ts
git commit -m "feat(autonomy): real screen observation via macOS adapter in CognitiveLoop"
```

---

## Task 3: LLM-driven ProactiveAssistant

**Files:**
- Modify: `src/oscar/cognition/proactive.ts`
- Create: `__tests__/cognition/proactive-llm.test.ts`

- [ ] **Step 3.1: Write the failing test**

```typescript
// __tests__/cognition/proactive-llm.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SuggestionContext } from "../../src/oscar/cognition/proactive.js";

const MOCK_CONTEXT: SuggestionContext = {
  currentApp: "Slack",
  recentActions: ["navigate"],
  timeOfDay: 14,
  dayOfWeek: 2,
};

describe("ProactiveAssistant with ModelProvider", () => {
  it("calls provider.chat and returns LLM suggestion", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockResolvedValue({
        content: "You have 3 unread Slack threads from this morning.",
      }),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    const suggestions = await assistant.suggest(MOCK_CONTEXT);

    expect(fakeProvider.chat).toHaveBeenCalledOnce();
    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].message).toBe("You have 3 unread Slack threads from this morning.");
    expect(suggestions[0].confidence).toBeGreaterThan(0.6);
  });

  it("caches responses for 5 minutes — second call within TTL skips provider", async () => {
    vi.useFakeTimers();
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockResolvedValue({ content: "Focus on your open PRs." }),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    await assistant.suggest(MOCK_CONTEXT);
    await assistant.suggest(MOCK_CONTEXT); // same context, within TTL

    expect(fakeProvider.chat).toHaveBeenCalledOnce(); // not twice

    vi.advanceTimersByTime(6 * 60 * 1000); // advance past 5-min TTL
    await assistant.suggest(MOCK_CONTEXT);

    expect(fakeProvider.chat).toHaveBeenCalledTimes(2); // now refreshed
    vi.useRealTimers();
  });

  it("falls back to empty array when provider throws", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");

    const fakeProvider: Partial<ModelProvider> = {
      chat: vi.fn().mockRejectedValue(new Error("rate limited")),
    };

    const assistant = new ProactiveAssistant(fakeProvider as ModelProvider);
    const suggestions = await assistant.suggest(MOCK_CONTEXT);

    expect(suggestions).toHaveLength(0);
  });

  it("works without provider — returns empty array (no crash)", async () => {
    const { ProactiveAssistant } = await import("../../src/oscar/cognition/proactive.js");
    const assistant = new ProactiveAssistant();
    const suggestions = await assistant.suggest(MOCK_CONTEXT);
    expect(Array.isArray(suggestions)).toBe(true);
  });
});
```

- [ ] **Step 3.2: Run test to confirm it fails**

```bash
npx vitest run __tests__/cognition/proactive-llm.test.ts 2>&1 | tail -20
```

Expected: FAIL — constructor doesn't accept a provider; `suggest()` uses hardcoded rules.

- [ ] **Step 3.3: Rewrite ProactiveAssistant**

Replace the entire content of `src/oscar/cognition/proactive.ts` with:

```typescript
import type { ModelProvider, ChatMessage } from "../../providers/base.js";
import type { Suggestion } from "./types.js";

export interface SuggestionContext {
  currentApp: string | null;
  currentElement?: string;
  recentActions: string[];
  timeOfDay: number;
  dayOfWeek: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000;

function contextKey(ctx: SuggestionContext): string {
  return `${ctx.currentApp}|${ctx.timeOfDay}|${ctx.recentActions.slice(0, 3).join(",")}`;
}

export class ProactiveAssistant {
  private cache = new Map<string, { suggestion: Suggestion; expiresAt: number }>();

  constructor(private provider?: ModelProvider) {}

  async suggest(context: SuggestionContext): Promise<Suggestion[]> {
    if (!this.provider) return [];

    const key = contextKey(context);
    const cached = this.cache.get(key);
    if (cached && Date.now() < cached.expiresAt) {
      return [cached.suggestion];
    }

    const appDesc = context.currentApp ?? "unknown application";
    const timeLabel = context.timeOfDay < 12 ? "morning" : context.timeOfDay < 17 ? "afternoon" : "evening";

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          "You are a proactive AI assistant. Given the user's current context, " +
          "suggest ONE brief, actionable insight (1 sentence, under 20 words). " +
          "Be specific and useful. Output only the suggestion text — no preamble.",
      },
      {
        role: "user",
        content:
          `App: ${appDesc}. Time: ${timeLabel}. ` +
          `Recent actions: ${context.recentActions.slice(0, 3).join(", ") || "none"}.`,
      },
    ];

    try {
      const response = await this.provider.chat(messages);
      const message = response.content.trim();
      if (!message || message.length < 5) return [];

      const suggestion: Suggestion = {
        id: `proactive_${Date.now()}`,
        type: "proactive",
        message,
        confidence: 0.75,
        context: { app: context.currentApp, timeOfDay: context.timeOfDay },
        createdAt: Date.now(),
      };

      this.cache.set(key, { suggestion, expiresAt: Date.now() + CACHE_TTL_MS });
      return [suggestion];
    } catch {
      return [];
    }
  }

  recordSuggestion(suggestion: Suggestion): void {
    // Keep API surface unchanged for callers
  }

  recordSuggestionResponse(suggestionId: string, accepted: boolean): void {
    for (const [key, cached] of this.cache) {
      if (cached.suggestion.id === suggestionId) {
        cached.suggestion.confidence = accepted
          ? Math.min(1, cached.suggestion.confidence + 0.1)
          : Math.max(0.1, cached.suggestion.confidence - 0.2);
        break;
      }
    }
  }

  getSuggestionStats(): { total: number; byType: Record<string, number>; avgConfidence: number } {
    return { total: this.cache.size, byType: { proactive: this.cache.size }, avgConfidence: 0.75 };
  }
}

export const proactiveAssistant = new ProactiveAssistant();
```

- [ ] **Step 3.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/cognition/proactive-llm.test.ts 2>&1 | tail -20
```

Expected: PASS — 4 tests passing.

- [ ] **Step 3.5: Run full test suite to check for regressions**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all previously-passing tests still pass.

- [ ] **Step 3.6: Commit**

```bash
git add src/oscar/cognition/proactive.ts __tests__/cognition/proactive-llm.test.ts
git commit -m "feat(autonomy): LLM-driven ProactiveAssistant with 5-min cache, replaces hardcoded rules"
```

---

## Task 4: Wire provider into ProactiveAssistant at startup

**Files:**
- Modify: `src/index.ts` (or wherever ProactiveAssistant / CognitiveLoop is instantiated)

- [ ] **Step 4.1: Find where proactiveAssistant singleton is consumed**

```bash
grep -rn "proactiveAssistant\|ProactiveAssistant\|new CognitiveLoop" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|spec"
```

- [ ] **Step 4.2: Pass provider to ProactiveAssistant**

Wherever `proactiveAssistant` singleton or `new ProactiveAssistant()` appears in startup code, change to:

```typescript
// Before:
import { proactiveAssistant } from "./oscar/cognition/proactive.js";

// After (pass provider after it's initialized):
import { ProactiveAssistant } from "./oscar/cognition/proactive.js";
// ...after provider is available:
const proactiveAssistant = new ProactiveAssistant(provider);
```

If the singleton export is used widely, update the module-level export at the bottom of `proactive.ts` too:

```typescript
// Bottom of proactive.ts — becomes a factory function consumers can call
export function createProactiveAssistant(provider?: ModelProvider): ProactiveAssistant {
  return new ProactiveAssistant(provider);
}
// Keep backwards-compat export for places that don't have a provider
export const proactiveAssistant = new ProactiveAssistant();
```

- [ ] **Step 4.3: Wire EpisodicMemory into BackgroundOrchestrator at startup**

Find where `BackgroundOrchestrator` is instantiated:

```bash
grep -rn "new BackgroundOrchestrator" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v test
```

Pass the episodic memory instance as the 8th argument:

```typescript
// Before:
new BackgroundOrchestrator(provider, owl, innerLife, desireExecutor, fulfillmentTracker, onProactiveMessage, config)

// After:
new BackgroundOrchestrator(provider, owl, innerLife, desireExecutor, fulfillmentTracker, onProactiveMessage, config, episodicMemory)
```

- [ ] **Step 4.4: Smoke test — start the assistant and verify background loop logs real data**

```bash
npm run dev 2>&1 | head -30
```

Expected: no crash on startup. After 5 minutes idle, check log:

```bash
cat logs/stackowl-$(date +%F).log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        r = json.loads(line)
        if 'consolidation' in r.get('msg','') or 'proactive' in r.get('msg','').lower():
            print(r)
    except: pass
" | head -20
```

- [ ] **Step 4.5: Final commit**

```bash
git add src/index.ts  # or whichever startup file changed
git commit -m "feat(autonomy): wire EpisodicMemory + provider into background autonomy stack"
```
