---
id: cli-store
path: src/cli/v2/state/store.ts
subsystem: cli
type: state-store
loc: 64
wired: true
status: mapped
mapped_week: 4
links:
  imports_from:
    - cli-state-slices
  imported_by:
    - cli-app
    - cli-bridge
    - cli-composer
    - cli-commands-registry
---

# src/cli/v2/state/ — Zustand Store + 9 Slices

> **Status:** mapped · **Wiring:** ✅ wired · **Mapped:** 2026-05-16 (squad audit)

## Purpose

Centralised reactive state for TUI v2. Uses **Zustand vanilla store** (no React context dependency at the store layer). Components subscribe via `useUiStore(selector)` hook from `UiStoreProvider`.

**Rule:** Only `reducer.ts:applyToStore()` may mutate state. Direct `uiStore.setState()` is reserved for emergency resets in tests.

## Store Composition

`UiState` is a flat intersection of 9 slice interfaces:

```ts
export interface UiState
  extends TurnsState, ToolsState, ParliamentState, HeartbeatState,
          SessionState, UiSliceState, PaletteState, PanelSliceState, ExitConfirmState {}
```

## Slice Inventory

| Slice | File | Key Fields |
|---|---|---|
| `TurnsState` | `slices/turns.ts` | `turns: Turn[]`, `activeTurnId: string \| null` |
| `ToolsState` | `slices/tools.ts` | `toolCalls: ToolCall[]` |
| `ParliamentState` | `slices/parliament.ts` | `parliamentPhase`, `owlPositions[]`, `debate` |
| `HeartbeatState` | `slices/heartbeat.ts` | `heartbeatMessages: HeartbeatMessage[]` |
| `SessionState` | `slices/session.ts` | `sessionId`, `activeOwlName`, `activeOwlEmoji`, `availableOwls[]` |
| `UiSliceState` | `slices/ui.ts` | `mode`, `generating`, `panelFocus`, `promptQuestion`, `promptChoices`, `promptDefault` |
| `PaletteState` | `slices/palette.ts` | `paletteOpen: boolean`, `paletteQuery: string` |
| `PanelSliceState` | `slices/panel.ts` | `panelStack: PanelItem[]`, `activePanel: PanelItem \| null` |
| `ExitConfirmState` | `slices/exitConfirm.ts` | `exitConfirmOpen: boolean` |

## Key Exported Functions

| Export | Purpose |
|---|---|
| `uiStore` | Zustand store instance |
| `applyToStore(updater)` | Only sanctioned mutation path |
| `getStore()` | Snapshot for tests and command handlers |
| `resetStore()` | Test isolation reset |

## Reducer Architecture

`reducer.ts` subscribes to `globalBridge` and maps each `UiEvent.kind` to the appropriate slice reducer. Pattern:

```ts
bridge.subscribe((event) => {
  applyToStore((state) => sliceReducer(state, event));
});
```

## Cross-references

- [[cli-bridge]] — source of all state mutations (via UiEvents)
- [[cli-composer]] — reads `mode`, `generating`, `panelFocus`, `promptQuestion` via `useUiStore()`
- [[cli-commands-registry]] — handlers read `getStore()` for session/owl context
- [[subsystem-cli]] — subsystem overview
