# Progress Notification System

Provides a unified "working on it" progress indication across all channels.

## Interface

```typescript
interface ProgressNotifier {
  start(phrase: string, turnId: string): Promise<void>;
  update(text: string, turnId: string): Promise<void>;
  stop(turnId: string): Promise<void>;
}
```

## Adding a new channel

1. Create `src/progress/notifiers/<channel>.ts` implementing `ProgressNotifier`.
2. In your channel adapter constructor, call:
   ```typescript
   const notifier = new YourProgressNotifier(...);
   gateway.getProgressManager().register(notifier);
   ```
3. Before `gateway.handle()`, call:
   ```typescript
   const turnId = makeSessionId(this.id, String(userId)); // or this._sessionId
   await gateway.getProgressManager().notifyStart(pickRandomPhrase(), turnId);
   ```
4. After `gateway.handle()` resolves (use `finally`), call:
   ```typescript
   await gateway.getProgressManager().notifyStop(turnId);
   ```
5. In your adapter's `stop()` method, call:
   ```typescript
   gateway.getProgressManager().unregister(notifier);
   ```

## turnId must match tool:start events

The `turnId` passed to `notifyStart`/`notifyStop` must match what `tool:start` events emit. The tool registry emits `tool:start` with `turnId: context.engineContext?.sessionId`. Use `makeSessionId(channelId, userId)` as the `turnId` — do NOT use a per-request `uuidv4()`.

## Session isolation

`ProgressManager` fans out ALL events to ALL registered notifiers. Each notifier is responsible for filtering by `turnId` — only acting on sessions it has registered. Unknown `turnId` values are silently ignored.

## Shared data

- `src/shared/progress.ts` — 100-language phrases, tool status map, utilities
- `pickRandomPhrase()` — returns a random language phrase
- `getToolStatusPhrase(toolName)` — returns a short status string for a tool

## Channel implementations

| Channel | File | Status |
|---|---|---|
| TUI (Ink) | `src/progress/notifiers/tui.ts` | ✅ Complete |
| Telegram | `src/progress/notifiers/telegram.ts` | ✅ Complete |
| Slack | `src/progress/notifiers/slack.ts` | 🚧 Stub |
| WebSocket | `src/progress/notifiers/websocket.ts` | 🚧 Stub |
