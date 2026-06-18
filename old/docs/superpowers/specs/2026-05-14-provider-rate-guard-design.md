# Provider Rate Guard — Design Spec

**Goal:** Eliminate 429 concurrent request limit errors (error 2062) by wiring the existing circuit breaker and rate limiter infrastructure, adding a missing concurrency gate semaphore, and cleaning up background LLM task scheduling.

**Architecture:** Two phases. Phase 1 fixes the provider layer (wiring + one new file). Phase 2 fixes background job scheduling and cross-system 429 state sharing. Phase 3 (trivial message fast-path + parliament concurrency) is a separate spec — it is a performance optimization, not a bug fix.

**Tech Stack:** TypeScript strict, Node.js 22, Anthropic SDK, existing `ProviderCircuitBreaker` (`src/providers/circuit-breaker.ts`), existing `RateLimitedProvider` (`src/ratelimit/provider-limiter.ts`), existing `TaskQueue` (`src/queue/task-queue.ts`).

---

## Background: What Is Broken

### Confirmed root causes (from log forensics + code audit)

A single user message triggers 8–14 LLM API calls in `gateway/core.ts::handleCore()`. These run without any shared concurrency control. When any call returns 429, each subsystem's independent retry loop fires separately, creating an exponential cascade of concurrent requests that trips Anthropic's concurrent request limit (error 2062).

**The infrastructure to prevent this already exists but is not wired:**

| Component | Location | Status |
|-----------|----------|--------|
| `ProviderCircuitBreaker` | `src/providers/circuit-breaker.ts` | Exists, never receives 429 signals |
| `RateLimitedProvider` | `src/ratelimit/provider-limiter.ts` | Exists, never applied to registered providers |
| `TaskQueue` | `src/queue/task-queue.ts` | Exists, concurrency=3 (too high for LLM tasks) |
| Concurrency semaphore | — | **Missing** — only new file needed |

**Specific wiring failures:**
1. `withProviderResilience` in `runtime.ts` retries 429s up to 3×, but never calls `recordProviderResult(name, false)` — the breaker has no input signal and never opens
2. The primary provider at line 1206 bypasses `isOpen()` entirely — only fallback providers are checked against the breaker
3. `RateLimitedProvider` uses a sliding-window count limiter (counts *initiated* calls per minute) — it does not limit *in-flight* concurrent calls
4. Sub-systems (IntentClarifier, EpisodicMemory, PreferenceRecognizer, ParliamentAutoTrigger, etc.) call `provider.chat()` raw — no resilience wrapper, no breaker
5. `CognitiveLoop` has an isolated 1-hour 429 backoff that shares no state with the circuit breaker — two disconnected rate-limit systems
6. `isTransientStreamError` classifies 429 and 5xx identically — 429 should trip the breaker immediately; 5xx should increment failure count
7. `Retry-After` header from Anthropic 429 responses is ignored — the fixed backoff (1.5s→3s→6s) is shorter than the actual rate-limit window

---

## Phase 1 — Provider Safety Layer

**Files:**
- Create: `src/ratelimit/concurrency-gate.ts`
- Modify: `src/ratelimit/index.ts` (export singleton `ConcurrencyGate`)
- Modify: `src/ratelimit/provider-limiter.ts`
- Modify: `src/providers/circuit-breaker.ts` (make `failureThreshold` configurable)
- Modify: `src/providers/registry.ts`
- Modify: `src/engine/runtime.ts` (`withProviderResilience` + `isTransientStreamError`)
- Create: `__tests__/ratelimit/concurrency-gate.test.ts`
- Modify: `__tests__/ratelimit/provider-limiter.test.ts` (extend existing)
- Create: `__tests__/engine/resilience.test.ts`

### 1a — `ConcurrencyGate` semaphore (`src/ratelimit/concurrency-gate.ts`)

New file. A semaphore with acquire/release semantics, queue-wait timeout, and fail-fast on circuit-open signal.

```typescript
export interface ConcurrencyGateOptions {
  /** Maximum number of in-flight calls at any moment. Default: 2. */
  maxConcurrent: number;
  /**
   * Max ms a caller will wait for a slot before rejecting with
   * ConcurrencyTimeoutError. Default: 30_000.
   */
  queueTimeoutMs: number;
}

export class ConcurrencyTimeoutError extends Error {
  constructor() { super("Timed out waiting for a provider concurrency slot"); }
}

export class CircuitOpenError extends Error {
  constructor() { super("Provider circuit is open — call rejected fast"); }
}

export class ConcurrencyGate {
  private _inflight = 0;
  private _queue: Array<{ resolve: () => void; reject: (e: Error) => void; timer: NodeJS.Timeout }> = [];
  private _circuitOpen = false;

  constructor(private readonly opts: ConcurrencyGateOptions) {}

  /**
   * Acquire a slot. Returns a release function.
   * Throws CircuitOpenError if circuit is open.
   * Throws ConcurrencyTimeoutError if queue wait exceeds queueTimeoutMs.
   */
  async acquire(): Promise<() => void> { ... }

  /** Signal that the circuit is open — drain queue with CircuitOpenError. */
  notifyCircuitOpen(): void { ... }

  /** Signal that the circuit is closed again. */
  notifyCircuitClosed(): void { ... }

  get inflight(): number { return this._inflight; }
  get queued(): number { return this._queue.length; }
}
```

**Key behaviors:**
- `acquire()` waits if `_inflight >= maxConcurrent`, up to `queueTimeoutMs`
- When `notifyCircuitOpen()` is called, all queued waiters immediately reject with `CircuitOpenError`
- `release()` is idempotent — double-release is a no-op (defensive)
- A single global `ConcurrencyGate` instance is created in `src/ratelimit/index.ts` and exported; max concurrent is read from config (`rateLimit.maxConcurrentProviderCalls`, default 2)
- **Cross-provider behavior:** The singleton gate is intentionally shared across all providers. When any one provider opens the circuit, in-flight callers from all providers are drained. This is correct for StackOwl — all providers share the same Anthropic account and concurrent request limit. If per-provider gating is needed in future, `ConcurrencyGate` can be instantiated per-provider name; that is out of scope for this spec.

### 1b — Wire `ConcurrencyGate` into `RateLimitedProvider`

In `src/ratelimit/provider-limiter.ts`, inject `ConcurrencyGate` and wrap every `chat()`, `chatWithTools()`, `chatStream()`, `chatWithToolsStream()` method:

```typescript
async chat(messages, model, opts) {
  const release = await this.gate.acquire();   // blocks if 2 in-flight
  try {
    return await this.inner.chat(messages, model, opts);
  } finally {
    release();
  }
}
```

The gate is shared (singleton) across all `RateLimitedProvider` instances — it is the single global in-flight counter.

### 1c — Apply `RateLimitedProvider` in `ProviderRegistry.register()`

In `src/providers/registry.ts`, wrap every provider on registration:

```typescript
register(provider: ModelProvider): void {
  const limited = new RateLimitedProvider(provider, this.rateLimiter, this.concurrencyGate);
  this.providers.set(provider.name, { provider: limited, breaker: new ProviderCircuitBreaker(...) });
}
```

**Effect:** Every subsystem that receives a provider from the registry (IntentClarifier, EpisodicMemory, ParliamentAutoTrigger, etc.) automatically gets the concurrency gate and rate limiter — zero changes to those subsystems.

### 1d — Fix `withProviderResilience` to feed the circuit breaker

In `src/engine/runtime.ts`, modify `withProviderResilience`:

**Before each attempt:**
```typescript
if (registry.isProviderOpen(provider.name)) {
  // Skip to alternate provider immediately — don't attempt the open one
  throw new ProviderOpenError(provider.name);
}
```

**After each failed attempt with 429:**
```typescript
registry.recordProviderResult(provider.name, false);   // feed the breaker
```

**After a successful attempt:**
```typescript
registry.recordProviderResult(provider.name, true);    // allow breaker to close
```

### 1e — Differentiate 429 from 5xx in `isTransientStreamError`

The Anthropic SDK exports `RateLimitError` (status 429) and `InternalServerError` (status 500–599) as typed classes from `@anthropic-ai/sdk`. Use `instanceof` checks — string-matching on `.message` is fragile and breaks when the SDK changes error formatting.

In `src/engine/runtime.ts`, split the error classification:

```typescript
import { RateLimitError, InternalServerError } from "@anthropic-ai/sdk";

function isRateLimitError(err: unknown): boolean {
  // Use SDK-typed class first; fall back to status property for non-Anthropic providers
  if (err instanceof RateLimitError) return true;
  const status = (err as { status?: number }).status;
  return status === 429;
}

function isTransientStreamError(err: unknown): boolean {
  // 500, 502, 503, 504 — provider-side transient failures
  if (err instanceof InternalServerError) return true;
  const status = (err as { status?: number }).status;
  if (typeof status === "number" && status >= 500 && status < 600) return true;
  const msg = String((err as Error).message ?? "");
  return msg.includes("ECONNRESET");
}
```

In the retry loop:
- `isRateLimitError` → call `recordProviderResult(name, false)`; parse `Retry-After` header; sleep that duration (or 30s default); trip breaker immediately (threshold = 1 for 429)
- `isTransientStreamError` → call `recordProviderResult(name, false)`; use exponential backoff + jitter; trip breaker only after N consecutive failures (default threshold = 5, unchanged)

**Breaker threshold for 429:** `ProviderCircuitBreaker` currently has `failureThreshold` hardcoded to `5`. To open immediately on a single 429, the constructor must accept a configurable threshold. Modify `src/providers/circuit-breaker.ts` to accept `failureThreshold` as a constructor parameter (default: 5). The registry then creates the breaker for each provider as `new ProviderCircuitBreaker(recoveryTimeoutMs, 1)` when the registered provider is the primary LLM provider (Anthropic). Non-primary providers keep the default threshold of 5.

### 1f — Parse `Retry-After` + add jitter

In the retry backoff calculation:

```typescript
function backoffMs(attempt: number, retryAfterMs?: number): number {
  const base = retryAfterMs ?? BASE_DELAY_MS * Math.pow(2, attempt);
  const jitter = base * 0.2 * (Math.random() * 2 - 1);  // ±20%
  return Math.max(100, Math.round(base + jitter));
}
```

Parse `Retry-After` from the error. The Anthropic SDK's `APIError.headers` is a Fetch API `Headers` object — use `.get()`, not bracket access. For non-SDK errors, fall back gracefully:

```typescript
import { APIError } from "@anthropic-ai/sdk";

function parseRetryAfterMs(err: unknown): number | undefined {
  // Anthropic SDK error: headers is a Fetch API Headers object (.get(), not bracket)
  if (err instanceof APIError && err.headers) {
    const val = err.headers.get("retry-after");
    if (val) return parseInt(val, 10) * 1000;
  }
  return undefined;
}
```

### 1g — Notify `ConcurrencyGate` when circuit opens/closes

In `ProviderRegistry.recordProviderResult()`, after state transitions:
```typescript
if (breaker.isOpen() && !wasOpen) {
  this.concurrencyGate.notifyCircuitOpen();
}
if (!breaker.isOpen() && wasOpen) {
  this.concurrencyGate.notifyCircuitClosed();
}
```

### Phase 1 Verification

After Phase 1: type a short message; confirm via logs that:
- Only 1 concurrent in-flight provider call at a time (check `concurrencyGate.inflight` log)
- First 429 trips the breaker (log: `circuit breaker opened`)
- Subsequent calls fail-fast without hitting the API (log: `circuit open — fast fail`)
- Retry backoff respects `Retry-After` header

---

## Phase 2 — Background Job Cleanup

**Files:**
- Create: `src/queue/llm-task-queue.ts`
- Modify: `src/gateway/core.ts` (background job routing + `preferenceRecognizer` deferral)
- Modify: `src/cognition/loop.ts` (share circuit breaker state)
- Modify: `__tests__/queue/llm-task-queue.test.ts` (new)

### 2a — `LLMTaskQueue` (concurrency: 1)

Create `src/queue/llm-task-queue.ts` — a `TaskQueue` instance pre-configured with `concurrency: 1`. The existing `TaskQueue` constructor accepts `concurrency` and `maxQueueSize` only — it does not accept `defaultPriority`. Priority is set per `enqueue()` call. All callers in `gateway/core.ts` must pass `priority: "low"` explicitly.

```typescript
// Single export — one shared instance at concurrency 1
export const llmTaskQueue = new TaskQueue({ concurrency: 1 });
```

In `gateway/core.ts`, replace `runBackground("episode-extract", ...)`, `runBackground("pellet-flywheel", ...)`, `runBackground("opinion-form", ...)`, and other LLM-calling background tasks with:

```typescript
llmTaskQueue.enqueue({ name: "episode-extract", priority: "low", execute: async () => { ... } });
```

Non-LLM background tasks (session persistence, analytics, telemetry) remain on the existing `TaskQueue` at concurrency 3.

Non-LLM background tasks (session persistence, analytics, telemetry) remain on the existing `TaskQueue` at concurrency 3.

### 2b — Defer `preferenceRecognizer.recognizeFromMessage`

In `gateway/core.ts`, move the `await preferenceRecognizer.recognizeFromMessage(...)` call (confirmed at line 1754–1757, blocking before the main engine) to a post-response `llmTaskQueue.enqueue()`.

**Safety verified:** `recognizeFromMessage` updates the recognizer's internal preference store. The output is used via `preferenceRecognizer.buildContextString(0.5)` which reads from the same store. This output feeds into `buildSystemPrompt()` on the **next** turn only — the current turn's system prompt is assembled before `recognizeFromMessage` is even called today. Deferring by one response is therefore safe by construction: the current turn is unaffected, and the next turn reads the latest preferences (which will have been written by the deferred task before the next request arrives in practice).

Before the change, the call sequence is:
```
preferenceRecognizer (await, blocks main engine)
→ intentClarifier (await)
→ main engine
```

After:
```
intentClarifier (await)
→ main engine
→ [post-response] llmTaskQueue: preferenceRecognizer
```

### 2c — Fix `CognitiveLoop` isolated backoff

In `src/cognition/loop.ts`, replace the isolated `_rateLimitedUntil` field with a check against the shared circuit breaker state:

```typescript
// BEFORE: isolated per-loop state
if (Date.now() < this._rateLimitedUntil) return;

// AFTER: shared circuit breaker state
if (this.registry.isProviderOpen(this.providerName)) {
  log.cognition.debug("loop.tick: primary provider circuit open — skipping tick");
  return;
}
```

This ensures the cognitive loop's activity is gated by the same breaker that governs the main conversation path.

### Phase 2 Verification

After Phase 2:
- Background LLM jobs appear in logs with `priority: low`
- `preferenceRecognizer` log entry appears AFTER the main response log, not before
- `CognitiveLoop` tick skips when breaker is open (check logs)
- Under sustained load, at most 1 concurrent background LLM task is running

---

## Non-Goals (Phase 3 — separate spec)

The following are confirmed improvements but are **not in scope for this spec**:

- **Trivial message fast-path**: extend `isConversational` (currently English-only, violates multilingual platform rules) to skip expensive subsystems for short/greeting messages. Separate spec — performance optimization.
- **Parliament `parallel-runner.ts` concurrency fix**: route `Promise.allSettled` owl calls through `ConcurrencyGate`. Separate spec — parliament-specific.
- **`TriageClassifier` gate wiring**: use existing `TriageClassifier` to skip cognitive subsystems based on message class. Separate spec.

---

## Files Touched

| File | Phase | Change |
|------|-------|--------|
| `src/ratelimit/concurrency-gate.ts` | 1 | **Create** — semaphore with acquire/release/queue-timeout/circuit-open notify |
| `src/ratelimit/index.ts` | 1 | **Modify** — export singleton `ConcurrencyGate` instance; read `maxConcurrentProviderCalls` from config |
| `src/ratelimit/provider-limiter.ts` | 1 | **Modify** — inject `ConcurrencyGate`; wrap all `chat*()` methods with acquire/release |
| `src/providers/circuit-breaker.ts` | 1 | **Modify** — make `failureThreshold` a constructor parameter (default 5) |
| `src/providers/registry.ts` | 1 | **Modify** — wrap every registered provider with `RateLimitedProvider` + inject singleton `ConcurrencyGate`; pass `failureThreshold: 1` for Anthropic providers |
| `src/engine/runtime.ts` | 1 | **Modify** — fix `withProviderResilience`: feed breaker on 429/success; check `isOpen()` before attempt; parse `Retry-After` via `APIError.headers.get()`; add jitter; split `isTransientStreamError` / `isRateLimitError` using SDK `instanceof` |
| `src/queue/llm-task-queue.ts` | 2 | **Create** — shared `TaskQueue` instance, concurrency 1 |
| `src/gateway/core.ts` | 2 | **Modify** — route LLM background jobs through `llmTaskQueue` with `priority: "low"`; defer `preferenceRecognizer` to post-response |
| `src/cognition/loop.ts` | 2 | **Modify** — replace isolated `_rateLimitedUntil` with shared circuit breaker check |
| `__tests__/ratelimit/concurrency-gate.test.ts` | 1 | **Create** — unit tests for semaphore acquire/release/timeout/circuit-open |
| `__tests__/ratelimit/provider-limiter.test.ts` | 1 | **Modify** — extend existing tests with concurrency gate injection |
| `__tests__/engine/resilience.test.ts` | 1 | **Create** — tests for `isRateLimitError`, `isTransientStreamError`, `backoffMs`, breaker wiring |
| `__tests__/queue/llm-task-queue.test.ts` | 2 | **Create** — tests for LLM queue concurrency=1 and priority enforcement |

Panel infrastructure, tools, TUI, Telegram, and owl DNA are **not touched**.
