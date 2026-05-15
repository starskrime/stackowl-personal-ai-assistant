# Cross-Channel Progress Notification System — Design Spec

## Goal

Unify the "working on it" progress/status indication across all channels (TUI, Telegram, future channels) by extracting the existing TUI animation assets into shared infrastructure and introducing a `ProgressNotifier` interface that every channel adapter implements independently.

## Background

The TUI already has a polished system:
- `src/cli/v2/components/spinner.ts` — 100-language `THINKING_MESSAGES`, 6-frame `STACKOWL_SPINNER`, `FADE_COLORS`
- `src/cli/v2/components/ThinkingIndicator.tsx` — Ink component combining spinner + color fade + random language
- Signal path: `turn.started` → Zustand `generating: true` → ThinkingIndicator renders; `turn.committed` → gone

Telegram currently:
- Sends `sendChatAction(chatId, "typing")` at message start and periodically via `onProgress` callbacks
- Has a hardcoded 5-phrase English-only `ACK_MESSAGES` array
- No tool-level status updates

Slack, WebSocket, and future channels have no progress indication at all.

## Decisions

| Question | Decision |
|---|---|
| Telegram progress style | Send multi-language ACK text + continuously refresh `sendChatAction("typing")` |
| Language selection | Random per request from the shared 100-language pool |
| Event granularity | Full event stream: initial indicator + per-tool updates + replaced by final answer |
| Architecture | `ProgressNotifier` interface + `ProgressManager` registry (Approach B) |

---

## Architecture

### Three Layers

**Layer 1 — Shared data** (`src/shared/progress.ts`)

Moves `THINKING_MESSAGES`, `STACKOWL_SPINNER`, `FADE_COLORS` out of the CLI-specific `spinner.ts` into a channel-agnostic module. Adds:
- `pickRandomPhrase(): string` — returns one random entry from `THINKING_MESSAGES`
- `TOOL_STATUS_PHRASES: Record<string, string>` — maps tool names to short display strings (e.g. `"shell" → "🐚 Running command…"`, `"web_fetch" → "🔍 Fetching page…"`, `"read_file" → "📄 Reading file…"`)

`src/cli/v2/components/spinner.ts` re-exports everything from the shared module for backwards compatibility — no existing imports break.

**Layer 2 — Notification contract** (`src/progress/`)

```typescript
// src/progress/types.ts
export interface ProgressNotifier {
  /** Called once when a turn begins. phrase is a random-language "Working on it…" string. */
  start(phrase: string, sessionId: string): Promise<void>;
  /** Called on tool:start / tool:result with a short status string from TOOL_STATUS_PHRASES. */
  update(text: string, sessionId: string): Promise<void>;
  /** Called when the turn is fully complete and the final answer has been sent. */
  stop(sessionId: string): Promise<void>;
}
```

`ProgressManager` (`src/progress/manager.ts`):
- Maintains a `Set<ProgressNotifier>` of registered notifiers
- `register(notifier)` / `unregister(notifier)` called at channel adapter startup/shutdown
- `notifyStart(phrase, sessionId)` — called by channel adapter before dispatching to gateway
- Subscribes to `GatewayEventBus` internally:
  - `tool:start` → fans out `update(TOOL_STATUS_PHRASES[toolName] ?? "Working…", sessionId)` to all notifiers
  - `engine:turn_complete` → fans out `stop(sessionId)` to all notifiers
- Session-scoped routing: `ProgressManager` fans out to ALL registered notifiers on every event; each notifier internally ignores calls for sessions it has no record of (e.g. `TelegramProgressNotifier` looks up `sessionId` in its internal map and no-ops if absent)

**Layer 3 — Channel implementations**

| Channel | `start` | `update` | `stop` |
|---|---|---|---|
| TUI | emit `thinking.phrase` to UiBridge | emit `thinking.tool` to UiBridge | no-op (turn.committed already drives generating: false) |
| Telegram | send ACK message + start typing refresh loop | edit ACK message in-place | delete ACK message + clear timer |
| Slack | add reaction emoji | edit ephemeral status message | remove reaction |
| WebSocket | push `{type:"thinking",phrase}` event | push `{type:"tool",text}` event | push `{type:"done"}` event |

### Signal Flow

```
user message arrives at channel adapter
  └─ adapter calls progressManager.notifyStart(pickRandomPhrase(), sessionId)
       └─ fans out to all registered notifiers → notifier.start(phrase, sessionId)

GatewayEventBus emits tool:start {toolName, sessionId}
  └─ ProgressManager.onToolStart
       └─ fans out → notifier.update(TOOL_STATUS_PHRASES[toolName], sessionId)

GatewayEventBus emits engine:turn_complete {sessionId}
  └─ ProgressManager.onTurnComplete
       └─ fans out → notifier.stop(sessionId)
```

---

## Channel Implementations

### TelegramProgressNotifier

**`start(phrase, sessionId)`**
1. Send ACK message: `await api.sendMessage(chatId, phrase)` — store `messageId` keyed by `sessionId`
2. Start `setInterval` every 4 000 ms calling `api.sendChatAction(chatId, "typing")` — store timer handle keyed by `sessionId`

**`update(text, sessionId)`**
1. Edit the stored ACK message: `api.editMessageText(chatId, messageId, text)`
2. Reset the typing refresh interval

**`stop(sessionId)`**
1. Clear the typing refresh interval
2. Delete the ACK message: `api.deleteMessage(chatId, messageId)` — final answer arrives as the next message, ACK cleans itself up
3. Remove session-keyed state from internal maps

**Session binding**: `start()` does not include `chatId` (it's not in the shared interface). Before `progressManager.notifyStart()` is called, the Telegram adapter calls `telegramNotifier.bindSession(sessionId, chatId)` — a Telegram-specific method that stores the `chatId` keyed by `sessionId`. `start()` then looks it up. `stop()` removes it.

**Concurrency model**: singleton per bot instance with `Map<sessionId, {chatId, messageId, timer}>` — multiple concurrent chats don't interfere.

**Error handling**:
- `editMessageText` failure: log warning, continue — typing indicator still works
- `deleteMessage` failure: log warning, leave ACK message — not worth retrying

Replaces the existing hardcoded English `ACK_MESSAGES` array entirely.

### TuiProgressNotifier

Thin bridge adapter — no visual change to users.

**`start(phrase, sessionId)`**: emits `{kind: "thinking.phrase", sessionId, phrase}` to `UiBridge`. `ThinkingIndicator` reads `thinkingPhrase` from Zustand store and renders it (falls back to its own random pick if store value is absent, so it works standalone).

**`update(text, sessionId)`**: emits `{kind: "thinking.tool", sessionId, text}` to `UiBridge`. Displayed beneath the spinner while a tool runs.

**`stop(sessionId)`**: no-op — `turn.committed` already drives `generating: false` via the existing path.

**New `UiEvent` kinds**: `thinking.phrase` and `thinking.tool` added to the union in `src/cli/v2/events/bridge.ts`. Zustand slice handles both.

### Slack / WebSocket Stubs

Minimal implementations that satisfy the interface — full rendering logic added when those channels are built out. Documented in `src/progress/README.md` so any new channel developer knows exactly what to implement.

---

## File Map

| Action | Path |
|---|---|
| Create | `src/shared/progress.ts` |
| Modify | `src/cli/v2/components/spinner.ts` (re-export only) |
| Create | `src/progress/types.ts` |
| Create | `src/progress/manager.ts` |
| Create | `src/progress/notifiers/tui.ts` |
| Create | `src/progress/notifiers/telegram.ts` |
| Create | `src/progress/notifiers/slack.ts` (stub) |
| Create | `src/progress/notifiers/websocket.ts` (stub) |
| Create | `src/progress/index.ts` (barrel export) |
| Create | `src/progress/README.md` |
| Modify | `src/cli/v2/events/bridge.ts` (new event kinds) |
| Modify | `src/cli/v2/state/slices/ui.ts` (handle new events) |
| Modify | `src/cli/v2/components/ThinkingIndicator.tsx` (read phrase from store) |
| Modify | `src/gateway/adapters/telegram.ts` (register notifier, replace ACK_MESSAGES) |
| Modify | `src/gateway/adapters/cli-v2.ts` (register notifier, call notifyStart) |
| Modify | `src/gateway/core.ts` (expose ProgressManager singleton) |
| Modify | `src/gateway/event-bus.ts` (ensure tool:start carries sessionId) |
| Create | `__tests__/progress/manager.test.ts` |
| Create | `__tests__/progress/notifiers/telegram.test.ts` |
| Create | `__tests__/progress/notifiers/tui.test.ts` |

---

## Phased Implementation

### Phase 1 — Shared Foundation (no user-visible change)

- Create `src/shared/progress.ts` with `THINKING_MESSAGES`, `STACKOWL_SPINNER`, `FADE_COLORS`, `pickRandomPhrase()`, `TOOL_STATUS_PHRASES`
- Re-export from `spinner.ts` for backwards compat
- Create `src/progress/types.ts`: `ProgressNotifier` interface
- Create `src/progress/manager.ts`: `ProgressManager` with register/unregister, notifyStart, event bus subscriptions
- Create `src/progress/index.ts`: barrel export
- Unit tests: manager fans out correctly, session isolation, no cross-talk between sessions

### Phase 2 — Telegram Notifier

- Create `src/progress/notifiers/telegram.ts`: `TelegramProgressNotifier`
- ACK message + typing refresh loop + in-place edits + cleanup on stop
- Replace `ACK_MESSAGES` array with `pickRandomPhrase()`
- Register notifier in `telegram.ts` at startup; call `notifyStart()` where ACK was previously sent
- Tests: mock grammY API, verify send → edit → delete sequence per session

### Phase 3 — TUI Notifier

- Add `thinking.phrase` and `thinking.tool` to `UiEvent` union
- Update Zustand slice to handle both new event kinds
- Update `ThinkingIndicator` to read `thinkingPhrase` from store (random fallback preserved)
- Create `src/progress/notifiers/tui.ts`
- Register in `cli-v2.ts` adapter; call `notifyStart()` where turn begins
- Tests: bridge emits correct events, ThinkingIndicator renders store phrase when set

### Phase 4 — Future Channel Stubs + Documentation

- Create `src/progress/notifiers/slack.ts`: `SlackProgressNotifier` stub
- Create `src/progress/notifiers/websocket.ts`: `WebSocketProgressNotifier` stub
- Write `src/progress/README.md`: interface contract, how to implement a new notifier, registration pattern
- Both stubs log a no-op warning so missing implementations are visible in logs

---

## Testing Strategy

- **Unit**: `ProgressManager` — register/unregister, fan-out, session routing, no cross-talk
- **Unit**: `TelegramProgressNotifier` — mock grammY API, verify start/update/stop lifecycle, timer management, concurrent sessions
- **Unit**: `TuiProgressNotifier` — mock UiBridge, verify correct event kinds emitted
- **Integration**: `ThinkingIndicator` renders store phrase when set, falls back to random when not
- All error paths (editMessageText failure, deleteMessage failure) produce log warnings and do not throw
