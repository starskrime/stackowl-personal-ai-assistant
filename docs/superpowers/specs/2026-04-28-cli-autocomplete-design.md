# CLI Autocomplete — Design Spec

## Goal

Fix two bugs in the terminal CLI autocomplete and make subcommand completion extensible.

**Bug 1:** Typing `/spez` (no match) closes the popup. Deleting `z` does not reopen it — you must retype `/` to start over.

**Bug 2:** Typing `/skills ` (with a space) should show available subcommands (`list`, `install`). Currently the popup closes because no top-level command name starts with `"skills "`.

---

## Root Causes

**Bug 1** — `_updateMatches()` in `InputHandler` contains:
```
if (this._cmdMatches.length === 0) this._cmdPopupActive = false;
```
Once the popup is closed, control returns to `_handleNormalKey`. That path never re-activates the popup on backspace — it only re-activates on a fresh `/` keystroke.

**Bug 2** — The completion system holds a flat `string[]` of top-level command names. There is no concept of subcommands. A space after a command name produces zero matches and closes the popup.

---

## Architecture

### New unit: `CompletionEngine`

`src/cli/completion-engine.ts` — pure class, no I/O, no state beyond the injected provider.

```
CompletionProvider (interface)
  topLevelNames(): string[]
  subcommands(commandName: string): string[]

CompletionResult (interface)
  items: string[]
  mode: "command" | "subcommand"

CompletionEngine
  constructor(provider: CompletionProvider)
  complete(buf: string): CompletionResult
```

`complete(buf)` logic:
- `buf` does not start with `/` → `{ items: [], mode: "command" }`
- No space after `/` → prefix-filter `provider.topLevelNames()` by `buf.slice(1)`
- Space present → extract command name, return `provider.subcommands(commandName)`

This is a pure function over `buf`. It is independently unit-testable.

### Modified: `CommandRegistry`

- `CommandDef` gains `subcommands?: string[]`
- `CommandRegistry` implements `CompletionProvider`
- `topLevelNames()` returns `Object.keys(COMMANDS)`
- `subcommands(name)` returns `COMMANDS[name]?.subcommands ?? []`
- Two commands declare subcommands:
  - `specialization`: `["list", "show", "create", "delete", "update"]`
  - `skills`: `["list", "install"]`

`skills` subcommands are declared here for completion only — execution still falls through to `gateway.handle()` unchanged.

### Modified: `InputHandler`

**Removed:**
- `_cmdNames: string[]`
- `_cmdMatches: string[]`
- `_cmdPopupActive: boolean` (stored flag)
- `_updateMatches()`
- `_handlePopupKey()`
- `setCommandList(names: string[])`

**Added:**
- `_engine: CompletionEngine | null = null`
- `_completion: CompletionResult = { items: [], mode: "command" }`
- `_cmdIdx: number` (kept — tracks selection within current items)
- `setCompletionEngine(engine: CompletionEngine): void`
- `_refreshCompletion(): void` — calls `_engine.complete(this._buf)`, stores result, resets `_cmdIdx = 0`

**Changed getters:**
- `cmdPopupActive`: derived — `this._completion.items.length > 0 && this._buf.startsWith("/")`
- `cmdMatches`: returns `[...this._completion.items]`

**Unified key handler** — the `_handleNormalKey` / `_handlePopupKey` split is eliminated. Single `_handleKey(data)`:

| Key | Behaviour |
|-----|-----------|
| Backspace | Delete char from buf, call `_refreshCompletion()` — popup re-appears if buf starts with `/` and matches exist |
| Printable char | Insert into buf, call `_refreshCompletion()` |
| `↑` | If popup active: `_cmdIdx--`. Else: history navigation |
| `↓` | If popup active: `_cmdIdx++`. Else: history navigation |
| Enter | If popup active and item highlighted: apply selection to buf, close popup (reset completion). Else: submit line |
| ESC | If popup active: clear buf, reset completion. Else: no-op |
| `/` | Insert `/` into buf (no special case needed — `_refreshCompletion()` will open popup) |

Note: `/` no longer special-cases `this._buf = "/"`. It inserts normally. If the user is mid-sentence and types `/`, no popup opens (buf doesn't start with `/`). This is correct.

**Apply selection behaviour:**
- Mode `"command"`: buf becomes `"/" + selected + " "` (trailing space triggers subcommand mode on next refresh)
- Mode `"subcommand"`: buf becomes `"/" + cmdName + " " + selected + " "`

### Modified: `TerminalRenderer`

- `setCommandList(names: string[])` replaced by `setCompletionEngine(engine: CompletionEngine)`
- Delegates to `this.input.setCompletionEngine(engine)`

### Modified: `cli.ts` (wire-up)

```typescript
const engine = new CompletionEngine(this.commands);
this.renderer.setCompletionEngine(engine);
```

`CmdPopup` component (`cmd-popup.ts`) is unchanged — it already takes `{ matches, selectedIdx }`.

---

## File Map

| File | Action |
|------|--------|
| `src/cli/completion-engine.ts` | Create |
| `src/cli/commands.ts` | Add `subcommands` to `CommandDef`; implement `CompletionProvider` |
| `src/cli/input-handler.ts` | Remove dual-path; add `CompletionEngine` delegation |
| `src/cli/renderer.ts` | `setCommandList` → `setCompletionEngine` |
| `src/gateway/adapters/cli.ts` | Wire `CompletionEngine` |
| `__tests__/cli/completion-engine.test.ts` | Create — unit tests for `CompletionEngine` |

---

## What Does NOT Change

- `CmdPopup` rendering component — unchanged
- `/skills` gateway routing — unchanged
- History navigation — unchanged
- Paste stripping, bracketed-paste handling — unchanged
- Parliament shortcut (`^P`), clear (`^L`) — unchanged

---

## Testing Strategy

`CompletionEngine` is pure and fully unit-testable without a terminal:

- `complete("/")` → all top-level names, mode `"command"`
- `complete("/spe")` → commands starting with `"spe"`, mode `"command"`
- `complete("/spez")` → `[]`, mode `"command"` (no match — but engine returns empty, InputHandler shows empty popup, popup hides via derived getter — not a crash)
- `complete("/skills ")` → `["list", "install"]`, mode `"subcommand"`
- `complete("/specialization s")` → `["show"]` (subcommands prefix-filtered by `"s"`)
- `complete("hello")` → `[]`, mode `"command"`

**Subcommand partial filtering:** When mode is `"subcommand"`, `complete()` also extracts the text after the space and prefix-filters the subcommand list. `/specialization s` → inner = `"specialization s"`, spaceIdx = 14, cmdName = `"specialization"`, partial = `"s"` → filter `["list","show","create","delete","update"]` by `startsWith("s")` → `["show"]`. Consistent with top-level behaviour.

---

## Edge Cases

| Case | Behaviour |
|------|-----------|
| `/spez` — no top-level match | Engine returns `[]`; derived `cmdPopupActive = false`; no popup shown |
| Backspace from `/spez` to `/spe` | `_refreshCompletion()` runs; if matches exist popup reappears |
| `/skills ` — unknown command | Engine looks up `subcommands("skills")` → `["list", "install"]` |
| `/unknown ` — command not in registry | `subcommands("unknown")` → `[]`; no popup |
| Empty buf | `cmdPopupActive = false` |
| User types mid-sentence then `/` | buf = `"hello /"` — doesn't start with `/`; no popup |
