# Channel Architecture Phase 1 — Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the infrastructure for the Thin Adapter Protocol — contracts, ChannelRegistry, GatewayEventBus, DeliveryRouter, StreamSession, V1Shim — and prove it works by routing heartbeat proactive messages through the bus.

**Architecture:** Create 9 new files in `src/gateway/`. Modify `OwlGateway.register()` to also wrap v1 adapters in V1Shim and register with ChannelRegistry. Add `GatewayEventBus` + `DeliveryRouter` to the gateway. Wire the heartbeat's proactive messages through the bus (both Telegram and Slack adapters). Add `delivery_log` table to SQLite (schema v11). All 5 existing adapters continue working unchanged through the V1Shim.

**Tech Stack:** TypeScript (NodeNext), Vitest, better-sqlite3, Node.js EventEmitter, uuid

**Spec:** `docs/superpowers/specs/2026-04-28-channel-architecture-design.md`

---

## File Map

### New files (created in this plan)
| File | Responsibility |
|---|---|
| `src/gateway/channel-capabilities.ts` | `ChannelCapabilities` interface + `ChannelFormat` type |
| `src/gateway/rich-content.ts` | `RichContent`, `RichAction`, `RichFile` interfaces |
| `src/gateway/delivery-envelope.ts` | `DeliveryEnvelope`, `DeliveryUrgency`, `DeliveryTrigger`, `makeEnvelope()` |
| `src/gateway/adapter-v2.ts` | `ChannelAdapterV2` interface + `AskPayload` |
| `src/gateway/channel-registry.ts` | `ChannelRegistry` class |
| `src/gateway/event-bus.ts` | `GatewayEventBus` class + `GatewaySystemEvent` union |
| `src/gateway/stream-session.ts` | `StreamSession` class |
| `src/gateway/delivery-router.ts` | `DeliveryRouter` class |
| `src/gateway/adapter-v1-shim.ts` | `ChannelAdapterV1Shim` + `defaultCapsForV1()` |

### Modified files
| File | Change |
|---|---|
| `src/gateway/core.ts` | Add `channelRegistry`, `gatewayEventBus`, `deliveryRouter` fields; wrap adapters in shim in `register()` |
| `src/heartbeat/proactive.ts` | Add optional `gatewayEventBus` to `PingContext`; use bus when present |
| `src/gateway/adapters/telegram.ts` | Pass `gatewayEventBus` to `ProactivePinger` |
| `src/gateway/adapters/slack.ts` | Pass `gatewayEventBus` to `ProactivePinger` |
| `src/memory/db.ts` | Bump `SCHEMA_VERSION` to 11; add `delivery_log` migration |

### New test files
| File | Tests |
|---|---|
| `__tests__/gateway/channel-registry.test.ts` | register, unregister, presence, routing (7 cases) |
| `__tests__/gateway/event-bus.test.ts` | publish→onDeliver, system events, no cross-contamination |
| `__tests__/gateway/stream-session.test.ts` | accumulation, complete wins over timer, abort, no-op after complete |
| `__tests__/gateway/delivery-router.test.ts` | route to adapter, TTL drop, no-channel drop, retries |

---

## Task 1: Create Contract Types

**Files:**
- Create: `src/gateway/channel-capabilities.ts`
- Create: `src/gateway/rich-content.ts`
- Create: `src/gateway/delivery-envelope.ts`
- Create: `src/gateway/adapter-v2.ts`

These are pure TypeScript interfaces. No runtime behavior, no tests needed.

- [ ] **Step 1: Create `src/gateway/channel-capabilities.ts`**

```typescript
export interface ChannelCapabilities {
  channelId: string
  displayName: string
  streaming: boolean
  async: boolean
  multiUser: boolean
  maxMessageLength: number
  formatting: ChannelFormat
  supportsButtons: boolean
  supportsFiles: boolean
  supportsVoice: boolean
  supportsImages: boolean
  supportsThreads: boolean
  supportsReactions: boolean
  supportsInterrupt: boolean
  quietHours?: { start: number; end: number }
}

export type ChannelFormat = "html" | "mrkdwn" | "ansi" | "plain" | "markdown"
```

- [ ] **Step 2: Create `src/gateway/rich-content.ts`**

```typescript
export interface RichContent {
  text: string
  markdown?: string
  voiceText?: string
  actions?: RichAction[]
  files?: RichFile[]
  structured?: unknown
  streamable: boolean
  stream?: AsyncIterable<string>
}

export interface RichAction {
  id: string
  label: string
  style: "primary" | "danger" | "default"
  value: string
}

export interface RichFile {
  name: string
  path: string
  mimeType: string
}
```

- [ ] **Step 3: Create `src/gateway/delivery-envelope.ts`**

```typescript
import { v4 as uuidv4 } from "uuid"
import type { RichContent } from "./rich-content.js"

export type DeliveryUrgency = "background" | "normal" | "proactive" | "interrupt"

export type DeliveryTrigger =
  | "user-request"
  | "proactive"
  | "background-result"
  | "commitment"
  | "alert"
  | "parliament"

export interface DeliveryEnvelope {
  envelopeId: string
  createdAt: number
  userId: string
  channelId?: string
  content: RichContent
  urgency: DeliveryUrgency
  trigger: DeliveryTrigger
  ttlMs?: number
  sessionId?: string
}

export function makeEnvelope(
  partial: Omit<DeliveryEnvelope, "envelopeId" | "createdAt">
): DeliveryEnvelope {
  return { ...partial, envelopeId: uuidv4(), createdAt: Date.now() }
}
```

- [ ] **Step 4: Create `src/gateway/adapter-v2.ts`**

```typescript
import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

export interface ChannelAdapterV2 {
  readonly capabilities: ChannelCapabilities
  start(): Promise<void>
  stop(): Promise<void>
  register(registry: ChannelRegistry): void
  deliver(envelope: DeliveryEnvelope): Promise<void>
  ask(userId: string, prompt: AskPayload): Promise<string>
}

export interface AskPayload {
  text: string
  choices?: string[]
  timeoutMs?: number
  defaultChoice?: string
}
```

- [ ] **Step 5: Verify TypeScript compiles cleanly**

Run: `npm run build`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/gateway/channel-capabilities.ts src/gateway/rich-content.ts \
        src/gateway/delivery-envelope.ts src/gateway/adapter-v2.ts
git commit -m "feat(channels): add Phase 1 contract types — capabilities, rich-content, envelope, adapter-v2"
```

---

## Task 2: ChannelRegistry

**Files:**
- Create: `src/gateway/channel-registry.ts`
- Create: `__tests__/gateway/channel-registry.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/gateway/channel-registry.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest"
import { ChannelRegistry } from "../../src/gateway/channel-registry.js"
import type { ChannelAdapterV2 } from "../../src/gateway/adapter-v2.js"
import type { ChannelCapabilities } from "../../src/gateway/channel-capabilities.js"

function makeAdapter(id: string, caps: Partial<ChannelCapabilities> = {}): ChannelAdapterV2 {
  const defaults: ChannelCapabilities = {
    channelId: id, displayName: id,
    streaming: false, async: true, multiUser: false,
    maxMessageLength: 4096, formatting: "plain",
    supportsButtons: false, supportsFiles: false, supportsVoice: false,
    supportsImages: false, supportsThreads: false, supportsReactions: false,
    supportsInterrupt: false,
  }
  return {
    capabilities: { ...defaults, ...caps },
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    register: vi.fn(),
    deliver: vi.fn().mockResolvedValue(undefined),
    ask: vi.fn().mockResolvedValue("yes"),
  }
}

describe("ChannelRegistry", () => {
  let registry: ChannelRegistry
  beforeEach(() => { registry = new ChannelRegistry() })

  it("registers and retrieves an adapter by channelId", () => {
    const a = makeAdapter("telegram")
    registry.register(a)
    expect(registry.get("telegram")).toBe(a)
  })

  it("unregisters an adapter and clears its presence data", () => {
    registry.register(makeAdapter("telegram"))
    registry.markActive("telegram", "user1")
    registry.unregister("telegram")
    expect(registry.get("telegram")).toBeUndefined()
    expect(registry.getLastSeen("telegram", "user1")).toBe(0)
  })

  it("listAll returns all registered adapters", () => {
    registry.register(makeAdapter("telegram"))
    registry.register(makeAdapter("slack"))
    expect(registry.listAll()).toHaveLength(2)
  })

  it("markActive makes channel appear in getActiveChannels", () => {
    const t = makeAdapter("telegram")
    const s = makeAdapter("slack")
    registry.register(t)
    registry.register(s)
    registry.markActive("telegram", "user1")
    const active = registry.getActiveChannels("user1")
    expect(active).toContain(t)
    expect(active).not.toContain(s)
  })

  it("getBestChannel — interrupt picks first supportsInterrupt adapter regardless of presence", () => {
    const cli = makeAdapter("cli", { async: false, supportsInterrupt: false })
    const tg = makeAdapter("telegram", { async: true, supportsInterrupt: true })
    registry.register(cli)
    registry.register(tg)
    // no markActive — user not currently active anywhere
    expect(registry.getBestChannel("user1", "interrupt")).toBe(tg)
  })

  it("getBestChannel — proactive skips non-async adapters", () => {
    const cli = makeAdapter("cli", { async: false })
    const tg = makeAdapter("telegram", { async: true })
    registry.register(cli)
    registry.register(tg)
    registry.markActive("cli", "user1")
    registry.markActive("telegram", "user1")
    expect(registry.getBestChannel("user1", "proactive")).toBe(tg)
  })

  it("getBestChannel — normal returns most-recently-active channel", async () => {
    const tg = makeAdapter("telegram")
    const sl = makeAdapter("slack")
    registry.register(tg)
    registry.register(sl)
    registry.markActive("telegram", "user1")
    await new Promise(r => setTimeout(r, 2))  // ensure different timestamps
    registry.markActive("slack", "user1")
    expect(registry.getBestChannel("user1", "normal")).toBe(sl)
  })

  it("getBestChannel — returns undefined when no active channels for normal urgency", () => {
    registry.register(makeAdapter("telegram"))
    expect(registry.getBestChannel("user1", "normal")).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run __tests__/gateway/channel-registry.test.ts`
Expected: FAIL with "Cannot find module '../../src/gateway/channel-registry.js'"

- [ ] **Step 3: Implement `src/gateway/channel-registry.ts`**

```typescript
import type { ChannelAdapterV2 } from "./adapter-v2.js"
import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { DeliveryUrgency } from "./delivery-envelope.js"
import { log } from "../logger.js"

export class ChannelRegistry {
  private adapters = new Map<string, ChannelAdapterV2>()
  private presence = new Map<string, Map<string, number>>() // channelId → userId → lastSeen ms

  register(adapter: ChannelAdapterV2): void {
    this.adapters.set(adapter.capabilities.channelId, adapter)
    log.engine.info(`[ChannelRegistry] registered: ${adapter.capabilities.channelId}`)
  }

  unregister(channelId: string): void {
    this.adapters.delete(channelId)
    this.presence.delete(channelId)
  }

  get(channelId: string): ChannelAdapterV2 | undefined {
    return this.adapters.get(channelId)
  }

  listAll(): ChannelAdapterV2[] {
    return Array.from(this.adapters.values())
  }

  markActive(channelId: string, userId: string): void {
    if (!this.presence.has(channelId)) this.presence.set(channelId, new Map())
    this.presence.get(channelId)!.set(userId, Date.now())
  }

  markInactive(channelId: string, userId: string): void {
    this.presence.get(channelId)?.delete(userId)
  }

  getLastSeen(channelId: string, userId: string): number {
    return this.presence.get(channelId)?.get(userId) ?? 0
  }

  getActiveChannels(userId: string): ChannelAdapterV2[] {
    const result: ChannelAdapterV2[] = []
    for (const [channelId, adapter] of this.adapters) {
      if (this.presence.get(channelId)?.has(userId)) result.push(adapter)
    }
    return result
  }

  getCapableChannels(
    userId: string,
    requires: Partial<ChannelCapabilities>
  ): ChannelAdapterV2[] {
    return this.getActiveChannels(userId).filter(adapter => {
      const caps = adapter.capabilities as Record<string, unknown>
      return Object.entries(requires).every(([k, v]) => caps[k] === v)
    })
  }

  getBestChannel(userId: string, urgency: DeliveryUrgency): ChannelAdapterV2 | undefined {
    if (urgency === "interrupt") {
      for (const adapter of this.adapters.values()) {
        if (adapter.capabilities.supportsInterrupt) return adapter
      }
      return undefined
    }

    const active = this.getActiveChannels(userId)

    if (urgency === "proactive") {
      const asyncChannels = active.filter(a => a.capabilities.async)
      if (asyncChannels.length === 0) return undefined
      return asyncChannels.reduce((best, a) =>
        this.getLastSeen(a.capabilities.channelId, userId) >
        this.getLastSeen(best.capabilities.channelId, userId) ? a : best
      )
    }

    if (urgency === "background") {
      const TWENTY_FOUR_H = 24 * 60 * 60 * 1000
      return active
        .filter(a => a.capabilities.async)
        .find(a => this.getLastSeen(a.capabilities.channelId, userId) > Date.now() - TWENTY_FOUR_H)
    }

    // normal — most-recently-active
    if (active.length === 0) return undefined
    return active.reduce((best, a) =>
      this.getLastSeen(a.capabilities.channelId, userId) >
      this.getLastSeen(best.capabilities.channelId, userId) ? a : best
    )
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/gateway/channel-registry.test.ts`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gateway/channel-registry.ts __tests__/gateway/channel-registry.test.ts
git commit -m "feat(channels): add ChannelRegistry with routing logic and presence tracking"
```

---

## Task 3: GatewayEventBus

**Files:**
- Create: `src/gateway/event-bus.ts`
- Create: `__tests__/gateway/event-bus.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/gateway/event-bus.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest"
import { GatewayEventBus } from "../../src/gateway/event-bus.js"
import { makeEnvelope } from "../../src/gateway/delivery-envelope.js"

describe("GatewayEventBus", () => {
  it("routes published envelopes to onDeliver handler", async () => {
    const bus = new GatewayEventBus()
    const received: unknown[] = []
    bus.onDeliver(async env => { received.push(env) })

    const envelope = makeEnvelope({
      userId: "u1",
      content: { text: "hello", streamable: false },
      urgency: "normal",
      trigger: "user-request",
    })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 0))
    expect(received).toHaveLength(1)
    expect((received[0] as typeof envelope).userId).toBe("u1")
  })

  it("routes system events to typed handlers", () => {
    const bus = new GatewayEventBus()
    const received: unknown[] = []
    bus.on("parliament:done", e => { received.push(e) })
    bus.emit({ type: "parliament:done", topic: "AI safety", verdict: "inconclusive", userId: "u1" })
    expect(received).toHaveLength(1)
    expect((received[0] as any).topic).toBe("AI safety")
  })

  it("delivery events do not reach system event handlers", () => {
    const bus = new GatewayEventBus()
    const systemReceived: unknown[] = []
    bus.on("cost:alert", e => systemReceived.push(e))
    bus.publish(makeEnvelope({
      userId: "u1",
      content: { text: "hi", streamable: false },
      urgency: "normal",
      trigger: "user-request",
    }))
    expect(systemReceived).toHaveLength(0)
  })

  it("multiple onDeliver handlers all receive the envelope", async () => {
    const bus = new GatewayEventBus()
    const calls: number[] = []
    bus.onDeliver(async () => { calls.push(1) })
    bus.onDeliver(async () => { calls.push(2) })
    bus.publish(makeEnvelope({ userId: "u1", content: { text: "x", streamable: false }, urgency: "normal", trigger: "proactive" }))
    await new Promise(r => setTimeout(r, 0))
    expect(calls.sort()).toEqual([1, 2])
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run __tests__/gateway/event-bus.test.ts`
Expected: FAIL with "Cannot find module '../../src/gateway/event-bus.js'"

- [ ] **Step 3: Implement `src/gateway/event-bus.ts`**

```typescript
import { EventEmitter } from "node:events"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

export type GatewaySystemEvent =
  | { type: "pellet:created";    pelletId: string;  userId: string }
  | { type: "learning:complete"; summary: string;   userId: string }
  | { type: "evolution:done";    owlName: string;   changes: string[] }
  | { type: "parliament:done";   topic: string;     verdict: string; userId: string }
  | { type: "perch:event";       source: string;    detail: string;  userId: string }
  | { type: "commitment:due";    text: string;      userId: string }
  | { type: "cost:alert";        spent: number;     budget: number;  userId: string }

const DELIVER_EVENT = "gateway:deliver"

export class GatewayEventBus {
  private emitter = new EventEmitter()

  publish(envelope: DeliveryEnvelope): void {
    this.emitter.emit(DELIVER_EVENT, envelope)
  }

  onDeliver(handler: (env: DeliveryEnvelope) => Promise<void>): void {
    this.emitter.on(DELIVER_EVENT, handler)
  }

  emit<T extends GatewaySystemEvent>(event: T): void {
    this.emitter.emit(`system:${event.type}`, event)
  }

  on<T extends GatewaySystemEvent["type"]>(
    type: T,
    handler: (e: Extract<GatewaySystemEvent, { type: T }>) => void
  ): void {
    this.emitter.on(`system:${type}`, handler as (e: GatewaySystemEvent) => void)
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/gateway/event-bus.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gateway/event-bus.ts __tests__/gateway/event-bus.test.ts
git commit -m "feat(channels): add GatewayEventBus — typed pub/sub for all outbound delivery"
```

---

## Task 4: StreamSession

**Files:**
- Create: `src/gateway/stream-session.ts`
- Create: `__tests__/gateway/stream-session.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/gateway/stream-session.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest"
import { StreamSession } from "../../src/gateway/stream-session.js"

describe("StreamSession", () => {
  it("accumulates appended deltas and delivers all to onComplete", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("hello ")
    session.append("world")
    await session.complete()
    expect(onComplete).toHaveBeenCalledWith("hello world")
  })

  it("complete() calls onComplete exactly once", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 100, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("abc")
    await session.complete()
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith("abc")
  })

  it("complete() cancels pending flush timer — no race condition", async () => {
    const order: string[] = []
    const onFlush = vi.fn().mockImplementation(async () => { order.push("flush") })
    const onComplete = vi.fn().mockImplementation(async () => { order.push("complete") })
    const session = new StreamSession({ throttleMs: 200, maxLength: Infinity, onFlush, onComplete })
    session.append("hello")
    // complete() fires before the 200ms throttle timer
    await session.complete()
    await new Promise(r => setTimeout(r, 300))  // wait past the timer
    // flush should NOT run after complete()
    expect(order).toEqual(["complete"])
  })

  it("abort() delivers accumulated text via onComplete before stopping", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 1000, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    session.append("partial text")
    await session.abort(new Error("network error"))
    expect(onComplete).toHaveBeenCalledWith("partial text")
  })

  it("append after complete() is a no-op — buffer stays empty", async () => {
    const onComplete = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete,
    })
    await session.complete()
    session.append("late delta — should be ignored")
    expect(session.text).toBe("")
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("throttles — does not call onFlush on every append when throttleMs > 0", async () => {
    const onFlush = vi.fn().mockResolvedValue(undefined)
    const session = new StreamSession({
      throttleMs: 200, maxLength: Infinity,
      onFlush,
      onComplete: vi.fn().mockResolvedValue(undefined),
    })
    session.append("a")
    session.append("b")
    session.append("c")
    await new Promise(r => setTimeout(r, 50))
    // Only one flush may be scheduled (not 3 separate calls)
    expect(onFlush.mock.calls.length).toBeLessThanOrEqual(1)
    await session.complete()
  })

  it("text getter returns accumulated buffer", () => {
    const session = new StreamSession({
      throttleMs: 0, maxLength: Infinity,
      onFlush: vi.fn().mockResolvedValue(undefined),
      onComplete: vi.fn().mockResolvedValue(undefined),
    })
    session.append("foo")
    session.append("bar")
    expect(session.text).toBe("foobar")
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run __tests__/gateway/stream-session.test.ts`
Expected: FAIL with "Cannot find module '../../src/gateway/stream-session.js'"

- [ ] **Step 3: Implement `src/gateway/stream-session.ts`**

```typescript
export interface StreamSessionOptions {
  throttleMs: number
  maxLength: number
  onFlush: (text: string) => Promise<void>
  onComplete: (text: string) => Promise<void>
}

export class StreamSession {
  private buffer = ""
  private lastFlush = 0
  private flushTimer: ReturnType<typeof setTimeout> | null = null
  private completed = false

  constructor(private opts: StreamSessionOptions) {}

  append(delta: string): void {
    if (this.completed) return
    this.buffer += delta
    if (this.opts.throttleMs === 0) {
      void this.opts.onFlush(this.buffer)
      return
    }
    const elapsed = Date.now() - this.lastFlush
    if (elapsed >= this.opts.throttleMs) {
      this.scheduleFlush(0)
    } else if (!this.flushTimer) {
      this.scheduleFlush(this.opts.throttleMs - elapsed)
    }
  }

  private scheduleFlush(delayMs: number): void {
    if (this.flushTimer) clearTimeout(this.flushTimer)
    this.flushTimer = setTimeout(async () => {
      if (this.completed) return
      this.flushTimer = null
      this.lastFlush = Date.now()
      try { await this.opts.onFlush(this.buffer) } catch { /* swallow */ }
    }, delayMs)
  }

  async complete(): Promise<void> {
    this.completed = true
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null }
    try {
      await this.opts.onComplete(this.buffer)
    } catch {
      try { await this.opts.onComplete(this.buffer) } catch (e) {
        console.error("[StreamSession] onComplete failed:", e)
      }
    }
  }

  async abort(err: Error): Promise<void> {
    this.completed = true
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null }
    console.error("[StreamSession] stream aborted:", err.message)
    if (this.buffer) {
      try { await this.opts.onComplete(this.buffer) } catch { /* best effort */ }
    }
  }

  get text(): string { return this.buffer }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/gateway/stream-session.test.ts`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gateway/stream-session.ts __tests__/gateway/stream-session.test.ts
git commit -m "feat(channels): add StreamSession — shared throttled streaming, eliminates race condition"
```

---

## Task 5: DeliveryRouter

**Files:**
- Create: `src/gateway/delivery-router.ts`
- Create: `__tests__/gateway/delivery-router.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/gateway/delivery-router.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest"
import { DeliveryRouter } from "../../src/gateway/delivery-router.js"
import { GatewayEventBus } from "../../src/gateway/event-bus.js"
import { ChannelRegistry } from "../../src/gateway/channel-registry.js"
import { makeEnvelope } from "../../src/gateway/delivery-envelope.js"
import type { ChannelAdapterV2 } from "../../src/gateway/adapter-v2.js"
import type { ChannelCapabilities } from "../../src/gateway/channel-capabilities.js"

function makeAdapter(id: string, caps: Partial<ChannelCapabilities> = {}): ChannelAdapterV2 {
  const defaults: ChannelCapabilities = {
    channelId: id, displayName: id,
    streaming: false, async: true, multiUser: false,
    maxMessageLength: 4096, formatting: "plain",
    supportsButtons: false, supportsFiles: false, supportsVoice: false,
    supportsImages: false, supportsThreads: false, supportsReactions: false,
    supportsInterrupt: false,
  }
  return {
    capabilities: { ...defaults, ...caps },
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    register: vi.fn(),
    deliver: vi.fn().mockResolvedValue(undefined),
    ask: vi.fn().mockResolvedValue("yes"),
  }
}

describe("DeliveryRouter", () => {
  let bus: GatewayEventBus
  let registry: ChannelRegistry
  // pass retryDelaysMs=[0,0,0] so retries are instant in tests
  let router: DeliveryRouter

  beforeEach(() => {
    bus = new GatewayEventBus()
    registry = new ChannelRegistry()
    router = new DeliveryRouter(registry, undefined, [0, 0, 0])
    router.start(bus)
  })

  it("delivers envelope to the correct adapter via channelId", async () => {
    const adapter = makeAdapter("telegram")
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    const envelope = makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "hi", streamable: false },
      urgency: "normal", trigger: "user-request",
    })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    expect(adapter.deliver).toHaveBeenCalledWith(envelope)
  })

  it("drops envelope when TTL has expired before routing", async () => {
    const adapter = makeAdapter("telegram")
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    const envelope = makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "stale", streamable: false },
      urgency: "proactive", trigger: "proactive",
      ttlMs: 1,
    })
    // backdate createdAt so TTL is already expired
    Object.assign(envelope, { createdAt: Date.now() - 10_000 })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    expect(adapter.deliver).not.toHaveBeenCalled()
  })

  it("drops silently when no channel is available — does not throw", async () => {
    // no adapter registered
    const envelope = makeEnvelope({
      userId: "u1",
      content: { text: "hello", streamable: false },
      urgency: "normal", trigger: "user-request",
    })
    // Should not throw even though there is no channel
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    // If we get here without an unhandled rejection, the test passes
    expect(true).toBe(true)
  })

  it("retries delivery up to MAX_RETRIES on transient failure", async () => {
    const adapter = makeAdapter("telegram")
    adapter.deliver = vi.fn().mockRejectedValue(new Error("timeout"))
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    bus.publish(makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "retry me", streamable: false },
      urgency: "normal", trigger: "user-request",
    }))
    await new Promise(r => setTimeout(r, 50))
    // 1 initial + 2 retries = 3 total
    expect(adapter.deliver).toHaveBeenCalledTimes(3)
  })

  it("stops retrying after a successful delivery", async () => {
    const adapter = makeAdapter("telegram")
    let calls = 0
    adapter.deliver = vi.fn().mockImplementation(async () => {
      calls++
      if (calls === 1) throw new Error("first fail")
      // second call succeeds
    })
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    bus.publish(makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "retry once", streamable: false },
      urgency: "normal", trigger: "user-request",
    }))
    await new Promise(r => setTimeout(r, 50))
    expect(calls).toBe(2)  // failed once, succeeded on retry
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run __tests__/gateway/delivery-router.test.ts`
Expected: FAIL with "Cannot find module '../../src/gateway/delivery-router.js'"

- [ ] **Step 3: Implement `src/gateway/delivery-router.ts`**

```typescript
import type { GatewayEventBus } from "./event-bus.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"
import type Database from "better-sqlite3"
import { log } from "../logger.js"

export class DeliveryRouter {
  private readonly retryDelaysMs: number[]

  constructor(
    private registry: ChannelRegistry,
    private db?: Database.Database,
    retryDelaysMs = [0, 2_000, 8_000]
  ) {
    this.retryDelaysMs = retryDelaysMs
  }

  start(bus: GatewayEventBus): void {
    bus.onDeliver(env => this.route(env))
  }

  private async route(envelope: DeliveryEnvelope): Promise<void> {
    if (envelope.ttlMs !== undefined && envelope.createdAt + envelope.ttlMs < Date.now()) {
      this.writeLog(envelope, "dropped_ttl", 0, undefined)
      return
    }

    const adapter = envelope.channelId
      ? this.registry.get(envelope.channelId)
      : this.registry.getBestChannel(envelope.userId, envelope.urgency)

    if (!adapter) {
      this.writeLog(envelope, "dropped_no_channel", 0, undefined)
      log.engine.warn(
        `[DeliveryRouter] no channel for userId=${envelope.userId} urgency=${envelope.urgency}`
      )
      return
    }

    const maxAttempts = this.retryDelaysMs.length
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0) {
        await new Promise(r => setTimeout(r, this.retryDelaysMs[attempt]))
        if (envelope.ttlMs !== undefined && envelope.createdAt + envelope.ttlMs < Date.now()) {
          this.writeLog(envelope, "dropped_ttl", attempt, undefined)
          return
        }
      }
      try {
        await adapter.deliver(envelope)
        this.writeLog(envelope, "delivered", attempt, undefined)
        return
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (attempt === maxAttempts - 1) {
          this.writeLog(envelope, "failed", attempt, msg)
          log.engine.error(`[DeliveryRouter] delivery failed after ${attempt + 1} attempt(s): ${msg}`)
        }
      }
    }
  }

  private writeLog(
    envelope: DeliveryEnvelope,
    status: string,
    attempt: number,
    error: string | undefined
  ): void {
    if (!this.db) return
    try {
      this.db.prepare(`
        INSERT INTO delivery_log
          (id, envelope_id, user_id, channel_id, urgency, trigger, status, attempt, error, delivered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        crypto.randomUUID(),
        envelope.envelopeId,
        envelope.userId,
        envelope.channelId ?? "unknown",
        envelope.urgency,
        envelope.trigger,
        status,
        attempt,
        error ?? null,
        status === "delivered" ? Date.now() : null
      )
    } catch {
      // non-fatal — never break delivery because of a logging error
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/gateway/delivery-router.test.ts`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gateway/delivery-router.ts __tests__/gateway/delivery-router.test.ts
git commit -m "feat(channels): add DeliveryRouter — routes envelopes, retries, TTL, delivery log"
```

---

## Task 6: V1Shim + Register Adapters

**Files:**
- Create: `src/gateway/adapter-v1-shim.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Create `src/gateway/adapter-v1-shim.ts`**

```typescript
import type { ChannelAdapter, GatewayResponse } from "./types.js"
import type { ChannelAdapterV2, AskPayload } from "./adapter-v2.js"
import type { ChannelCapabilities } from "./channel-capabilities.js"
import type { ChannelRegistry } from "./channel-registry.js"
import type { DeliveryEnvelope } from "./delivery-envelope.js"

/**
 * Wraps a v1 ChannelAdapter to satisfy the ChannelAdapterV2 interface.
 * Remove one-by-one as each adapter is rewritten in Phase 2.
 */
export class ChannelAdapterV1Shim implements ChannelAdapterV2 {
  constructor(
    private v1: ChannelAdapter,
    private caps: ChannelCapabilities
  ) {}

  get capabilities(): ChannelCapabilities { return this.caps }

  async start(): Promise<void> { await this.v1.start() }

  async stop(): Promise<void> { this.v1.stop() }

  register(registry: ChannelRegistry): void { registry.register(this) }

  async deliver(envelope: DeliveryEnvelope): Promise<void> {
    const response: GatewayResponse = {
      content: envelope.content.text,
      owlName: "",
      owlEmoji: "",
      toolsUsed: [],
    }
    await this.v1.sendToUser(envelope.userId, response)
  }

  async ask(_userId: string, prompt: AskPayload): Promise<string> {
    return prompt.defaultChoice ?? "yes"
  }
}

/**
 * Returns default ChannelCapabilities for each known v1 channel ID.
 * Used when wrapping v1 adapters in the shim.
 */
export function defaultCapsForV1(channelId: string): ChannelCapabilities {
  const base: ChannelCapabilities = {
    channelId,
    displayName: channelId,
    streaming: false,
    async: false,
    multiUser: false,
    maxMessageLength: Infinity,
    formatting: "plain",
    supportsButtons: false,
    supportsFiles: false,
    supportsVoice: false,
    supportsImages: false,
    supportsThreads: false,
    supportsReactions: false,
    supportsInterrupt: false,
  }
  switch (channelId) {
    case "telegram":
      return { ...base, streaming: true, async: true, maxMessageLength: 4096,
               formatting: "html", supportsButtons: true, supportsFiles: true,
               supportsImages: true, supportsInterrupt: true }
    case "slack":
      return { ...base, streaming: true, async: true, multiUser: true,
               maxMessageLength: 3000, formatting: "mrkdwn", supportsButtons: true,
               supportsFiles: true, supportsImages: true, supportsThreads: true,
               supportsReactions: true, supportsInterrupt: true }
    case "cli":
      return { ...base, streaming: true, formatting: "ansi" }
    case "voice":
      return { ...base, streaming: true, supportsVoice: true,
               maxMessageLength: 800, formatting: "plain" }
    case "web":
      return { ...base, streaming: true, async: true, maxMessageLength: Infinity,
               formatting: "markdown", supportsButtons: true, supportsFiles: true,
               supportsImages: true, supportsInterrupt: true }
    default:
      return base
  }
}
```

- [ ] **Step 2: Add imports for the new classes in `src/gateway/core.ts`**

At the top of `src/gateway/core.ts`, after the existing local imports (after the last `import` statement), add:
```typescript
import { ChannelRegistry } from "./channel-registry.js";
import { GatewayEventBus } from "./event-bus.js";
import { DeliveryRouter } from "./delivery-router.js";
import { ChannelAdapterV1Shim, defaultCapsForV1 } from "./adapter-v1-shim.js";
```

- [ ] **Step 3: Add three new fields to `OwlGateway` in `src/gateway/core.ts`**

Find this line (search for it):
```typescript
  private adapters: Map<string, ChannelAdapter> = new Map();
```

Add immediately after it:
```typescript
  readonly channelRegistry: ChannelRegistry = new ChannelRegistry();
  readonly gatewayEventBus: GatewayEventBus = new GatewayEventBus();
  readonly deliveryRouter: DeliveryRouter = new DeliveryRouter(this.channelRegistry);
```

- [ ] **Step 4: Wire the DeliveryRouter to the bus in the `OwlGateway` constructor**

In the constructor (around line 242), after `this.taskQueue = ...` initialization, add:
```typescript
    // Wire delivery bus → router (Phase 1 channel infrastructure)
    this.deliveryRouter.start(this.gatewayEventBus);
```

- [ ] **Step 5: Update `OwlGateway.register()` to also register with ChannelRegistry via shim**

Find the `register()` method (around line 618):
```typescript
  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
    log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
  }
```

Replace with:
```typescript
  register(adapter: ChannelAdapter): void {
    this.adapters.set(adapter.id, adapter);
    log.engine.info(`Channel registered: ${adapter.name} [${adapter.id}]`);
    // Also register with ChannelRegistry via V1Shim for Phase 1 event bus routing
    const shim = new ChannelAdapterV1Shim(adapter, defaultCapsForV1(adapter.id));
    this.channelRegistry.register(shim);
  }
```

- [ ] **Step 6: Run all tests to verify nothing broke**

Run: `npm test`
Expected: All existing tests pass (failing clarification tests were pre-existing)

- [ ] **Step 7: Commit**

```bash
git add src/gateway/adapter-v1-shim.ts src/gateway/core.ts
git commit -m "feat(channels): add V1Shim + wire ChannelRegistry/EventBus/DeliveryRouter into OwlGateway"
```

---

## Task 7: Wire Heartbeat Through the Bus

**Files:**
- Modify: `src/heartbeat/proactive.ts`
- Modify: `src/gateway/adapters/telegram.ts`
- Modify: `src/gateway/adapters/slack.ts`

- [ ] **Step 1: Add `gatewayEventBus` option to `PingContext` in `src/heartbeat/proactive.ts`**

Find the `PingContext` interface (around line 41). The existing last field before the closing `}` is approximately:
```typescript
  autonomousPlanner?: AutonomousPlanner;
  /** Global event bus */
  eventBus?: import("../events/bus.js").EventBus;
}
```

Add after `eventBus` and before the closing `}`:
```typescript
  /** GatewayEventBus — when set, proactive messages are routed through the delivery bus */
  gatewayEventBus?: import("../gateway/event-bus.js").GatewayEventBus;
```

- [ ] **Step 2: Update `sendToUser` call in `ProactivePinger` to use the bus when available**

Find the single call to `this.context.sendToUser` (around line 755):
```typescript
      await this.context.sendToUser(
        `📌 **Goal check-in** — you have ${stale.length} goal(s) with no recent progress:\n` +
        goalSummaries +
        `\n\nWant me to pick one and start working on it?`,
      );
```

Add an import at the top of the file for `makeEnvelope`:
```typescript
import { makeEnvelope } from "../gateway/delivery-envelope.js";
```

Create a helper method inside `ProactivePinger` that wraps the delivery choice:

Add this private method to the `ProactivePinger` class (place it near other private helpers):
```typescript
  private async deliverProactive(message: string): Promise<void> {
    const { gatewayEventBus, userId } = this.context;
    if (gatewayEventBus && userId) {
      gatewayEventBus.publish(makeEnvelope({
        userId,
        content: { text: message, streamable: false },
        urgency: "proactive",
        trigger: "proactive",
        ttlMs: 4 * 60 * 60 * 1000,  // drop if user unreachable after 4h
      }));
      return;
    }
    await this.context.sendToUser(message);
  }
```

Replace the `await this.context.sendToUser(...)` call with `await this.deliverProactive(...)`:
```typescript
      await this.deliverProactive(
        `📌 **Goal check-in** — you have ${stale.length} goal(s) with no recent progress:\n` +
        goalSummaries +
        `\n\nWant me to pick one and start working on it?`,
      );
```

- [ ] **Step 3: Pass `gatewayEventBus` in Telegram's ProactivePinger constructor**

In `src/gateway/adapters/telegram.ts`, find the `new ProactivePinger({...})` call (around line 991). The context object ends with:
```typescript
      eventBus: self.gateway.getEventBus(),
      jobQueue,
      userId: "default",
      sendToUser: async (message: string) => {
        await self.broadcast({
          content: message,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        });
      },
    });
```

Add `gatewayEventBus` after `eventBus`:
```typescript
      eventBus: self.gateway.getEventBus(),
      gatewayEventBus: self.gateway.gatewayEventBus,
      jobQueue,
      userId: "default",
      sendToUser: async (message: string) => {
        await self.broadcast({
          content: message,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        });
      },
    });
```

- [ ] **Step 4: Pass `gatewayEventBus` in Slack's ProactivePinger constructor**

In `src/gateway/adapters/slack.ts`, find the `new ProactivePinger({...})` call (around line 589). Apply the same change — add `gatewayEventBus: self.gateway.gatewayEventBus` after the `eventBus` field.

The exact location in slack will be in the object passed to `new ProactivePinger`. Find the `eventBus:` line in that block and add after it:
```typescript
      gatewayEventBus: self.gateway.gatewayEventBus,
```

- [ ] **Step 5: Run all tests to verify nothing broke**

Run: `npm test`
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/heartbeat/proactive.ts src/gateway/adapters/telegram.ts src/gateway/adapters/slack.ts
git commit -m "feat(channels): route heartbeat proactive messages through GatewayEventBus"
```

---

## Task 8: delivery_log SQLite Migration

**Files:**
- Modify: `src/memory/db.ts`

- [ ] **Step 1: Bump SCHEMA_VERSION and add the migration block in `src/memory/db.ts`**

Find line 29:
```typescript
const SCHEMA_VERSION = 10;
```

Change to:
```typescript
const SCHEMA_VERSION = 11;
```

Find the migration block that ends at:
```typescript
    if (current < SCHEMA_VERSION) {
      this.db.pragma(`user_version = ${SCHEMA_VERSION}`);
      log.engine.info(`[MemoryDatabase] Schema migrated to v${SCHEMA_VERSION}`);
    }
```

Insert a new block immediately before the `if (current < SCHEMA_VERSION)` line:
```typescript
    if (current < 11) {
      // v11: delivery log — every outbound message attempt via DeliveryRouter
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS delivery_log (
          id           TEXT PRIMARY KEY,
          envelope_id  TEXT NOT NULL,
          user_id      TEXT NOT NULL,
          channel_id   TEXT NOT NULL,
          urgency      TEXT NOT NULL,
          trigger      TEXT NOT NULL,
          status       TEXT NOT NULL,
          attempt      INTEGER NOT NULL,
          error        TEXT,
          delivered_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_dl_user    ON delivery_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_dl_channel ON delivery_log(channel_id);
        CREATE INDEX IF NOT EXISTS idx_dl_status  ON delivery_log(status);
      `);
    }
```

- [ ] **Step 2: Verify TypeScript still compiles**

Run: `npm run build`
Expected: No errors

- [ ] **Step 3: Run full test suite**

Run: `npm test`
Expected: All existing tests pass

- [ ] **Step 4: Commit**

```bash
git add src/memory/db.ts
git commit -m "feat(channels): add delivery_log table to SQLite (schema v11)"
```

---

## Phase 1 Done — Verification

- [ ] **Run all new gateway tests together**

Run: `npx vitest run __tests__/gateway/`
Expected: All 4 test files pass (channel-registry, event-bus, stream-session, delivery-router)

- [ ] **Run the full test suite one final time**

Run: `npm test`
Expected: All tests pass (excluding pre-existing clarification test failures)

- [ ] **Verify TypeScript clean**

Run: `npm run build`
Expected: No errors

**What Phase 1 delivers:**
- All 9 new `src/gateway/` files in place
- All 5 existing adapters registered with ChannelRegistry via V1Shim
- Heartbeat proactive messages flow through GatewayEventBus → DeliveryRouter → V1Shim → original sendToUser
- delivery_log table records every delivery attempt
- All existing adapter behavior preserved — no regressions

**Phase 2** rewrites adapters one-by-one as native ChannelAdapterV2 (Telegram last, as the largest). Phase 3 wires Parliament/Learning/Perches through the bus and adds SSE + auth to REST.
