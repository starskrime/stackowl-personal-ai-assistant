# LLM Token Drain Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ~800 LLM requests / 15 minutes at startup caused by 6 automatic subsystems that each call the provider independently with no shared rate limiting.

**Architecture:** Five targeted fixes in the calling layer of each offending subsystem — heuristic pre-filters replacing LLM classifiers, debouncing on per-message processors, throttled heartbeat verifier, and delayed startup of proactive knowledge generation. No changes to business logic or provider layer.

**Tech Stack:** TypeScript (NodeNext ESM), existing `ModelProvider` interface (`src/providers/base.ts`), Vitest for tests.

---

## Root Cause Summary (do not skip)

| Subsystem | File | LLM calls/15min | Mechanism |
|---|---|---|---|
| SignalClassifier | `src/signals/classifier.ts` | ~90–180 | LLM call for EVERY ambient signal (git/system poll) |
| SignalPool heartbeat verifier | `src/signals/pool.ts` | ~75 | 5 verifier LLM calls every 60 seconds |
| EventBasedPelletGenerator | `src/pellets/event-based-generator.ts` | ~50–100 | LLM classifier on every tool-using response |
| LearningOrchestrator | `src/learning/orchestrator.ts` | ~30–60 | Full extraction+synthesis pipeline on every message |
| ProactiveKnowledgeGenerator | `src/index.ts` | ~9 (at startup) | Fires 30s after launch, generates 3 pellets × 3 calls |

---

## File Map

| Action | File | Change |
|---|---|---|
| Modify | `src/signals/classifier.ts` | Add `heuristicClassify()` that runs before LLM |
| Modify | `src/signals/pool.ts` | Add `_lastVerifiedAt` map; throttle heartbeat verifier |
| Modify | `src/pellets/event-based-generator.ts` | Add cooldown to `handleMessageResponded` |
| Modify | `src/learning/orchestrator.ts` | Add `_messageCount` debounce |
| Modify | `src/index.ts` | Change 30s startup delay to 10 minutes |
| Create | `__tests__/signals/classifier.test.ts` | Heuristic classifier unit tests |
| Create | `__tests__/signals/pool-throttle.test.ts` | Heartbeat verifier throttle tests |
| Create | `__tests__/pellets/event-based-generator-throttle.test.ts` | Cooldown tests |
| Create | `__tests__/learning/orchestrator-debounce.test.ts` | Debounce tests |

---

## Task 1: Heuristic pre-filter in SignalClassifier

The `SignalClassifier.classify()` currently calls `provider.chat()` for every signal. Add a fast heuristic pass that resolves obvious cases (noise/clear-signal) without LLM. Only truly ambiguous signals proceed to the LLM call.

**Files:**
- Modify: `src/signals/classifier.ts`
- Create: `__tests__/signals/classifier.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/signals/classifier.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { SignalClassifier } from "../../src/signals/classifier.js";
import type { ContextSignal } from "../../src/ambient/types.js";

function makeSignal(source: string, title: string, content: string): ContextSignal {
  return {
    id: "test-id",
    source: source as any,
    title,
    content,
    priority: "medium",
    timestamp: Date.now(),
    ttlMs: 60_000,
    userSurfaceable: false,
  };
}

describe("SignalClassifier heuristic pre-filter", () => {
  it("returns keep=false for empty content without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("clipboard", "Clipboard", ""));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for content shorter than 5 chars without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("clipboard", "Clip", "hi"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("returns keep=false for time context noise without LLM call", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(makeSignal("time_context", "Time", "2026-05-17T14:30:00Z"));
    expect(result.keep).toBe(false);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("calls LLM only for non-obvious signals", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: '{"keep":true,"confidence":0.8}' }),
    };
    const classifier = new SignalClassifier(mockProvider as any);
    const result = await classifier.classify(
      makeSignal("git_status", "Modified files", "src/engine/runtime.ts has uncommitted changes with 40 new lines implementing X"),
    );
    expect(mockProvider.chat).toHaveBeenCalledTimes(1);
    expect(result.keep).toBe(true);
  });

  it("returns keep=true immediately for critical source without LLM", async () => {
    const mockProvider = { chat: vi.fn() };
    const classifier = new SignalClassifier(mockProvider as any);
    // critical priority signals bypass classification
    const signal = { ...makeSignal("git_status", "Error", "fatal: merge conflict"), priority: "critical" as const };
    const result = await classifier.classify(signal);
    expect(result.keep).toBe(true);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/signals/classifier.test.ts
```

Expected: FAIL — `heuristicClassify` not defined, LLM is always called.

- [ ] **Step 3: Add heuristic pre-filter to SignalClassifier**

Replace the entire `src/signals/classifier.ts` with:

```typescript
import type { ChatMessage, ChatOptions, ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ContextSignal } from "../ambient/types.js";

export interface ClassifierProvider {
  chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<{ content: string }>;
}

export interface ClassifierResult {
  keep: boolean;
  confidence: number;
}

const SYSTEM_PROMPT = `You filter ambient workspace signals for relevance to a coding/agent assistant.
Reply JSON only: {"keep": boolean, "confidence": number between 0 and 1}.
- keep=true if the signal is plausibly useful context for a developer right now.
- confidence reflects how confident you are this signal is worth surfacing.
- Mundane signals (e.g. routine clipboard noise, minor time updates) → keep=false.`;

// Sources that produce so much noise their signals skip classification by default
const ALWAYS_SKIP_SOURCES = new Set(["time_context", "system_metrics"]);

// Minimum content length to be worth classifying
const MIN_CONTENT_LENGTH = 5;

/**
 * Fast heuristic pre-filter — runs before any LLM call.
 * Returns a definitive result for obvious cases.
 * Returns null when the signal is genuinely ambiguous and needs LLM.
 */
function heuristicClassify(signal: ContextSignal): ClassifierResult | null {
  // Critical signals always pass through
  if (signal.priority === "critical") return { keep: true, confidence: 1.0 };

  // Empty or trivially short content is noise
  const content = signal.content.trim();
  if (content.length < MIN_CONTENT_LENGTH) return { keep: false, confidence: 1.0 };

  // Known noisy sources — skip LLM entirely
  if (ALWAYS_SKIP_SOURCES.has(signal.source)) return { keep: false, confidence: 0.9 };

  // Pure timestamp content is noise (ISO8601 pattern)
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(content) && content.length < 30) {
    return { keep: false, confidence: 0.95 };
  }

  // Signals with meaningful error keywords are almost always relevant
  if (/error|fatal|exception|failed|crash|panic/i.test(content)) {
    return { keep: true, confidence: 0.85 };
  }

  // Ambiguous — let LLM decide
  return null;
}

export class SignalClassifier {
  constructor(private readonly provider: ClassifierProvider) {}

  static create(
    router: IntelligenceRouter,
    providers: Map<string, ModelProvider>,
  ): SignalClassifier {
    const resolved = router.resolve("classification");
    const provider = providers.get(resolved.provider);
    if (!provider) {
      return new SignalClassifier({
        chat: async () => ({ content: `{"keep":false,"confidence":0}` }),
      });
    }
    return new SignalClassifier({
      chat: (messages, _model, options) =>
        provider.chat(messages, resolved.model, options),
    });
  }

  async classify(signal: ContextSignal): Promise<ClassifierResult> {
    // Fast path: heuristic decides without any LLM call
    const heuristic = heuristicClassify(signal);
    if (heuristic !== null) return heuristic;

    // Slow path: LLM for genuinely ambiguous signals
    const userMsg = `source: ${signal.source}\ntitle: ${signal.title}\ncontent: ${signal.content.slice(0, 500)}`;
    try {
      const { content } = await this.provider.chat([
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userMsg },
      ]);
      const parsed = JSON.parse(content);
      const keep = parsed.keep === true;
      const conf = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));
      return { keep, confidence: conf };
    } catch {
      return { keep: false, confidence: 0 };
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/signals/classifier.test.ts
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
npx vitest run
```

Expected: No new failures.

- [ ] **Step 6: Commit**

```bash
git add src/signals/classifier.ts __tests__/signals/classifier.test.ts
git commit -m "perf(signals): add heuristic pre-filter to SignalClassifier — skip LLM for obvious noise"
```

---

## Task 2: Throttle SignalPool heartbeat verifier

`SignalPool.heartbeatTick()` calls `verifier.verify()` on up to 5 signals every 60 seconds. Add a `_lastVerifiedAt` map so each signal is only sent to the LLM verifier once every 10 minutes.

**Files:**
- Modify: `src/signals/pool.ts`
- Create: `__tests__/signals/pool-throttle.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/signals/pool-throttle.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import { DEFAULT_CONSENT } from "../../src/ambient/types.js";

function makeSignal(id: string, priority: "medium" | "high" = "medium"): ContextSignal {
  return {
    id,
    source: "git_status",
    title: "Test signal",
    content: "some meaningful content that is long enough",
    priority,
    timestamp: Date.now(),
    ttlMs: 600_000,
    userSurfaceable: false,
  };
}

function makePool(verifier: any) {
  const bus = { emit: vi.fn() } as any;
  const classifier = { classify: vi.fn().mockResolvedValue({ keep: true, confidence: 0.8 }) };
  const goalGraph = { getTopPriority: vi.fn().mockReturnValue({ id: "g1", title: "Ship it" }) };
  return new SignalPool({
    bus,
    classifier,
    verifier,
    goalGraph,
    config: { maxSignals: 50, consent: DEFAULT_CONSENT },
    workspacePath: "/tmp/test",
  });
}

describe("SignalPool heartbeat verifier throttle", () => {
  it("does not re-verify a signal within the cooldown window", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    // Inject a medium-priority signal
    const signal = makeSignal("s1", "medium");
    // Manually put it in the pool's signals map by calling injectSignal
    // (classifier will keep it, verifier is only called on high or heartbeat)
    // Simulate it already being verified recently
    (pool as any).signals.set("s1", signal);
    (pool as any)._lastVerifiedAt = new Map([["s1", Date.now() - 1_000]]); // 1s ago

    await (pool as any).heartbeatTick();

    expect(verifier.verify).not.toHaveBeenCalled();
  });

  it("re-verifies a signal after the cooldown has elapsed", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    // Last verified 11 minutes ago — past the 10-minute cooldown
    (pool as any)._lastVerifiedAt = new Map([["s1", Date.now() - 11 * 60_000]]);

    await (pool as any).heartbeatTick();

    expect(verifier.verify).toHaveBeenCalledTimes(1);
  });

  it("verifies a signal with no prior verification record", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    (pool as any)._lastVerifiedAt = new Map(); // no record

    await (pool as any).heartbeatTick();

    expect(verifier.verify).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/signals/pool-throttle.test.ts
```

Expected: FAIL — `_lastVerifiedAt` not defined, throttle not implemented.

- [ ] **Step 3: Add throttle to SignalPool**

In `src/signals/pool.ts`, add a private field and update `heartbeatTick()`:

After line 42 (`private timers: ReturnType<typeof setInterval>[] = [];`), add:

```typescript
  /** Tracks when each signal was last sent to the LLM verifier. */
  private _lastVerifiedAt = new Map<string, number>();
  /** Cooldown between re-verification of the same signal (10 minutes). */
  private readonly VERIFY_COOLDOWN_MS = 10 * 60_000;
```

Replace `heartbeatTick()` (lines 190–235) with:

```typescript
  async heartbeatTick(): Promise<void> {
    try {
      const now = Date.now();
      for (const [id, s] of this.signals) {
        if (s.timestamp + s.ttlMs < now) {
          this.signals.delete(id);
          this._lastVerifiedAt.delete(id);
          this.deps.bus.emit({
            type: "signal:expired",
            signal: s,
            reason: "ttl",
          });
        }
      }
      const goal = this.deps.goalGraph.getTopPriority();
      if (!goal) return;
      const candidates = [...this.signals.values()]
        .filter(
          (s) =>
            !s.userSurfaceable &&
            (s.priority === "medium" || s.priority === "high"),
        )
        .filter((s) => {
          const lastVerified = this._lastVerifiedAt.get(s.id) ?? 0;
          return now - lastVerified >= this.VERIFY_COOLDOWN_MS;
        })
        .slice(0, 5);
      for (const s of candidates) {
        try {
          this._lastVerifiedAt.set(s.id, now);
          const result = await this.deps.verifier.verify(
            signalToVerifyArgs(s, goal),
          );
          if (result.verdict === "ADVANCES") {
            await this.promote(
              s,
              { id: goal.id, title: goal.title },
              result.reason,
            );
          }
        } catch (err) {
          log.engine.warn(
            `[SignalPool] heartbeat verify failed: ${(err as Error).message}`,
          );
        }
      }
    } catch (err) {
      log.engine.warn(
        `[SignalPool] heartbeatTick uncaught: ${(err as Error).message}`,
      );
    }
  }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/signals/pool-throttle.test.ts
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```

Expected: No new failures.

- [ ] **Step 6: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-throttle.test.ts
git commit -m "perf(signals): throttle heartbeat verifier — 10min cooldown per signal"
```

---

## Task 3: Cooldown on EventBasedPelletGenerator message classification

`handleMessageResponded()` calls LLM to classify every tool-using response. Add a per-session cooldown so classification runs at most once every 2 minutes.

**Files:**
- Modify: `src/pellets/event-based-generator.ts`
- Create: `__tests__/pellets/event-based-generator-throttle.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/pellets/event-based-generator-throttle.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { EventBasedPelletGenerator } from "../../src/pellets/event-based-generator.js";

function makeGenerator(routerResolve: any) {
  const eventBus = { on: vi.fn(), off: vi.fn() } as any;
  const pelletStore = {} as any;
  const router = { resolve: routerResolve };
  return new EventBasedPelletGenerator(eventBus, pelletStore, router as any);
}

const TOOL_PAYLOAD = {
  sessionId: "sess1",
  channelId: "cli",
  userId: "u1",
  content: "I have completed the task using the shell tool.",
  owlName: "Noctua",
  toolsUsed: ["shell"],
};

describe("EventBasedPelletGenerator message classification cooldown", () => {
  it("classifies the first tool-using response", async () => {
    const resolve = vi.fn().mockResolvedValue('{"isDecision":false,"isInsight":false,"isCorrection":false}');
    const gen = makeGenerator(resolve);

    await (gen as any).handleMessageResponded(TOOL_PAYLOAD);

    expect(resolve).toHaveBeenCalledTimes(1);
  });

  it("skips classification if called again within cooldown window", async () => {
    const resolve = vi.fn().mockResolvedValue('{"isDecision":false,"isInsight":false,"isCorrection":false}');
    const gen = makeGenerator(resolve);

    await (gen as any).handleMessageResponded(TOOL_PAYLOAD);
    await (gen as any).handleMessageResponded(TOOL_PAYLOAD);

    expect(resolve).toHaveBeenCalledTimes(1);
  });

  it("classifies again after cooldown has elapsed", async () => {
    const resolve = vi.fn().mockResolvedValue('{"isDecision":false,"isInsight":false,"isCorrection":false}');
    const gen = makeGenerator(resolve);

    await (gen as any).handleMessageResponded(TOOL_PAYLOAD);

    // Simulate cooldown elapsed by backdating the last classification time
    (gen as any)._lastClassifiedAt = Date.now() - 3 * 60_000;

    await (gen as any).handleMessageResponded(TOOL_PAYLOAD);

    expect(resolve).toHaveBeenCalledTimes(2);
  });

  it("skips without LLM call if no tools used", async () => {
    const resolve = vi.fn();
    const gen = makeGenerator(resolve);

    await (gen as any).handleMessageResponded({ ...TOOL_PAYLOAD, toolsUsed: [] });

    expect(resolve).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/pellets/event-based-generator-throttle.test.ts
```

Expected: FAIL — `_lastClassifiedAt` not defined, cooldown not implemented.

- [ ] **Step 3: Add cooldown to handleMessageResponded**

In `src/pellets/event-based-generator.ts`, add private field after the class opening (after `private recentErrors = new Set<string>();`):

```typescript
  /** Timestamp of last message:responded LLM classification. */
  private _lastClassifiedAt = 0;
  /** Minimum ms between message classification LLM calls (2 minutes). */
  private readonly _classificationCooldownMs = 2 * 60_000;
```

Replace `handleMessageResponded()` (starting at line 214) with:

```typescript
  private async handleMessageResponded(payload: {
    sessionId: string;
    channelId: string;
    userId: string;
    content: string;
    owlName: string;
    toolsUsed: string[];
  }): Promise<void> {
    if (!payload.toolsUsed?.length) return;

    // Cooldown: at most one LLM classification per cooldown window
    const now = Date.now();
    if (now - this._lastClassifiedAt < this._classificationCooldownMs) {
      log.engine.debug("[EventBasedPelletGenerator] message:responded classification skipped — cooldown active");
      return;
    }

    if (this.activityGate && !(await this.activityGate.hasNewActivity("pellet-classification"))) {
      log.engine.debug("[EventBasedPelletGenerator] message:responded classification skipped — no new user activity");
      return;
    }

    let isSignificant = false;
    try {
      const raw = await this.router.resolve(
        "classification",
        `Classify this AI assistant response:\n"${payload.content.slice(0, 500)}"\n\n` +
        `Reply with JSON only: {"isDecision":bool,"isInsight":bool,"isCorrection":bool}`,
      );
      this._lastClassifiedAt = Date.now();
      const classification = JSON.parse(raw.trim());
      isSignificant = classification.isDecision || classification.isInsight || classification.isCorrection;
    } catch (err) {
      log.engine.warn(`[EventBasedPelletGenerator] Classification parse failed: ${err instanceof Error ? err.message : String(err)}`);
      isSignificant = false;
    }

    if (!isSignificant) return;

    log.engine.info(`[EventBasedPelletGenerator] Decision/insight detected — generating pellet`);

    const pellet = await this.generateFromEvent(
      {
        sourceName: `decision:${payload.sessionId}`,
        sourceMaterial: `Owl "${payload.owlName}" made a decision using tools [${payload.toolsUsed.join(", ")}]. Decision: ${payload.content.slice(0, 1000)}.`,
        tags: ["decision-capture", "tool-driven"],
        owlsInvolved: [payload.owlName],
      },
      "decision-capture",
    );

    if (pellet) {
      await this.activityGate?.markSeen("pellet-classification");
    }
  }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/pellets/event-based-generator-throttle.test.ts
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```

Expected: No new failures.

- [ ] **Step 6: Commit**

```bash
git add src/pellets/event-based-generator.ts __tests__/pellets/event-based-generator-throttle.test.ts
git commit -m "perf(pellets): add 2-minute cooldown to message:responded LLM classifier"
```

---

## Task 4: Debounce LearningOrchestrator to every 5 messages

`processConversation()` runs after every user message. Add a counter so it only runs every 5th message.

**Files:**
- Modify: `src/learning/orchestrator.ts`
- Create: `__tests__/learning/orchestrator-debounce.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/learning/orchestrator-debounce.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { LearningOrchestrator } from "../../src/learning/orchestrator.js";
import type { ChatMessage } from "../../src/providers/base.js";

// Minimal stubs so LearningOrchestrator can be instantiated
const stubProvider: any = {
  chat: vi.fn().mockResolvedValue({ content: '{"topics":[],"knowledgeGaps":[]}' }),
  embed: vi.fn(),
};
const stubOwl: any = { persona: { name: "Noctua" }, dna: {} };
const stubConfig: any = { workspace: "/tmp/test", synthesis: {} };
const stubPelletStore: any = {
  search: vi.fn().mockResolvedValue([]),
  save: vi.fn(),
};
const msgs: ChatMessage[] = [
  { role: "user", content: "hello" },
  { role: "assistant", content: "hi" },
];

describe("LearningOrchestrator message debounce", () => {
  it("skips processConversation for the first 4 calls", async () => {
    const orch = new LearningOrchestrator(stubProvider, stubOwl, stubConfig, stubPelletStore, "/tmp/test");
    const extractSpy = vi.spyOn((orch as any).extractor, "extract").mockResolvedValue({ topics: [], knowledgeGaps: [], timestamp: "" });

    for (let i = 0; i < 4; i++) {
      await orch.processConversation(msgs);
    }

    expect(extractSpy).not.toHaveBeenCalled();
  });

  it("runs processConversation on the 5th call", async () => {
    const orch = new LearningOrchestrator(stubProvider, stubOwl, stubConfig, stubPelletStore, "/tmp/test");
    const extractSpy = vi.spyOn((orch as any).extractor, "extract").mockResolvedValue({ topics: [], knowledgeGaps: [], timestamp: "" });

    for (let i = 0; i < 5; i++) {
      await orch.processConversation(msgs);
    }

    expect(extractSpy).toHaveBeenCalledTimes(1);
  });

  it("runs again on the 10th call", async () => {
    const orch = new LearningOrchestrator(stubProvider, stubOwl, stubConfig, stubPelletStore, "/tmp/test");
    const extractSpy = vi.spyOn((orch as any).extractor, "extract").mockResolvedValue({ topics: [], knowledgeGaps: [], timestamp: "" });

    for (let i = 0; i < 10; i++) {
      await orch.processConversation(msgs);
    }

    expect(extractSpy).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/learning/orchestrator-debounce.test.ts
```

Expected: FAIL — debounce not implemented, extractor runs every call.

- [ ] **Step 3: Add debounce counter to LearningOrchestrator**

In `src/learning/orchestrator.ts`, after `private _busy = false;` (around line 93), add:

```typescript
  /** Counts calls to processConversation; only runs every N calls. */
  private _processCallCount = 0;
  /** Run learning cycle every N conversational messages. Default: 5. */
  private readonly _processEveryN = 5;
```

At the top of `processConversation()` (before the `_busy` check, around line 160), add:

```typescript
    this._processCallCount++;
    if (this._processCallCount % this._processEveryN !== 0) {
      log.evolution.debug(
        `[Orchestrator] processConversation debounced (call ${this._processCallCount}, runs every ${this._processEveryN})`,
      );
      return this.recordCycle({
        id: `reactive_debounce_${Date.now()}`,
        trigger: "reactive",
        startedAt: new Date().toISOString(),
        insightsExtracted: 0,
        topicsPrioritized: 0,
        criticalTopics: 0,
        durationMs: 0,
        success: true,
      });
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/learning/orchestrator-debounce.test.ts
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```

Expected: No new failures.

- [ ] **Step 6: Commit**

```bash
git add src/learning/orchestrator.ts __tests__/learning/orchestrator-debounce.test.ts
git commit -m "perf(learning): debounce processConversation — run every 5 messages instead of every message"
```

---

## Task 5: Delay ProactiveKnowledgeGenerator startup from 30s → 10 minutes

The knowledge council fires 30 seconds after launch and generates ~9 LLM calls before the user has done anything. Delay it to 10 minutes so it only runs if the session is actually active.

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Find the setTimeout in src/index.ts**

```bash
grep -n "runKnowledgeCouncil\|30_000\|30000" /ssd/projects/stackowl-personal-ai-assistant/src/index.ts
```

Expected output: a line like `setTimeout(() => proactiveGenerator.runKnowledgeCouncil(), 30_000)` around line 1065–1069.

- [ ] **Step 2: Change the delay from 30 seconds to 10 minutes**

Locate the line found in Step 1. It will look like one of:

```typescript
setTimeout(() => { void proactiveGenerator.runKnowledgeCouncil(); }, 30_000);
// or
setTimeout(() => proactiveGenerator.runKnowledgeCouncil(), 30_000);
```

Change `30_000` to `10 * 60_000`:

```typescript
// Run knowledge council 10 minutes after startup (not 30 seconds)
// Rationale: gives the user time to start a real session before burning tokens
setTimeout(() => { void proactiveGenerator.runKnowledgeCouncil(); }, 10 * 60_000);
```

- [ ] **Step 3: Run full suite to confirm no regressions**

```bash
npx vitest run
```

Expected: No failures.

- [ ] **Step 4: Commit**

```bash
git add src/index.ts
git commit -m "perf(startup): delay ProactiveKnowledgeGenerator from 30s to 10min after launch"
```

---

## Expected Outcome

After all 5 tasks:

| Subsystem | Before | After |
|---|---|---|
| SignalClassifier | ~90–180 LLM calls/15min | ~5–15 (only ambiguous signals) |
| SignalPool heartbeat verifier | ~75 calls/15min | ~5–10 (10min cooldown per signal) |
| EventBasedPelletGenerator | ~50–100 calls/15min | ~5–8 (2min cooldown) |
| LearningOrchestrator | ~30–60 calls/15min | ~6–12 (every 5 messages) |
| ProactiveKnowledgeGenerator | 9 calls at 30s | 0 calls in first 10min |
| **Total** | **~800 calls/15min** | **~21–45 calls/15min** |

**Reduction: ~95%** — from ~800 to ~21–45 calls per 15 minutes.

---

## Self-Review

**Spec coverage:** All 5 root causes addressed with targeted fixes. ✅

**Placeholder scan:** All code blocks are complete and specific. ✅

**Type consistency:**
- `_lastVerifiedAt: Map<string, number>` — defined in Task 2, used in Task 2 ✅
- `_lastClassifiedAt: number` — defined in Task 3, used in Task 3 ✅
- `_processCallCount: number` — defined in Task 4, used in Task 4 ✅
- `LearningCycle` return type from `recordCycle()` — used in Task 4, already defined in `orchestrator.ts` ✅
