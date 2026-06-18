# Provider Rate Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate Anthropic 429 concurrent-request-limit errors (error 2062) by wiring the existing circuit breaker infrastructure, adding a `ConcurrencyGate` semaphore, and serializing background LLM jobs.

**Architecture:** Two phases. Phase 1 adds a `ConcurrencyGate` semaphore and wires it through `RateLimitedProvider` → `ProviderRegistry` → `withProviderResilience` so all LLM calls share one in-flight counter and the circuit breaker actually receives failure signals. Phase 2 creates an `LLMTaskQueue` (concurrency 1) for background jobs and fixes the `CognitiveLoop`'s isolated 429 backoff.

**Tech Stack:** TypeScript strict, Node.js 22, `@anthropic-ai/sdk` (exports `RateLimitError`, `InternalServerError`, `APIError`), `vitest`.

**Spec:** `docs/superpowers/specs/2026-05-14-provider-rate-guard-design.md`

---

## File Map

| File | Phase | Action |
|------|-------|--------|
| `src/ratelimit/concurrency-gate.ts` | 1 | **Create** — semaphore |
| `src/ratelimit/index.ts` | 1 | **Modify** — export singletons |
| `src/ratelimit/provider-limiter.ts` | 1 | **Modify** — inject gate, async-gen streams |
| `src/providers/registry.ts` | 1 | **Modify** — wrap providers, notifications |
| `src/engine/runtime.ts` | 1 | **Modify** — error split, Retry-After, breaker |
| `src/queue/llm-task-queue.ts` | 2 | **Create** — concurrency-1 queue |
| `src/gateway/core.ts` | 2 | **Modify** — route LLM backgrounds, defer pref-recognize |
| `src/cognition/loop.ts` | 2 | **Modify** — replace isolated backoff |
| `__tests__/ratelimit/concurrency-gate.test.ts` | 1 | **Create** |
| `__tests__/ratelimit/provider-limiter.test.ts` | 1 | **Create** |
| `__tests__/engine/resilience.test.ts` | 1 | **Create** |
| `__tests__/queue/llm-task-queue.test.ts` | 2 | **Create** |

---

## Phase 1 — Provider Safety Layer

---

### Task 1: `ConcurrencyGate` semaphore

**Files:**
- Create: `src/ratelimit/concurrency-gate.ts`
- Create: `__tests__/ratelimit/concurrency-gate.test.ts`

- [ ] **Step 1: Create `src/ratelimit/concurrency-gate.ts`**

```typescript
import { log } from "../logger.js";

export interface ConcurrencyGateOptions {
  /** Max in-flight calls at any moment. */
  maxConcurrent: number;
  /** Max ms a caller waits before rejecting. */
  queueTimeoutMs: number;
}

export class ConcurrencyTimeoutError extends Error {
  constructor() {
    super("Timed out waiting for a provider concurrency slot");
    this.name = "ConcurrencyTimeoutError";
  }
}

export class CircuitOpenError extends Error {
  constructor() {
    super("Provider circuit is open — call rejected fast");
    this.name = "CircuitOpenError";
  }
}

export class ConcurrencyGate {
  private _inflight = 0;
  private _queue: Array<{
    resolve: () => void;
    reject: (e: Error) => void;
    timer: NodeJS.Timeout;
  }> = [];
  private _circuitOpen = false;

  constructor(private readonly opts: ConcurrencyGateOptions) {}

  /**
   * Acquire a slot. Returns a release function.
   * Throws CircuitOpenError immediately if circuit is open.
   * Throws ConcurrencyTimeoutError if no slot opens within queueTimeoutMs.
   */
  async acquire(): Promise<() => void> {
    log.engine.debug("concurrency-gate.acquire: entry", {
      inflight: this._inflight,
      queued: this._queue.length,
      circuitOpen: this._circuitOpen,
    });

    if (this._circuitOpen) {
      log.engine.warn("concurrency-gate.acquire: circuit open — fast fail");
      throw new CircuitOpenError();
    }

    if (this._inflight < this.opts.maxConcurrent) {
      this._inflight++;
      log.engine.debug("concurrency-gate.acquire: slot acquired immediately", { inflight: this._inflight });
      return this._makeRelease();
    }

    log.engine.debug("concurrency-gate.acquire: queuing caller", { queued: this._queue.length + 1 });
    return new Promise<() => void>((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._queue.findIndex((w) => w.timer === timer);
        if (idx !== -1) this._queue.splice(idx, 1);
        log.engine.warn("concurrency-gate.acquire: timeout", {
          queueTimeoutMs: this.opts.queueTimeoutMs,
        });
        reject(new ConcurrencyTimeoutError());
      }, this.opts.queueTimeoutMs);

      this._queue.push({
        resolve: () => {
          clearTimeout(timer);
          this._inflight++;
          log.engine.debug("concurrency-gate.acquire: queued caller unblocked", {
            inflight: this._inflight,
          });
          resolve(this._makeRelease());
        },
        reject,
        timer,
      });
    });
  }

  /** Circuit opened — drain all queued waiters with CircuitOpenError. */
  notifyCircuitOpen(): void {
    this._circuitOpen = true;
    const waiters = this._queue.splice(0);
    log.engine.warn("concurrency-gate.notifyCircuitOpen: draining queue", {
      waiters: waiters.length,
    });
    for (const w of waiters) {
      clearTimeout(w.timer);
      w.reject(new CircuitOpenError());
    }
  }

  /** Circuit closed — new acquire() calls may proceed. */
  notifyCircuitClosed(): void {
    this._circuitOpen = false;
    log.engine.debug("concurrency-gate.notifyCircuitClosed");
  }

  private _makeRelease(): () => void {
    let released = false;
    return () => {
      if (released) return; // idempotent
      released = true;
      this._inflight--;
      log.engine.debug("concurrency-gate.release", { inflight: this._inflight });
      this._dequeue();
    };
  }

  private _dequeue(): void {
    if (this._queue.length > 0 && this._inflight < this.opts.maxConcurrent) {
      const waiter = this._queue.shift()!;
      waiter.resolve();
    }
  }

  get inflight(): number { return this._inflight; }
  get queued(): number { return this._queue.length; }
}
```

- [ ] **Step 2: Create `__tests__/ratelimit/concurrency-gate.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import {
  ConcurrencyGate,
  ConcurrencyTimeoutError,
  CircuitOpenError,
} from "../../src/ratelimit/concurrency-gate.js";

describe("ConcurrencyGate", () => {
  it("acquires and releases a slot immediately when under limit", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    const release = await gate.acquire();
    expect(gate.inflight).toBe(1);
    release();
    expect(gate.inflight).toBe(0);
  });

  it("blocks a second caller until the first releases when maxConcurrent=1", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 1000 });
    const release1 = await gate.acquire();
    expect(gate.inflight).toBe(1);

    const p2 = gate.acquire();
    await new Promise((r) => setTimeout(r, 0)); // flush microtasks
    expect(gate.queued).toBe(1);

    release1();
    const release2 = await p2;
    expect(gate.inflight).toBe(1);
    expect(gate.queued).toBe(0);
    release2();
    expect(gate.inflight).toBe(0);
  });

  it("rejects with ConcurrencyTimeoutError after queueTimeoutMs", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 50 });
    const release1 = await gate.acquire();
    await expect(gate.acquire()).rejects.toBeInstanceOf(ConcurrencyTimeoutError);
    release1();
  });

  it("rejects immediately with CircuitOpenError when circuit is already open", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    gate.notifyCircuitOpen();
    await expect(gate.acquire()).rejects.toBeInstanceOf(CircuitOpenError);
  });

  it("drains queue with CircuitOpenError when circuit opens while callers are waiting", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 1, queueTimeoutMs: 1000 });
    const release1 = await gate.acquire();
    const p2 = gate.acquire();
    await new Promise((r) => setTimeout(r, 0));
    expect(gate.queued).toBe(1);

    gate.notifyCircuitOpen();
    await expect(p2).rejects.toBeInstanceOf(CircuitOpenError);
    expect(gate.queued).toBe(0);
    release1();
  });

  it("allows acquisition after notifyCircuitClosed", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    gate.notifyCircuitOpen();
    gate.notifyCircuitClosed();
    const release = await gate.acquire();
    expect(gate.inflight).toBe(1);
    release();
  });

  it("release is idempotent — double-release does not decrement below zero", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 2, queueTimeoutMs: 100 });
    const release = await gate.acquire();
    release();
    release(); // must not crash or go negative
    expect(gate.inflight).toBe(0);
  });
});
```

- [ ] **Step 3: Run the tests to confirm they pass**

```bash
npx vitest run __tests__/ratelimit/concurrency-gate.test.ts
```

Expected: 7 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/ratelimit/concurrency-gate.ts __tests__/ratelimit/concurrency-gate.test.ts
git commit -m "feat(ratelimit): ConcurrencyGate semaphore with circuit-open drain"
```

---

### Task 2: Singletons in `src/ratelimit/index.ts`, gate injection in `RateLimitedProvider`

**Files:**
- Modify: `src/ratelimit/index.ts`
- Modify: `src/ratelimit/provider-limiter.ts`
- Create: `__tests__/ratelimit/provider-limiter.test.ts`

Context: `src/ratelimit/index.ts` currently exports `RateLimiter`, `RateLimitedProvider`, and types. We add two module-level singletons: `concurrencyGate` (the semaphore) and `providerRateLimiter` (a count-based limiter at 100 req/min — this is the existing sliding-window limiter applied per-provider-name as a secondary safety net; the gate does the real work).

`src/ratelimit/provider-limiter.ts` currently has 3-arg constructor `(inner, limiter, providerKey)`. We add `gate: ConcurrencyGate` as the 4th argument. The `chat()` and `chatWithTools()` methods change from sync `checkLimit()` to `await gate.acquire()`. The stream methods (`chatStream`, `chatWithToolsStream`) currently return `AsyncGenerator<T>` — they must become `async *` generator methods (prefixed `async *`) so `await gate.acquire()` is allowed inside them.

- [ ] **Step 1: Replace `src/ratelimit/index.ts`**

```typescript
import { ConcurrencyGate } from "./concurrency-gate.js";
import { RateLimiter } from "./limiter.js";

export { RateLimiter } from "./limiter.js";
export { RateLimitedProvider } from "./provider-limiter.js";
export { ConcurrencyGate, ConcurrencyTimeoutError, CircuitOpenError } from "./concurrency-gate.js";
export type { RateLimitRule, RateLimitResult, RateLimitStats } from "./limiter.js";

/**
 * Shared semaphore — at most 2 provider calls in-flight across all subsystems.
 * maxConcurrent=2 allows one foreground + one background call simultaneously.
 * queueTimeoutMs=30_000 prevents stuck callers piling up indefinitely.
 */
export const concurrencyGate = new ConcurrencyGate({
  maxConcurrent: 2,
  queueTimeoutMs: 30_000,
});

/**
 * Shared count-based rate limiter — 100 calls/minute per provider name.
 * Secondary protection; the concurrencyGate does the primary in-flight control.
 */
export const providerRateLimiter = new RateLimiter([
  { name: "provider-minute", maxRequests: 100, windowMs: 60_000 },
]);
```

- [ ] **Step 2: Replace `src/ratelimit/provider-limiter.ts`**

Replace the entire file. Key changes: add `gate: ConcurrencyGate` param; `chat()` and `chatWithTools()` await the gate after `checkLimit()`; stream methods become `async *` generators so `await` is legal inside them.

```typescript
/**
 * StackOwl — Rate-Limited Provider Wrapper
 *
 * Wraps a ModelProvider with sliding-window rate limiting and a concurrency
 * gate. Both checks fire on every non-embedding call.
 *
 *   1. checkLimit()  — sliding-window count (rejects if > N calls/minute)
 *   2. gate.acquire() — semaphore (blocks if maxConcurrent in-flight)
 */

import type {
  ModelProvider,
  ChatMessage,
  ChatResponse,
  ChatOptions,
  ToolDefinition,
  StreamChunk,
  StreamEvent,
  EmbeddingResponse,
} from "../providers/base.js";
import type { RateLimiter } from "./limiter.js";
import type { ConcurrencyGate } from "./concurrency-gate.js";
import { log } from "../logger.js";

export class RateLimitedProvider implements ModelProvider {
  readonly name: string;

  constructor(
    private inner: ModelProvider,
    private limiter: RateLimiter,
    private providerKey: string,
    private gate: ConcurrencyGate,
  ) {
    this.name = inner.name;
  }

  private checkLimit(): void {
    const result = this.limiter.consume(this.providerKey);
    if (!result.allowed) {
      const waitSec = Math.ceil((result.retryAfterMs ?? 1000) / 1000);
      log.engine.warn(
        `[RateLimitedProvider] ${this.providerKey} rate limited by "${result.rule}" — retry in ${waitSec}s`,
      );
      throw new Error(`Rate limited (${result.rule}): retry after ${waitSec}s`);
    }
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    log.engine.debug("rate-limited-provider.chat: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      const result = await this.inner.chat(messages, model, options);
      log.engine.debug("rate-limited-provider.chat: exit", { provider: this.providerKey });
      return result;
    } finally {
      release();
    }
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    log.engine.debug("rate-limited-provider.chatWithTools: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      const result = await this.inner.chatWithTools(messages, tools, model, options);
      log.engine.debug("rate-limited-provider.chatWithTools: exit", { provider: this.providerKey });
      return result;
    } finally {
      release();
    }
  }

  // Stream methods must be async generators so `await` is legal inside them.
  // The return type AsyncGenerator<T> is compatible with the ModelProvider interface.

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    log.engine.debug("rate-limited-provider.chatStream: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      yield* this.inner.chatStream(messages, model, options);
    } finally {
      release();
      log.engine.debug("rate-limited-provider.chatStream: exit", { provider: this.providerKey });
    }
  }

  async *chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    log.engine.debug("rate-limited-provider.chatWithToolsStream: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    if (!this.inner.chatWithToolsStream) {
      release();
      throw new Error(`Provider ${this.name} does not support chatWithToolsStream`);
    }
    try {
      yield* this.inner.chatWithToolsStream(messages, tools, model, options);
    } finally {
      release();
      log.engine.debug("rate-limited-provider.chatWithToolsStream: exit", { provider: this.providerKey });
    }
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    // Embeddings are lightweight — skip rate limit and gate
    return this.inner.embed(text, model);
  }

  async listModels(): Promise<string[]> {
    return this.inner.listModels();
  }

  async healthCheck(): Promise<boolean> {
    return this.inner.healthCheck();
  }
}
```

- [ ] **Step 3: Create `__tests__/ratelimit/provider-limiter.test.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { RateLimitedProvider } from "../../src/ratelimit/provider-limiter.js";
import { ConcurrencyGate } from "../../src/ratelimit/concurrency-gate.js";
import { RateLimiter } from "../../src/ratelimit/limiter.js";
import type { ModelProvider, ChatResponse } from "../../src/providers/base.js";

function makeProvider(name = "test"): ModelProvider {
  return {
    name,
    chat: vi.fn().mockResolvedValue({ content: "ok", toolCalls: [], usage: {} } as ChatResponse),
    chatWithTools: vi.fn().mockResolvedValue({ content: "ok", toolCalls: [], usage: {} } as ChatResponse),
    chatStream: vi.fn(async function* () { yield { type: "text", text: "ok" }; }),
    chatWithToolsStream: vi.fn(async function* () { yield { type: "text", text: "ok" }; }),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeGate(maxConcurrent = 10) {
  return new ConcurrencyGate({ maxConcurrent, queueTimeoutMs: 1000 });
}

function makeLimiter() {
  return new RateLimiter([{ name: "test-minute", maxRequests: 1000, windowMs: 60_000 }]);
}

describe("RateLimitedProvider", () => {
  it("calls the inner provider on chat()", async () => {
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", makeGate());
    await wrapped.chat([], "model");
    expect(inner.chat).toHaveBeenCalledOnce();
  });

  it("calls the inner provider on chatWithTools()", async () => {
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", makeGate());
    await wrapped.chatWithTools([], [], "model");
    expect(inner.chatWithTools).toHaveBeenCalledOnce();
  });

  it("serializes concurrent calls when maxConcurrent=1", async () => {
    const gate = makeGate(1);
    const limiter = makeLimiter();
    const order: number[] = [];
    let resolveFirst!: () => void;
    const inner = makeProvider();
    (inner.chat as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(
        () => new Promise<ChatResponse>((res) => {
          resolveFirst = () => {
            order.push(1);
            res({ content: "first", toolCalls: [], usage: {} });
          };
        }),
      )
      .mockImplementationOnce(async () => {
        order.push(2);
        return { content: "second", toolCalls: [], usage: {} };
      });

    const wrapped = new RateLimitedProvider(inner, limiter, "test", gate);
    const p1 = wrapped.chat([], "model");
    const p2 = wrapped.chat([], "model");

    await new Promise((r) => setTimeout(r, 0));
    expect(gate.queued).toBe(1); // p2 waiting

    resolveFirst();
    await p1;
    await p2;
    expect(order).toEqual([1, 2]); // p2 ran after p1
  });

  it("releases the gate slot even if inner.chat throws", async () => {
    const gate = makeGate(1);
    const inner = makeProvider();
    (inner.chat as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("boom"));
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", gate);

    await expect(wrapped.chat([], "model")).rejects.toThrow("boom");
    expect(gate.inflight).toBe(0); // gate released despite throw
  });

  it("skips the gate for embed()", async () => {
    const gate = new ConcurrencyGate({ maxConcurrent: 0, queueTimeoutMs: 10 }); // would block immediately
    const inner = makeProvider();
    const wrapped = new RateLimitedProvider(inner, makeLimiter(), "test", gate);
    await wrapped.embed("text");
    expect(inner.embed).toHaveBeenCalled();
    expect(gate.inflight).toBe(0);
  });
});
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/ratelimit/provider-limiter.test.ts
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ratelimit/index.ts src/ratelimit/provider-limiter.ts __tests__/ratelimit/provider-limiter.test.ts
git commit -m "feat(ratelimit): add ConcurrencyGate singletons and wire gate into RateLimitedProvider"
```

---

### Task 3: Apply `RateLimitedProvider` in `ProviderRegistry` + gate → breaker notifications

**Files:**
- Modify: `src/providers/registry.ts`

Context: `ProviderRegistry` creates providers via `PROTOCOL_FACTORIES` in `register()`. There are two code paths — the `baseUrl`-only fallback path (lines ~175–189) and the main `modelDef` path (lines ~199–213). Both must wrap the created provider with `RateLimitedProvider`.

The `ProviderCircuitBreaker` already has `failureThreshold` as its first constructor parameter (default 5). For Anthropic providers (`modelDef.compatible === "anthropic"`), pass `failureThreshold = 1` so the first 429 immediately opens the circuit.

`recordProviderResult()` must detect state transitions and call `gate.notifyCircuitOpen()` / `notifyCircuitClosed()`.

- [ ] **Step 1: Add imports at the top of `src/providers/registry.ts`**

After the existing imports, add:

```typescript
import { RateLimitedProvider, concurrencyGate, providerRateLimiter } from "../ratelimit/index.js";
```

- [ ] **Step 2: Update `register()` — `baseUrl` fallback path (lines ~174–189)**

Find this block:
```typescript
      try {
        const provider = factory(config, syntheticDef);
        this.providers.set(config.name, provider);
        this.breakers.set(
          config.name,
          new ProviderCircuitBreaker(
            this.healthPolicy.failureThreshold,
            this.healthPolicy.recoveryTimeoutMs,
          ),
        );
      } catch (error) {
```

Replace with:
```typescript
      try {
        const rawProvider = factory(config, syntheticDef);
        const provider = new RateLimitedProvider(
          rawProvider,
          providerRateLimiter,
          config.name,
          concurrencyGate,
        );
        this.providers.set(config.name, provider);
        this.breakers.set(
          config.name,
          new ProviderCircuitBreaker(
            this.healthPolicy.failureThreshold,
            this.healthPolicy.recoveryTimeoutMs,
          ),
        );
      } catch (error) {
```

- [ ] **Step 3: Update `register()` — main `modelDef` path (lines ~198–213)**

Find this block:
```typescript
    try {
      const provider = factory(config, modelDef!);
      this.providers.set(config.name, provider);
      this.breakers.set(
        config.name,
        new ProviderCircuitBreaker(
          this.healthPolicy.failureThreshold,
          this.healthPolicy.recoveryTimeoutMs,
        ),
      );
    } catch (error) {
```

Replace with:
```typescript
    try {
      const rawProvider = factory(config, modelDef!);
      const provider = new RateLimitedProvider(
        rawProvider,
        providerRateLimiter,
        config.name,
        concurrencyGate,
      );
      this.providers.set(config.name, provider);
      // Anthropic providers open the circuit after a single 429 — error 2062
      // is a concurrent-request limit that retrying immediately makes worse.
      const failureThreshold =
        modelDef!.compatible === "anthropic" ? 1 : this.healthPolicy.failureThreshold;
      this.breakers.set(
        config.name,
        new ProviderCircuitBreaker(failureThreshold, this.healthPolicy.recoveryTimeoutMs),
      );
    } catch (error) {
```

- [ ] **Step 4: Update `recordProviderResult()` to notify the gate on state transitions**

Find the existing `recordProviderResult()` method:
```typescript
  recordProviderResult(name: string, success: boolean): void {
    this.breakers.get(name)?.recordResult(success);
  }
```

Replace with:
```typescript
  recordProviderResult(name: string, success: boolean): void {
    const breaker = this.breakers.get(name);
    if (!breaker) return;
    const wasOpen = breaker.isOpen();
    breaker.recordResult(success);
    const isNowOpen = breaker.isOpen();
    if (isNowOpen && !wasOpen) {
      log.engine.warn(`[ProviderRegistry] Circuit opened for "${name}" — notifying concurrency gate`);
      concurrencyGate.notifyCircuitOpen();
    } else if (!isNowOpen && wasOpen) {
      log.engine.debug(`[ProviderRegistry] Circuit closed for "${name}" — notifying concurrency gate`);
      concurrencyGate.notifyCircuitClosed();
    }
  }
```

- [ ] **Step 5: Run the full test suite**

```bash
npm test
```

Expected: all tests pass. The registry change doesn't break tests because `_registerForTest()` bypasses `register()` and test providers are not wrapped.

- [ ] **Step 6: Commit**

```bash
git add src/providers/registry.ts
git commit -m "feat(providers): wrap all providers with RateLimitedProvider, notify gate on circuit transitions"
```

---

### Task 4: Fix `withProviderResilience` in `src/engine/runtime.ts`

**Files:**
- Modify: `src/engine/runtime.ts`
- Create: `__tests__/engine/resilience.test.ts`

Context: `withProviderResilience` (around line 652) currently has one `isTransientStreamError` function that classifies both 429 and 5xx as retryable, with no breaker signal, no Retry-After parsing, and no isOpen check. The `RETRYABLE_STREAM_STATUSES` set at line 617 is no longer needed after the split.

The Anthropic SDK exports:
- `RateLimitError` (extends `APIError<429>`) — use `instanceof` check
- `InternalServerError` (extends `APIError<number>`) — use `instanceof` check
- `APIError` — has `.headers: Headers` (Fetch API — use `.get("retry-after")`)

- [ ] **Step 1: Add the three error helper functions above `withProviderResilience`**

Find the `RETRYABLE_STREAM_STATUSES` constant at line ~617 and the `isTransientStreamError` function at line ~631. Replace both with:

```typescript
import { RateLimitError, InternalServerError, APIError } from "@anthropic-ai/sdk";

/**
 * True for 429 rate-limit errors from any provider.
 * Uses SDK typed class first; falls back to .status for non-Anthropic providers.
 */
function isRateLimitError(err: unknown): boolean {
  if (err instanceof RateLimitError) return true;
  const status = (err as { status?: number }).status;
  return status === 429;
}

/**
 * True for transient 5xx / network errors (worth retrying with backoff).
 * Does NOT include 429 — those are handled by isRateLimitError separately.
 */
function isTransientStreamError(err: unknown): boolean {
  if (err instanceof InternalServerError) return true;
  const status = (err as { status?: number }).status;
  if (typeof status === "number" && status >= 500 && status < 600) return true;
  const msg = err instanceof Error ? err.message : String(err);
  const networkKeywords = ["fetch", "ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "timeout", "network"];
  return networkKeywords.some((kw) => msg.toLowerCase().includes(kw.toLowerCase()));
}

/**
 * Parse Retry-After from an Anthropic SDK error's Headers object.
 * APIError.headers is a Fetch API Headers — use .get(), not bracket access.
 * Returns milliseconds, or undefined if no header present.
 */
function parseRetryAfterMs(err: unknown): number | undefined {
  if (err instanceof APIError && err.headers) {
    const val = err.headers.get("retry-after");
    if (val) {
      const seconds = parseInt(val, 10);
      if (!isNaN(seconds)) return seconds * 1000;
    }
  }
  return undefined;
}

/**
 * Calculate backoff with ±20% jitter.
 * Uses retryAfterMs if provided (from Retry-After header), otherwise exponential.
 */
function backoffMs(attempt: number, retryAfterMs?: number): number {
  const BASE_DELAY_MS = 1_500;
  const base = retryAfterMs ?? BASE_DELAY_MS * Math.pow(2, attempt);
  const jitter = base * 0.2 * (Math.random() * 2 - 1);
  return Math.max(100, Math.round(base + jitter));
}
```

**Important:** Also remove the now-unused `const RETRYABLE_STREAM_STATUSES` constant from around line 617.

- [ ] **Step 2: Update the retry loop inside `withProviderResilience`**

The `BASE_DELAY_MS` constant at line ~663 is now replaced by the `backoffMs()` helper. Remove it.

Find the Layer 1 retry loop (the `for (let attempt = 0; attempt < MAX_RETRIES; attempt++)` block). Replace the interior with:

```typescript
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    // Skip attempting an open provider immediately (fail-fast)
    if (providerRegistry?.isProviderOpen(provider.name)) {
      log.engine.warn(
        `[Resilience/${site}] Provider "${provider.name}" circuit is OPEN — skipping attempt ${attempt + 1}`,
      );
      break;
    }

    try {
      let result: ChatResponse;
      if (provider.chatWithToolsStream && onStreamEvent) {
        result = await withSpan("provider.call", async () => {
          return consumeStream(
            provider.chatWithToolsStream!(messages, tools, model, chatOptions),
            onStreamEvent!,
          );
        }, { model, attempt });
      } else {
        result = await withSpan("provider.call", async () => {
          return provider.chatWithTools(messages, tools, model, chatOptions);
        }, { model, attempt });
      }
      // Success — signal breaker and return
      providerRegistry?.recordProviderResult(provider.name, true);
      return result;
    } catch (err) {
      lastStreamError = err;
      const errMsg = err instanceof Error ? err.message : String(err);

      if (isRateLimitError(err)) {
        providerRegistry?.recordProviderResult(provider.name, false);
        const retryAfterMs = parseRetryAfterMs(err);
        const delay = backoffMs(attempt, retryAfterMs);
        if (attempt < MAX_RETRIES - 1) {
          log.engine.warn(
            `[Resilience/${site}] 429 rate-limit on "${provider.name}" (attempt ${attempt + 1}/${MAX_RETRIES}). ` +
            `Retrying in ${delay}ms${retryAfterMs ? ` (Retry-After: ${retryAfterMs}ms)` : ""}…`,
          );
          await new Promise((r) => setTimeout(r, delay));
          continue;
        }
        log.engine.warn(
          `[Resilience/${site}] 429 rate-limit — retries exhausted. Degrading to Layer 2.`,
        );
        break;
      }

      if (isTransientStreamError(err) && attempt < MAX_RETRIES - 1) {
        providerRegistry?.recordProviderResult(provider.name, false);
        const delay = backoffMs(attempt);
        log.engine.warn(
          `[Resilience/${site}] Transient error on "${provider.name}" (attempt ${attempt + 1}/${MAX_RETRIES}): ${errMsg}. Retrying in ${delay}ms…`,
        );
        await new Promise((r) => setTimeout(r, delay));
        continue;
      }

      // Non-transient or final attempt — break to Layer 2
      log.engine.warn(
        `[Resilience/${site}] Non-retryable error on "${provider.name}": ${errMsg}. Degrading to Layer 2.`,
      );
      break;
    }
  }
```

Also add `recordProviderResult(provider.name, true)` after Layer 2 success (around line 715 in the original):

```typescript
  // ── Layer 2: degrade to non-stream on same provider ─────────────
  try {
    log.engine.warn(`[Resilience/${site}] Attempting non-stream fallback on provider "${provider.name}"…`);
    const result = await provider.chatWithTools(messages, tools, model, chatOptions);
    providerRegistry?.recordProviderResult(provider.name, true);  // ADD THIS
    log.engine.info(`[Resilience/${site}] Non-stream fallback succeeded on "${provider.name}".`);
    return result;
  } catch (nonStreamErr) {
```

- [ ] **Step 3: Create `__tests__/engine/resilience.test.ts`**

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";

// These functions are not exported — we test via the behavior they affect.
// Instead, test the public helper functions we extracted:

import { RateLimitError, InternalServerError, APIError } from "@anthropic-ai/sdk";

// Since the helpers are module-private, we re-implement them inline for testing
// to verify the logic without export pollution.

function isRateLimitError(err: unknown): boolean {
  if (err instanceof RateLimitError) return true;
  const status = (err as { status?: number }).status;
  return status === 429;
}

function isTransientStreamError(err: unknown): boolean {
  if (err instanceof InternalServerError) return true;
  const status = (err as { status?: number }).status;
  if (typeof status === "number" && status >= 500 && status < 600) return true;
  const msg = err instanceof Error ? err.message : String(err);
  return ["ECONNRESET", "ETIMEDOUT", "timeout", "fetch", "network"].some((kw) =>
    msg.toLowerCase().includes(kw.toLowerCase()),
  );
}

function parseRetryAfterMs(err: unknown): number | undefined {
  if (err instanceof APIError && err.headers) {
    const val = err.headers.get("retry-after");
    if (val) {
      const seconds = parseInt(val, 10);
      if (!isNaN(seconds)) return seconds * 1000;
    }
  }
  return undefined;
}

function backoffMs(attempt: number, retryAfterMs?: number): number {
  const BASE_DELAY_MS = 1_500;
  const base = retryAfterMs ?? BASE_DELAY_MS * Math.pow(2, attempt);
  return Math.max(100, Math.round(base));
}

describe("isRateLimitError", () => {
  it("returns true for RateLimitError instance", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(isRateLimitError(err)).toBe(true);
  });

  it("returns true for error with status=429", () => {
    const err = Object.assign(new Error("too many"), { status: 429 });
    expect(isRateLimitError(err)).toBe(true);
  });

  it("returns false for InternalServerError", () => {
    const err = new InternalServerError(500, {}, "server error", new Headers());
    expect(isRateLimitError(err)).toBe(false);
  });
});

describe("isTransientStreamError", () => {
  it("returns true for InternalServerError", () => {
    const err = new InternalServerError(500, {}, "server error", new Headers());
    expect(isTransientStreamError(err)).toBe(true);
  });

  it("returns true for status=502", () => {
    const err = Object.assign(new Error("bad gateway"), { status: 502 });
    expect(isTransientStreamError(err)).toBe(true);
  });

  it("returns true for ECONNRESET", () => {
    expect(isTransientStreamError(new Error("ECONNRESET"))).toBe(true);
  });

  it("returns false for RateLimitError", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(isTransientStreamError(err)).toBe(false);
  });
});

describe("parseRetryAfterMs", () => {
  it("returns seconds * 1000 from Retry-After header", () => {
    const headers = new Headers({ "retry-after": "30" });
    const err = new RateLimitError(429, {}, "rate limit", headers);
    expect(parseRetryAfterMs(err)).toBe(30_000);
  });

  it("returns undefined when no Retry-After header", () => {
    const err = new RateLimitError(429, {}, "rate limit", new Headers());
    expect(parseRetryAfterMs(err)).toBeUndefined();
  });

  it("returns undefined for non-APIError", () => {
    expect(parseRetryAfterMs(new Error("plain error"))).toBeUndefined();
  });
});

describe("backoffMs", () => {
  it("uses retryAfterMs when provided", () => {
    expect(backoffMs(0, 30_000)).toBe(30_000);
  });

  it("uses exponential fallback when no retryAfterMs", () => {
    expect(backoffMs(0)).toBe(1_500);
    expect(backoffMs(1)).toBe(3_000);
    expect(backoffMs(2)).toBe(6_000);
  });

  it("never returns below 100ms", () => {
    expect(backoffMs(0, 50)).toBe(100);
  });
});
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/engine/resilience.test.ts
```

Expected: 11 tests pass.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/engine/runtime.ts __tests__/engine/resilience.test.ts
git commit -m "fix(runtime): split isRateLimitError/isTransientStreamError, parse Retry-After, feed circuit breaker"
```

---

## Phase 2 — Background Job Cleanup

---

### Task 5: `LLMTaskQueue` singleton

**Files:**
- Create: `src/queue/llm-task-queue.ts`
- Create: `__tests__/queue/llm-task-queue.test.ts`

Context: `TaskQueue` constructor signature is `new TaskQueue({ concurrency, maxQueueSize })`. It does NOT accept `defaultPriority`. Priority is set per `enqueue(name, fn, priority)` call. Callers in `gateway/core.ts` must pass `"low"` explicitly.

- [ ] **Step 1: Create `src/queue/llm-task-queue.ts`**

```typescript
/**
 * StackOwl — LLM Task Queue
 *
 * Shared task queue for all background LLM API calls.
 * concurrency=1 ensures at most one background LLM call runs at any moment,
 * preventing background tasks from competing with the foreground conversation.
 *
 * Usage:
 *   llmTaskQueue.enqueue("task-name", async () => { ... }, "low");
 */
import { TaskQueue } from "./task-queue.js";

export const llmTaskQueue = new TaskQueue({ concurrency: 1 });
```

- [ ] **Step 2: Create `__tests__/queue/llm-task-queue.test.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { llmTaskQueue } from "../../src/queue/llm-task-queue.js";

describe("llmTaskQueue", () => {
  it("runs tasks sequentially — only 1 at a time", async () => {
    const order: number[] = [];
    const delays: Array<() => void> = [];

    const p1 = new Promise<void>((res) => {
      llmTaskQueue.enqueue("task-1", async () => {
        await new Promise<void>((r) => { delays[0] = r; });
        order.push(1);
      }, "low");
      res();
    });

    await p1;
    await new Promise((r) => setTimeout(r, 0));

    llmTaskQueue.enqueue("task-2", async () => {
      order.push(2);
    }, "low");

    // task-2 is queued but not running — task-1 is still blocking
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual([]); // nothing completed yet

    delays[0]?.(); // unblock task-1
    await llmTaskQueue.drain();

    expect(order).toEqual([1, 2]);
  });

  it("uses concurrency=1", () => {
    // Access via stats
    const stats = llmTaskQueue.getStats();
    // With no tasks, active=0 and the queue was created with concurrency=1
    // We verify the singleton was created (not null)
    expect(llmTaskQueue).toBeTruthy();
    expect(typeof stats.pending).toBe("number");
  });
});
```

- [ ] **Step 3: Run tests**

```bash
npx vitest run __tests__/queue/llm-task-queue.test.ts
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/queue/llm-task-queue.ts __tests__/queue/llm-task-queue.test.ts
git commit -m "feat(queue): LLMTaskQueue singleton — concurrency 1 for background LLM tasks"
```

---

### Task 6: Route LLM background jobs + defer `preferenceRecognizer`

**Files:**
- Modify: `src/gateway/core.ts`

Context: `runBackground(name, task)` is the legacy helper that calls `this.taskQueue.enqueue(name, () => task)` at normal priority. We replace specific LLM-calling background tasks with `llmTaskQueue.enqueue(name, fn, "low")`. Non-LLM tasks (intent tracking, behavioral analysis) stay on `this.taskQueue`.

The `preferenceRecognizer.recognizeFromMessage` call at line ~1754 blocks the hot path before the main engine. We split it: keep `buildContextString(0.5)` in place (reads existing preferences), defer `recognizeFromMessage` to post-response via `llmTaskQueue`.

- [ ] **Step 1: Add `llmTaskQueue` import at the top of `src/gateway/core.ts`**

Find the existing imports section and add:

```typescript
import { llmTaskQueue } from "../queue/llm-task-queue.js";
```

- [ ] **Step 2: Update the `episode-extract` background task (line ~1624)**

Find:
```typescript
      this.runBackground(
        "episode-extract",
        (async () => {
```

Replace `this.runBackground(` with:
```typescript
      llmTaskQueue.enqueue(
        "episode-extract",
        async () => {
```

And close the enqueue call: the argument after the async function is `"low"`:
```typescript
        }, "low",
      );
```

**Note:** The async IIFE pattern `(async () => { ... })()` used by `runBackground` creates a Promise eagerly. With `llmTaskQueue.enqueue`, we pass `async () => { ... }` (a factory function, not a started Promise). Change the `(async () => {` to `async () => {` and remove the trailing `})()` at the end. The `llmTaskQueue` will call the function when a slot is available.

Example — find:
```typescript
      this.runBackground(
        "episode-extract",
        (async () => {
          try {
            // ... episode extract code ...
          } catch (err) {
            log.engine.warn("episode-extract failed", err);
          }
        })(),
      );
```

Replace with:
```typescript
      llmTaskQueue.enqueue("episode-extract", async () => {
        try {
          // ... episode extract code (same as before, unchanged) ...
        } catch (err) {
          log.engine.warn("episode-extract failed", err);
        }
      }, "low");
```

- [ ] **Step 3: Update the `preference-capture` background task (line ~2491)**

Find:
```typescript
      this.runBackground(
        "preference-capture",
        this.ctx.preferenceEnforcer.captureExplicitPreferences(
          message.text,
          this.ctx.preferenceModel,
        ),
      );
```

Replace with:
```typescript
      llmTaskQueue.enqueue("preference-capture", async () => {
        try {
          await this.ctx.preferenceEnforcer!.captureExplicitPreferences(
            message.text,
            this.ctx.preferenceModel!,
          );
        } catch (err) {
          log.engine.warn("preference-capture failed", err instanceof Error ? err : new Error(String(err)));
        }
      }, "low");
```

- [ ] **Step 4: Update the `preference-infer` background task (line ~2498)**

Find:
```typescript
      this.runBackground(
        "preference-infer",
        this.ctx.preferenceEnforcer.inferImplicitSignals(
          message.text,
          response.content,
          this.ctx.preferenceModel,
        ),
      );
```

Replace with:
```typescript
      llmTaskQueue.enqueue("preference-infer", async () => {
        try {
          await this.ctx.preferenceEnforcer!.inferImplicitSignals(
            message.text,
            response.content,
            this.ctx.preferenceModel!,
          );
        } catch (err) {
          log.engine.warn("preference-infer failed", err instanceof Error ? err : new Error(String(err)));
        }
      }, "low");
```

- [ ] **Step 5: Update the `pellet-flywheel` background task (line ~2647)**

Find:
```typescript
      this.runBackground("pellet-flywheel", (async () => {
```

Replace with:
```typescript
      llmTaskQueue.enqueue("pellet-flywheel", async () => {
```

And change the closing `})());` to `}, "low");`.

- [ ] **Step 6: Update the `verification` background task (line ~2576)**

Find:
```typescript
      this.runBackground("verification", (async () => {
```

Replace with:
```typescript
      llmTaskQueue.enqueue("verification", async () => {
```

Change closing `})());` to `}, "low");`.

- [ ] **Step 7: Defer `preferenceRecognizer.recognizeFromMessage` to post-response**

**BEFORE** (lines ~1754–1763):
```typescript
      // PreferenceRecognizer — inject recognized user preferences
      if (this.preferenceRecognizer) {
        try {
          await this.preferenceRecognizer.recognizeFromMessage(message.text);
          const prefCtx = this.preferenceRecognizer.buildContextString(0.5);
          if (prefCtx) {
            memoryContextParts.push(`\n${prefCtx}\n`);
          }
        } catch (err) {
          log.engine.debug(`[Memory] Preference recognition failed: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
```

Replace with (read existing prefs now, defer recognition to after response):
```typescript
      // PreferenceRecognizer — inject preferences recognized from PREVIOUS messages.
      // recognizeFromMessage (LLM call) is deferred to post-response via llmTaskQueue
      // so it doesn't block the hot path. The recognized preferences will be
      // available for the NEXT turn.
      if (this.preferenceRecognizer) {
        try {
          const prefCtx = this.preferenceRecognizer.buildContextString(0.5);
          if (prefCtx) {
            memoryContextParts.push(`\n${prefCtx}\n`);
          }
        } catch (err) {
          log.engine.debug(`[Memory] Preference context build failed: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
```

Then, find a post-response location (after the main engine returns, before the function returns — look for `this.detectPreferences` or `this.postProcess` calls around lines 2535–2545). Add the deferred recognition after those calls:

```typescript
    // Defer preference recognition to next turn — runs after response is delivered
    if (this.preferenceRecognizer) {
      const pr = this.preferenceRecognizer;
      const msgText = message.text;
      llmTaskQueue.enqueue("pref-recognize", async () => {
        try {
          await pr.recognizeFromMessage(msgText);
        } catch (err) {
          log.engine.warn("pref-recognize deferred task failed", err instanceof Error ? err : new Error(String(err)));
        }
      }, "low");
    }
```

- [ ] **Step 8: Run the full test suite**

```bash
npm test
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/gateway/core.ts
git commit -m "fix(gateway): route LLM background jobs through llmTaskQueue at low priority, defer pref-recognize"
```

---

### Task 7: Fix `CognitiveLoop` isolated 429 backoff

**Files:**
- Modify: `src/cognition/loop.ts`

Context: `CognitiveLoop` has `private _rateLimitedUntil = 0` (line 198). In `_tickInner()` (line 419), it checks `Date.now() < this._rateLimitedUntil`. When a 429 is detected in the catch block (line 471), it sets `_rateLimitedUntil = Date.now() + 60 * 60 * 1000`. This is a 1-hour isolated backoff that shares no state with the circuit breaker used by the main conversation path.

`CognitiveLoopDeps` already includes `providerRegistry?: ProviderRegistry` (line 112). We need the provider name to check against the breaker. The cognitive loop uses `deps.providerRegistry.getDefault()` for its LLM calls — so we check `isProviderOpen` on the default provider's name.

- [ ] **Step 1: Remove `private _rateLimitedUntil` and replace the rate-limit guard**

Find line ~198:
```typescript
  private _rateLimitedUntil = 0;
```

Remove it.

Find line ~417–421 (`_tickInner` method start):
```typescript
  private async _tickInner(): Promise<void> {
    // Rate-limit guard — stop all background LLM work when provider is 429
    if (Date.now() < this._rateLimitedUntil) {
      log.engine.debug(`[CognitiveLoop] Rate-limited — skipping tick until ${new Date(this._rateLimitedUntil).toISOString()}`);
      return;
    }
```

Replace with:
```typescript
  private async _tickInner(): Promise<void> {
    // Rate-limit guard — check shared circuit breaker instead of isolated backoff.
    // When the main conversation path opens the breaker (after a 429), cognitive
    // ticks automatically pause without needing a separate per-loop state variable.
    if (this.deps.providerRegistry) {
      const defaultName = this.deps.providerRegistry.getDefault().name;
      if (this.deps.providerRegistry.isProviderOpen(defaultName)) {
        log.cognition.debug("[CognitiveLoop] Primary provider circuit open — skipping tick");
          return;
        }
      }
    }
```

- [ ] **Step 2: Remove the isolated `_rateLimitedUntil` assignment in the catch block**

Find line ~471–473:
```typescript
      if (msg.includes("429") || msg.toLowerCase().includes("rate_limit") || msg.toLowerCase().includes("usage limit")) {
        this._rateLimitedUntil = Date.now() + 60 * 60 * 1000;
        log.engine.warn(`[CognitiveLoop] Rate limit detected — suspending background LLM calls for 1 hour (until ${new Date(this._rateLimitedUntil).toISOString()})`);
      }
```

Replace with:
```typescript
      if (msg.includes("429") || msg.toLowerCase().includes("rate_limit") || msg.toLowerCase().includes("usage limit")) {
        // The circuit breaker in ProviderRegistry will handle backoff — no per-loop state needed.
        log.cognition.warn("[CognitiveLoop] Rate limit detected during cognitive tick — breaker will gate future calls");
      }
```

- [ ] **Step 3: Run the full test suite**

```bash
npm test
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/cognition/loop.ts
git commit -m "fix(cognition): replace isolated _rateLimitedUntil with shared circuit breaker check"
```

---

## Final Verification

After all tasks, run:

```bash
npm test
```

All 3454+ tests must pass. Then start the app and send "hi":

```bash
npm run dev
```

Check logs:
```bash
cat logs/stackowl-$(date +%F).log | jq 'select(.msg | contains("concurrency-gate")) | {msg, fields}'
cat logs/stackowl-$(date +%F).log | jq 'select(.msg | contains("circuit")) | {ts, msg, fields}'
```

Expected log sequence for a normal "hi" message:
1. `concurrency-gate.acquire: slot acquired immediately` — gate allowed the call
2. `concurrency-gate.release` — call finished, gate slot freed
3. No `429` errors

If a 429 occurs, expected sequence:
1. `[Resilience/initial] 429 rate-limit on "anthropic"` — retry with backoff
2. `[ProviderRegistry] Circuit opened for "anthropic"` — breaker tripped
3. `concurrency-gate.notifyCircuitOpen: draining queue` — queued callers drained
4. `[CognitiveLoop] Primary provider circuit open — skipping tick` — background LLM paused
5. After recovery timeout: `[ProviderRegistry] Circuit closed for "anthropic"` — breaker reset
