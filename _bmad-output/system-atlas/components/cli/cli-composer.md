---
id: cli-composer
path: src/cli/v2/components/Composer.tsx
subsystem: cli
type: component
loc: 244
wired: true
status: mapped
mapped_week: 4
links:
  imports_from:
    - cli-bridge
    - cli-store
    - cli-commands-dispatcher
    - cli-commands-completion
    - cli-input-history
    - cli-input-paste
  imported_by:
    - cli-screen-chat
---

# src/cli/v2/components/Composer.tsx — Composer

> **Status:** mapped · **Wiring:** ✅ wired · **Mapped:** 2026-05-16 (squad audit)

## Purpose

The primary user input component. Renders a bordered input box, handles all keyboard events via Ink's `useInput`, manages tab completion popup, supports inline command prompts (wizard mode), and dispatches messages to the AI or command system.

## Layout

```
╭─────────────────────────────────────────────────╮
│  🦉 OwlName  ❯ your message here▋               │
│  /help · /config · /memory · /mcp · /skills      │ ← completions popup (above)
╰─────────────────────────────────────────────────╯
```

## Props

```ts
interface ComposerProps {
  onSubmit: (text: string) => void;  // AI message submission
  disabled: boolean;                  // Dimmed when generating or panelFocus === "panel"
}
```

## Keyboard Bindings

| Key | Context | Action |
|---|---|---|
| `Enter` | idle | Submit (AI or command or accept completion) |
| `Tab` | idle | Accept selected completion |
| `↑/↓` | idle | History navigation (no popup) or completion selection (popup) |
| `Escape` | idle | Clear input |
| `Escape` / `Ctrl+C` | **generating** | **Emit `cancel.requested` → abort in-flight AI call** |
| `Ctrl+C` / `Ctrl+D` (empty) | idle | Open exit confirm dialog |
| `Ctrl+L` | idle | Dispatch `/clear` |
| `Ctrl+P` | idle | Toggle parliament view |
| `Shift+Tab` | idle | Cycle owl personas |

### Two-hook input architecture

Two separate `useInput` hooks are registered:
- **Cancel hook** (`isActive: generating`): fires only while generating; handles `Escape` and `Ctrl+C` → `globalBridge.emit({ kind: "cancel.requested" })`.
- **Main hook** (`isActive: !disabled`): fires when not generating/disabled; handles all other keys.

Ink broadcasts keypresses to all active hooks simultaneously. Because `isActive` reflects `generating` state, the cancel hook activates precisely when the main hook goes inactive. In Ink's raw mode with `exitOnCtrlC: false`, Ctrl+C arrives as a keypress (`key.ctrl && input === 'c'`) — **not** as SIGINT — so the cancel hook intercepts it before the OS signal handler.

## Completion Popup Logic

- Popup shown when `completions.length > 0 && value !== completions[0].value`
- `useEffect` recomputes completions on every `value` change (debounced by React's scheduler)
- Completion state: `completionIdx` tracked per session; resets to 0 on new completions

## Fix (2026-05-17) — Cancellation

**Ctrl+C now cancels in-flight generation** (commit `179d7df`). Previously the cancel `useInput` hook only checked `key.escape`; `key.ctrl && input === 'c'` was silently ignored, falling through to the main hook which opened the exit dialog instead.

## Bug Fixed (2026-05-16) — B-CLI-00

**Enter handler for subcommand** (line 141):
- **Before:** `setValue(value.replace(/\S+$/, "").trimEnd() + " " + entry.value + " ")`
  - `/config` → replaces full string → `" provider "` (leading space, bare subcommand → sent to AI not command system)
- **After:** `const cmdPart = value.trimEnd().split(/\s+/)[0] ?? ""; setValue(cmdPart + " " + entry.value + " ")`
  - `/config` → `"/config provider "` (correct, slash command dispatched)

## Logging Gap

**B-CLI-05:** Errors in completion (`getCompletions`) and dispatch use `process.stderr.write()`. Should use `log.cli.error()`.

## Cross-references

- [[cli-bridge]] — keyboard shortcuts emit via `globalBridge`
- [[cli-store]] — reads `mode`, `generating`, `panelFocus`, `promptQuestion` via `useUiStore`
- [[cli-commands-dispatcher]] — `dispatcher.dispatch(trimmed)` for slash commands
- [[cli-commands-completion]] — `getCompletions(value, ctx)` for tab popup
- [[cli-input-history]] — `InputHistory` ref for ↑/↓ navigation
- [[subsystem-cli]] — subsystem overview
