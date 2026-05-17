---
id: cli-testing-harness
path: src/cli/v2/testing/harness.ts
subsystem: cli
type: test-infrastructure
loc: 7
wired: false
status: mapped
mapped_week: 5
links:
  imports_from:
    - cli-state-store
  imported_by: []
---

# src/cli/v2/testing/harness.ts

> **Status:** mapped · **Wiring:** test-only (not on message path) · **Mapped:** 2026-05-17 (sprint update, commit `d107560`)
> **Mermaid ID:** `CVTH`

## Purpose

Provides a thin wrapper around `ink-testing-library` for TUI v2 component tests. `renderWithStore<P>()` automatically calls `resetStore()` before each render so tests start from a clean, deterministic state, then returns a `RenderResult` snapshot containing `lastFrame`, `unmount`, and `store`. The module also re-exports `resetStore` and `getStore` from the store module so test files have a single import point for all harness utilities.

## Public API

| Export | Type | Purpose |
|---|---|---|
| `renderWithStore<P extends object>(component, props?)` | `function → RenderResult` | Renders an Ink component with a fresh store; resets store before render |
| `RenderResult` | `interface` | `{ lastFrame: () => string; unmount: () => void; store: StoreSnapshot }` |
| `resetStore` | re-export | Resets Zustand store to initial state (imported from store module) |
| `getStore` | re-export | Returns current store state snapshot (imported from store module) |

## Test Infrastructure

Files that rely on or extend this harness:

| File | Description | Tests |
|---|---|---|
| `__tests__/cli/v2/fixtures/store.ts` | `freshStore()` factory — returns a clean store snapshot for assertion helpers | — |
| `__tests__/cli/v2/fixtures/events.ts` | `captureEvents(fn)` event recorder — collects all UiEvents emitted during `fn()` | — |
| `__tests__/cli/v2/store.test.ts` | Store contract tests — slice initialization, action correctness, selector memoization | 14 |
| `__tests__/cli/v2/events.test.ts` | Event bus tests — emit, subscribe, wildcard, and ordering guarantees | 12 |

**Total: 26 tests, all passing.**

## Wiring Status

- **Wired to message path:** no (test-only module)
- **Active callers:** test suite files only
- **External subsystem imports:** cli-state-store

## Cross-references

- [[cli-state-store]] — `resetStore` / `getStore` source
- Subsystem rollup: [[cli]]

