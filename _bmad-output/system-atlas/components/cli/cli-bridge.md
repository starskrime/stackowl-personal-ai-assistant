---
id: cli-bridge
path: src/cli/v2/events/bridge.ts
subsystem: cli
type: event-bus
loc: 397
wired: true
status: mapped
mapped_week: 4
links:
  imports_from:
    - providers-base
    - parliament-protocol
    - cli-ui-event
  imported_by:
    - cli-adapter
    - cli-composer
    - cli-commands-registry
    - cli-store-reducer
---

# src/cli/v2/events/bridge.ts — UiBridge / globalBridge

> **Status:** mapped · **Wiring:** ✅ wired · **Mapped:** 2026-05-16 (squad audit)

## Purpose

**The ONE translator.** `UiBridge` is the sole class that may convert engine `StreamEvent`s into `UiEvent`s. No component or handler may import from `src/engine/*` directly — only `globalBridge` (module-level singleton of `UiBridge`) bridges that boundary.

This hard boundary prevents coupling between the engine streaming protocol and the TUI rendering model.

## Public API — UiBridge

| Method | Signature | Purpose |
|---|---|---|
| `subscribe(handler)` | `() => void` | Register event handler; returns unsubscribe |
| `emit(event)` | `void` | Broadcast UiEvent to all handlers |
| `translateStreamEvent(turnId, event, owlMeta, fullText)` | `void` | Map StreamEvent → UiEvent + emit |
| `changeOwl(name, emoji)` | `void` | Emit `owl.changed` |
| `requestParliamentView()` | `void` | Emit `nav.parliament` |
| `dismissParliamentView()` | `void` | Emit `mode.changed` |
| `openPanel(item)` | `void` | Emit `panel.opened` |
| `closePanel()` | `void` | Emit `panel.closed` |
| `popPanel()` | `void` | Emit `panel.popped` |
| `requestPrompt(question, choices?, default?)` | `void` | Emit `prompt.requested` |

## StreamEvent → UiEvent Mapping

| StreamEvent type | UiEvent kind | Notes |
|---|---|---|
| `text_delta` | `token.delta` | Forwarded with `turnId` + `owlMeta` |
| `tool_start` | `tool.requested` | Records start time in `_toolStartTimes` map |
| `tool_args_delta` | *(ignored)* | Args streaming not surfaced in TUI |
| `tool_end` | `tool.completed` | Computes `durationMs` from start time |
| `done` | `turn.committed` | `fullText` param required; emits full content |

## Additional Bridge Methods

Beyond `translateStreamEvent`, bridge exposes convenience emitters for:
- Parliament lifecycle (started, round, finished, owl-position)
- Heartbeat reception/dismissal
- Session changes (owl switched, session ID changed)
- Cost notification
- Notice/error display
- Palette open/close
- Exit confirm open

## Module Export

```ts
export const globalBridge = new UiBridge();
```

Imported everywhere as a module-level singleton. Never reconstructed; tests must call `resetStore()` separately.

## Logging Gap

**B-CLI-05:** Several methods use `process.stderr.write()` for error reporting instead of `log.cli.*` structured logger. Non-compliant with platform 4-point logging standard.

## Cross-references

- [[cli-ui-event]] — `UiEvent` discriminated union (42 kinds)
- [[cli-adapter]] — calls `translateStreamEvent()` on every StreamEvent
- [[cli-store-reducer]] — subscribes to globalBridge and dispatches to store
- [[cli-composer]] — emits keyboard shortcut events via globalBridge
- [[subsystem-cli]] — subsystem overview
