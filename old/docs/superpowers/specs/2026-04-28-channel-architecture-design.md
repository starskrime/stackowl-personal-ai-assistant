# Channel Architecture — Option B (Thin Adapter Protocol) Design Spec

**Date:** 2026-04-28
**Approach chosen:** Option B — Thin Adapter Protocol
**Goal:** Replace the current bloated, tightly-coupled channel adapters with a gateway-owned delivery layer: capability-aware routing, shared streaming, shared formatting, and event-bus decoupling — making the assistant fully autonomous across all channels.

---

## Context: Why This Exists

The current channel layer has 7 critical problems:

1. **Telegram streaming race condition** — `done` event fires after `handle()` returns, silently dropping the final message
2. **Memory leaks** — `userState` + `processedUpdates` maps grow unbounded in Telegram
3. **Slack auto-approves tool install** — `askInstall` returns `true` unconditionally
4. **Voice TTS blocks** — `execSync('say ...')` blocks the readline loop during playback
5. **No auth on REST** — `/api/chat`, `/api/parliament`, `/api/broadcast` all public
6. **No shared StreamHandler** — streaming bugs must be fixed in 3 places
7. **No shared MessageFormatter** — formatting rules must be updated in 4 places

Additionally, proactive messages only reach Telegram and Slack — CLI and Voice users get nothing. Adding a new channel (e.g. Discord) requires duplicating streaming, formatting, retry, and proactive-message logic.

---

## Architecture Overview

```
ANY subsystem (Heartbeat, Parliament, LearningEngine, OwlGateway)
       ↓  bus.publish(envelope)
GatewayEventBus          ← single pub/sub hub
       ↓
DeliveryRouter           ← only subscriber; owns retry + TTL + delivery log
       ↓  consults
ChannelRegistry          ← directory: adapters + user presence
       ↓  selects best adapter(s)
ChannelAdapterV2         ← pure transport (150-250 lines per adapter)
       ↓  uses
ChannelRenderer          ← per-channel formatting (testable without bot)
       ↓  uses
StreamSession            ← shared throttled streaming (race condition fixed once)
       ↓
Transport (Telegram API / Slack API / stdout / WebSocket)
```

---

## Section 1: Contracts

### 1.1 ChannelCapabilities

**File:** `src/gateway/channel-capabilities.ts`

Each adapter declares this once on registration. The gateway reads it before every delivery to decide where and how to send.

```typescript
export interface ChannelCapabilities {
  // Identity
  channelId: string       // "cli" | "telegram" | "slack" | "voice" | "web"
  displayName: string

  // Delivery
  streaming: boolean      // can edit message in-place as text arrives
  async: boolean          // can receive push messages not triggered by user
  multiUser: boolean      // multiple users on one channel (Slack workspace)

  // Content
  maxMessageLength: number      // 4096 Telegram, 3000 Slack, Infinity CLI
  formatting: ChannelFormat     // how to render markdown
  supportsButtons: boolean      // inline action buttons
  supportsFiles: boolean        // file attachments
  supportsVoice: boolean        // can speak audio back
  supportsImages: boolean       // can display images
  supportsThreads: boolean      // threaded replies (Slack)
  supportsReactions: boolean    // emoji reactions (Slack)

  // Urgency
  supportsInterrupt: boolean    // can push mid-session (proactive)
  quietHours?: { start: number; end: number }
}

export type ChannelFormat =
  | "html"       // Telegram
  | "mrkdwn"     // Slack
  | "ansi"       // CLI terminal
  | "plain"      // Voice (stripped)
  | "markdown"   // Web/REST (raw markdown)
```

**Capability matrix for current adapters:**

| Capability | CLI | Telegram | Slack | Voice | Web |
|---|---|---|---|---|---|
| streaming | ✓ | ✓ | ✓ | ✓ | WS only |
| async push | ✗ | ✓ | ✓ | ✗ | ✓ |
| buttons | ✗ | ✓ | ✓ | ✗ | ✓ |
| voice | ✗ | input only | ✗ | ✓ | ✗ |
| files | ✗ | ✓ | ✓ | ✗ | ✓ |
| interrupt | ✗ | ✓ | ✓ | ✗ | ✓ |
| format | ansi | html | mrkdwn | plain | markdown |

CLI and Voice have `async: false` — the gateway learns this from the table automatically, no hard-coded special cases.

---

### 1.2 RichContent

**File:** `src/gateway/rich-content.ts`

What the gateway produces. Each channel renderer picks what it can use.

```typescript
export interface RichContent {
  text: string              // always present — the fallback
  markdown?: string         // markdown source (Web / REST)
  voiceText?: string        // stripped, TTS-ready (Voice adapter)
  actions?: RichAction[]    // buttons / quick replies
  files?: RichFile[]
  structured?: unknown      // for JSON-mode / API consumers
  streamable: boolean                  // can this be streamed token-by-token?
  stream?: AsyncIterable<string>       // present when streamable is true; adapters consume this
}

export interface RichAction {
  id: string
  label: string
  style: "primary" | "danger" | "default"
  value: string             // sent back as user input when clicked
}

export interface RichFile {
  name: string
  path: string
  mimeType: string
}
```

---

### 1.3 DeliveryEnvelope

**File:** `src/gateway/delivery-envelope.ts`

Wraps `RichContent` with routing metadata. Every outbound message is an envelope.

```typescript
export interface DeliveryEnvelope {
  userId: string
  channelId?: string        // set = reply to specific channel; unset = router picks

  content: RichContent
  urgency: DeliveryUrgency
  trigger: DeliveryTrigger

  ttlMs?: number            // drop if not delivered within this window (default: no expiry)
  sessionId?: string        // for conversation threading
  envelopeId: string        // uuid — for delivery logging
  createdAt: number         // unix ms
}

export type DeliveryUrgency =
  | "background"    // learning results, parliament done; skip if user offline
  | "normal"        // reply to user request
  | "proactive"     // heartbeat; respect quiet hours
  | "interrupt"     // cost alerts, critical errors; ignore quiet hours

export type DeliveryTrigger =
  | "user-request"
  | "proactive"
  | "background-result"
  | "commitment"
  | "alert"
  | "parliament"
```

---

## Section 2: Gateway Side

### 2.1 ChannelRegistry

**File:** `src/gateway/channel-registry.ts`

Directory of all connected adapters. Tracks user presence per channel. Used by `DeliveryRouter` to pick the right adapter(s).

```typescript
export class ChannelRegistry {
  // Adapter lifecycle
  register(adapter: ChannelAdapterV2): void
  unregister(channelId: string): void
  get(channelId: string): ChannelAdapterV2 | undefined
  listAll(): ChannelAdapterV2[]

  // User presence — adapters call these on message in/out
  markActive(channelId: string, userId: string): void
  markInactive(channelId: string, userId: string): void
  getLastSeen(channelId: string, userId: string): number  // unix ms

  // Routing helpers
  getActiveChannels(userId: string): ChannelAdapterV2[]
  getCapableChannels(
    userId: string,
    requires: Partial<ChannelCapabilities>
  ): ChannelAdapterV2[]
  getBestChannel(
    userId: string,
    urgency: DeliveryUrgency
  ): ChannelAdapterV2 | undefined
}
```

**`getBestChannel` routing logic (priority order):**

1. If `channelId` set on envelope → use it directly (reply path)
2. `interrupt` → first channel with `supportsInterrupt: true`, ignore quiet hours
3. `proactive` → most-recently-active async channel, respect quiet hours
4. `background` → only async channels; skip if none active in last 24h
5. `normal` → channel the user last spoke on (session channel)
6. No capable channel found → envelope dropped, logged (not silently lost)

---

### 2.2 GatewayEventBus

**File:** `src/gateway/event-bus.ts`

Internal pub/sub. Any subsystem that wants to reach a user publishes here. No subsystem needs to know about channels. `DeliveryRouter` is the only delivery subscriber.

```typescript
export class GatewayEventBus {
  // Publish a delivery — any subsystem calls this
  publish(envelope: DeliveryEnvelope): void

  // DeliveryRouter subscribes once at startup
  onDeliver(handler: (env: DeliveryEnvelope) => Promise<void>): void

  // Internal system events (pellets, learning, etc.)
  emit<T extends GatewaySystemEvent>(event: T): void
  on<T extends GatewaySystemEvent["type"]>(
    type: T,
    handler: (e: Extract<GatewaySystemEvent, { type: T }>) => void
  ): void
}

export type GatewaySystemEvent =
  | { type: "pellet:created";    pelletId: string;  userId: string }
  | { type: "learning:complete"; summary: string;   userId: string }
  | { type: "evolution:done";    owlName: string;   changes: string[] }
  | { type: "parliament:done";   topic: string;     verdict: string; userId: string }
  | { type: "perch:event";       source: string;    detail: string;  userId: string }
  | { type: "commitment:due";    text: string;      userId: string }
  | { type: "cost:alert";        spent: number;     budget: number;  userId: string }
```

**How existing subsystems plug in:**

- **Heartbeat** → `bus.publish(envelope { trigger: "proactive" })` — stops knowing about channels
- **Parliament** → `bus.emit({ type: "parliament:done", ... })` — never imports a channel adapter
- **LearningEngine** → `bus.publish(envelope { trigger: "background-result", urgency: "background", ttlMs: 4h })`
- **Perches** → `bus.emit({ type: "perch:event", ... })` → bus sets urgency based on event type
- **CostTracker** → `bus.emit({ type: "cost:alert", ... })` → interrupt urgency, delivered in quiet hours
- **OwlGateway** → `bus.publish(envelope { channelId: req.channelId, trigger: "user-request" })` — same path as all others

Note: The existing `EventBus` in `src/events/index.ts` is for UI-state events (face emitter, tool display). `GatewayEventBus` owns message delivery — different concern, kept separate.

---

### 2.3 DeliveryRouter

**File:** `src/gateway/delivery-router.ts`

The only subscriber to `GatewayEventBus`. Routes envelopes to the right adapter(s), handles retries, enforces TTL, logs every delivery attempt.

```typescript
export class DeliveryRouter {
  constructor(
    private registry: ChannelRegistry,
    private db: Database         // SQLite — for delivery_log table
  ) {}

  start(bus: GatewayEventBus): void  // subscribes to bus.onDeliver

  private async route(envelope: DeliveryEnvelope): Promise<void>
  private async attempt(
    adapter: ChannelAdapterV2,
    envelope: DeliveryEnvelope,
    attempt: number
  ): Promise<void>
}
```

**Retry behaviour:**
- Max 2 retries on transient failure (network timeout, rate limit)
- Backoff: 0ms → 2s → 8s
- Permanent failure (4xx, adapter offline): no retry, log and drop
- TTL check before each attempt: if `envelope.createdAt + ttlMs < now`, drop

**delivery_log table (SQLite):**

```sql
CREATE TABLE delivery_log (
  id          TEXT PRIMARY KEY,
  envelope_id TEXT NOT NULL,
  user_id     TEXT NOT NULL,
  channel_id  TEXT NOT NULL,
  urgency     TEXT NOT NULL,
  trigger     TEXT NOT NULL,
  status      TEXT NOT NULL,   -- "delivered" | "failed" | "dropped_ttl" | "dropped_no_channel"
  attempt     INTEGER NOT NULL,
  error       TEXT,
  delivered_at INTEGER          -- unix ms, null if not delivered
);
```

---

## Section 3: Adapter Side

### 3.1 ChannelAdapterV2

**File:** `src/gateway/adapter-v2.ts` (interface only)

The new thin interface. Pure transport. No formatting, no retry, no streaming logic.

```typescript
export interface ChannelAdapterV2 {
  readonly capabilities: ChannelCapabilities

  start(): Promise<void>
  stop(): Promise<void>
  register(registry: ChannelRegistry): void

  // Outbound: deliver a response or proactive message
  deliver(envelope: DeliveryEnvelope): Promise<void>

  // Interactive: ask user a question and wait for answer
  ask(userId: string, prompt: AskPayload): Promise<string>
}

export interface AskPayload {
  text: string
  choices?: string[]        // shown as buttons if channel supports it
  timeoutMs?: number        // default 30s — auto-decline on timeout
  defaultChoice?: string    // used if timeout fires
}
```

**What each adapter loses vs. today:**

| Removed from adapter | Moved to |
|---|---|
| `sendToUser()` / `broadcast()` | `DeliveryRouter` + `GatewayEventBus` |
| `GatewayCallbacks` (onStreamEvent, onProgress, askInstall) | `StreamSession` + `ask()` |
| Streaming throttle logic | `StreamSession` |
| Markdown→HTML conversion | `ChannelRenderer` |
| Retry logic in `.catch()` blocks | `DeliveryRouter` |
| `ProactivePinger` init | `GatewayEventBus` subscription |
| `userState` / `processedUpdates` maps | `ChannelRegistry.markActive()` |

Telegram goes from 1453 lines → ~250 lines. The adapter only: receives a Telegram update, calls `gateway.handle()`, and sends a message via Telegram API. Everything else is extracted.

---

### 3.2 StreamSession

**File:** `src/gateway/stream-session.ts`

One shared throttled streaming implementation. Fixes the Telegram race condition once. All streaming adapters use this class.

```typescript
export class StreamSession {
  constructor(private opts: {
    throttleMs: number       // 1000 Telegram, 1200 Slack, 0 CLI/Voice/Web
    maxLength: number        // 4096 Telegram, 3000 Slack, Infinity CLI
    onFlush: (text: string) => Promise<void>
    onComplete: (text: string) => Promise<void>
  }) {}

  append(delta: string): void          // called for each LLM token
  complete(): Promise<void>            // always awaited — race eliminated
  abort(err: Error): Promise<void>     // deliver what we have, log error
  get text(): string
}
```

**Internal behaviour:**
- Throttle: calls `onFlush` at most every `throttleMs`
- `complete()`: cancels pending flush, calls `onComplete` with final text
- `complete()` always wins — race condition eliminated (the old bug was `done` event firing after `handle()` returned)
- If `onFlush` throws: swallow, continue accumulating
- If `onComplete` throws: retry once, then log

**Per-channel parameters:**

| Adapter | throttleMs | maxLength |
|---|---|---|
| Telegram | 1000ms | 4096 |
| Slack | 1200ms | 3000 |
| CLI | 0ms (direct) | ∞ |
| Voice | 0ms (stdout) | 800 chars |
| Web WS | 0ms (event) | ∞ |

**Usage pattern in an adapter:**

```typescript
// TelegramAdapterV2.deliver()
const msg = await bot.api.sendMessage(chatId, "...")
const session = new StreamSession({
  throttleMs: 1000,
  maxLength: 4096,
  onFlush: text => bot.api.editMessageText(chatId, msg.id, render(text)),
  onComplete: text => bot.api.editMessageText(chatId, msg.id, render(text)),
})
for await (const delta of envelope.content.stream) {
  session.append(delta)
}
await session.complete()  // always called — race eliminated
```

---

### 3.3 ChannelRenderer

**File:** `src/gateway/renderers/*.ts`

Converts `RichContent` into the platform's native format. One class per channel. Lives outside the adapter — fully testable without a live bot connection.

```typescript
export interface ChannelRenderer {
  render(content: RichContent, caps: ChannelCapabilities): RenderedPayload
  renderStream(delta: string): string   // for streaming incremental updates
}

export interface RenderedPayload {
  text: string           // formatted for platform
  parseMode?: string     // "HTML" for Telegram
  blocks?: unknown[]     // Block Kit for Slack
  keyboard?: unknown     // InlineKeyboard for Telegram
  chunks: string[]       // pre-split at maxLength boundaries
}

// Implementations:
class TelegramRenderer implements ChannelRenderer  // src/gateway/renderers/telegram-renderer.ts
class SlackRenderer    implements ChannelRenderer  // src/gateway/renderers/slack-renderer.ts
class CLIRenderer      implements ChannelRenderer  // src/gateway/renderers/cli-renderer.ts
class VoiceRenderer    implements ChannelRenderer  // src/gateway/renderers/voice-renderer.ts
class WebRenderer      implements ChannelRenderer  // src/gateway/renderers/web-renderer.ts
```

**Per-renderer responsibilities:**

| Renderer | Responsibilities |
|---|---|
| **Telegram** | markdown → HTML escape, tables → formatted text, strip `<thinking>` tags, actions → InlineKeyboard, chunk at 4096 chars, trigger prefix |
| **Slack** | markdown → mrkdwn, split into Block Kit sections, actions → Block Kit buttons, chunk at 3000 chars/block, header + context blocks |
| **CLI** | markdown → ANSI colors, code blocks → syntax highlight, actions → numbered list, tool status inline |
| **Voice** | use `voiceText` if present, strip all markdown, truncate at 800 chars, expand abbreviations, speak action prompts |
| **Web** | pass `RichContent` as-is (JSON), stream deltas as SSE events, structured for API consumers |

Key benefit: formatting is now unit-testable. `new TelegramRenderer().render(content, caps)` returns a plain object. No bot connection needed.

---

### 3.4 ChannelAdapterV1Shim

**File:** `src/gateway/adapter-v1-shim.ts`

Wraps existing v1 adapters so they satisfy `ChannelAdapterV2`. Enables zero-downtime incremental migration — remove one adapter at a time as they are rewritten.

```typescript
class ChannelAdapterV1Shim implements ChannelAdapterV2 {
  constructor(private v1: ChannelAdapter, private caps: ChannelCapabilities) {}
  get capabilities() { return this.caps }
  deliver(env: DeliveryEnvelope) { return this.v1.sendToUser(env.userId, env.content.text) }
  ask(userId, prompt) { return Promise.resolve(prompt.defaultChoice ?? "yes") }
  register(r) { r.register(this) }
  start() { return this.v1.start() }
  stop() { return this.v1.stop() }
}
```

---

## Migration Path

Three phases. Platform stays live throughout. No big-bang rewrite.

### Phase 1 — Foundation (Week 1–2)

1. Create contract files: `channel-capabilities.ts`, `rich-content.ts`, `delivery-envelope.ts`
2. Create `ChannelRegistry`, `GatewayEventBus`, `DeliveryRouter`
3. Create `StreamSession` with full test coverage
4. Wrap all 5 current adapters in `ChannelAdapterV1Shim`
5. Wire gateway to use `bus.publish()` for all replies
6. Add `delivery_log` SQLite table

**Exit criteria:** Platform works with new plumbing active. All existing tests pass.

### Phase 2 — Migrate Adapters (Week 2–3)

1. Rewrite CLI adapter → `ChannelAdapterV2` + `CLIRenderer`
2. Rewrite Slack adapter → `ChannelAdapterV2` + `SlackRenderer` + `StreamSession`
3. Rewrite Telegram adapter (largest) → `ChannelAdapterV2` + `TelegramRenderer` + `StreamSession`
4. Rewrite Voice adapter → `ChannelAdapterV2` + `VoiceRenderer`
5. Rewrite Web/REST adapter → `ChannelAdapterV2` + `WebRenderer` + SSE streaming + API key auth
6. Delete `ChannelAdapterV1Shim` once all 5 migrated

**Exit criteria:** All adapters thin (~150–250 lines each). All renderers have unit tests.

### Phase 3 — Enable New Powers (Week 3–4)

1. Wire Heartbeat → `bus.publish()` (all async channels get proactives, not just Telegram)
2. Wire Parliament → `bus.emit("parliament:done")` (stops importing channel adapters)
3. Wire LearningEngine → `bus.emit("learning:complete")`
4. Wire Perches → `bus.emit("perch:event")`
5. Add API key auth to REST endpoints (`/api/chat`, `/api/parliament`, `/api/broadcast`)
6. REST endpoint gets SSE streaming

**Exit criteria:** Fully autonomous delivery on all channels. Any subsystem can reach the user without knowing which channel they're on.

---

## Files Changed

### New files

| File | Purpose |
|---|---|
| `src/gateway/channel-capabilities.ts` | `ChannelCapabilities` interface + `ChannelFormat` type |
| `src/gateway/rich-content.ts` | `RichContent`, `RichAction`, `RichFile` interfaces |
| `src/gateway/delivery-envelope.ts` | `DeliveryEnvelope`, `DeliveryUrgency`, `DeliveryTrigger` |
| `src/gateway/channel-registry.ts` | `ChannelRegistry` class |
| `src/gateway/event-bus.ts` | `GatewayEventBus` class + `GatewaySystemEvent` union |
| `src/gateway/delivery-router.ts` | `DeliveryRouter` class |
| `src/gateway/stream-session.ts` | `StreamSession` class |
| `src/gateway/adapter-v2.ts` | `ChannelAdapterV2` interface + `AskPayload` |
| `src/gateway/adapter-v1-shim.ts` | `ChannelAdapterV1Shim` (temporary) |
| `src/gateway/renderers/telegram-renderer.ts` | Telegram-specific formatting |
| `src/gateway/renderers/slack-renderer.ts` | Slack-specific formatting |
| `src/gateway/renderers/cli-renderer.ts` | CLI/ANSI formatting |
| `src/gateway/renderers/voice-renderer.ts` | TTS-ready text stripping |
| `src/gateway/renderers/web-renderer.ts` | JSON/SSE passthrough |

### Modified files

| File | Change |
|---|---|
| `src/gateway/adapters/telegram.ts` | Rewritten to `ChannelAdapterV2` (~250 lines) |
| `src/gateway/adapters/slack.ts` | Rewritten to `ChannelAdapterV2` |
| `src/gateway/adapters/cli.ts` | Rewritten to `ChannelAdapterV2` |
| `src/gateway/adapters/voice.ts` | Rewritten to `ChannelAdapterV2` |
| `src/server/index.ts` | Web adapter → `ChannelAdapterV2`, SSE streaming, API key auth |
| `src/gateway/handler.ts` | Publish to `GatewayEventBus` instead of direct callback |
| `src/heartbeat/proactive.ts` | Publish to `GatewayEventBus` instead of direct Telegram/Slack calls |
| `src/parliament/orchestrator.ts` | Emit `parliament:done` event |

### Deleted files / code

- `ChannelAdapterV1Shim` (end of Phase 2)
- `ProactivePinger` wiring in Telegram/Slack adapters
- `GatewayCallbacks` type (replaced by bus + `ask()`)
- `userState` / `processedUpdates` maps in Telegram

---

## Testing Strategy

- `StreamSession`: unit tests with mock `onFlush`/`onComplete` — verify throttle, race fix, abort
- `ChannelRegistry`: unit tests for routing logic (all 6 `getBestChannel` paths)
- `DeliveryRouter`: unit tests for retry backoff, TTL drop, delivery log entries
- Each `ChannelRenderer`: unit tests with `RichContent` fixtures — verify output without bot
- `ChannelAdapterV1Shim`: integration test that v1 adapter still delivers via shim
- End-to-end: gateway → bus → router → adapter for each urgency level

---

## End State

Adding a new channel (e.g. Discord) = 1 adapter (~150 lines) + 1 renderer (~100 lines) + capabilities declaration. Gateway, event bus, delivery router, streaming, formatting — all pre-built.
