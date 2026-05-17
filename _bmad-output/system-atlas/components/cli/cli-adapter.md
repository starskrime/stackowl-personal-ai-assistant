---
id: cli-adapter
path: src/gateway/adapters/cli.ts
subsystem: cli
type: adapter
loc: 353
wired: true
status: mapped
mapped_week: 4
links:
  imports_from:
    - gateway-core
    - cli-bridge
    - cli-commands-dispatcher
  imported_by:
    - cli-index
---

# src/gateway/adapters/cli.ts — CliAdapter

> **Status:** mapped · **Wiring:** ✅ wired · **Mapped:** 2026-05-16 (squad audit)
> **Renamed from:** `cli-v2.ts` / `CliV2Adapter` — 2026-05-16

## Purpose

Primary channel adapter connecting the Ink TUI to `OwlGateway`. Implements `ChannelAdapter` and is responsible for:

1. Owning the stdin/Composer submit flow
2. Translating gateway `StreamEvent`s into `UiEvent`s via `globalBridge`
3. Tracking active turn state for correct tool-call attribution
4. Providing `capabilities()` that signal `tuiV2: true` to heartbeat and parliament

## Public API

| Method | Signature | Purpose |
|---|---|---|
| `submitMessage(text)` | `Promise<void>` | User message → gateway pipeline |
| `capabilities()` | `ChannelCapabilities` | Feature flags (tuiV2, supportsStreaming, etc.) |
| `start()` | `Promise<void>` | Begin listening (waits for quit signal) |
| `stop()` | `void` | Resolve quit promise → unblocks start() |
| `emit(event, owlMeta?)` | `void` | ChannelAdapter contract — routes StreamEvents |
| `setPinger(pinger)` | `void` | Stores `ProactivePinger` reference for engagement recording |

## submitMessage — 9-Step Flow

```
1. TTY guard — discard if generating (concurrent protection)
2. Mint traceId via uuidv4()
3. Build message via makeMessage("cli", userId, text)
4. Set _currentTurnId (links subsequent tool events to turn)
5. Emit turn.started → globalBridge
6. runWithContext(traceId) → OwlGateway.handle(message)
7. StreamEvents arrive via emit() → UiBridge.translateStreamEvent()
8. On done: emit turn.committed with accumulated text
9. Clear _currentTurnId; update sessionId from response
```

## Capabilities

```ts
{
  supportsStreaming: true,
  supportsMarkdown: true,
  supportsToolDisplay: true,
  supportsParliament: true,
  supportsHeartbeat: false,   // heartbeat cannot target TUI sessions
  tuiV2: true,
}
```

## Wiring Status

### B-CLI-01 — FIXED (commits `1e0b64f` `53ed67f` `09ad94b`)

`setPinger(pinger: ProactivePinger): void` is now implemented. In `submitMessage()`, if `_pinger` is set and the incoming message matches the last heartbeat delivery, `_pinger.recordEngagement()` is called. A `memory:written` event listener forwarded to `globalBridge.emit({ kind: "memory.written", ... })` was also added.

#### `_stopped` guard pattern

`GatewayEventBus` has no `offDeliver` API, so listeners cannot be formally deregistered. A `_stopped: boolean` field is set to `true` in `stop()` and gates both the `onDeliver` listener and the `memory:written` listener. This prevents post-stop state mutation and memory leaks without requiring an API that does not exist.

## Known Gaps

- `stop()` resolves the quit promise but does not tear down Ink render (handled by `index.ts`)

## Cross-references

- [[gateway-core]] — `OwlGateway.handle()` called in submitMessage
- [[cli-bridge]] — `globalBridge.translateStreamEvent()` + `emit()`
- [[cli-index]] — constructs `CliAdapter` and calls `start()`
- [[subsystem-cli]] — subsystem overview
