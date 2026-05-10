# TUI v2 Redesign — Restoration + Interactive Panels

## Context

After the TUI v2 visual overhaul shipped, the user reported it cut substantial functionality from v1. Concrete gaps verified by reading both code paths:

- **Commands:** v1's slash-command registry exposes 13 commands (with subcommand hierarchies for `/memory`, `/helper`, `/mcp`, `/skills`, `/owl`). v2 hardcodes 7 names in `Composer.tsx` and silently drops everything else.
- **Subcommand autocomplete:** v1's CompletionProvider completes top-level command names AND a static list of subcommands per command. v2 only completes top-level names.
- **Inline output:** v1's commands print results as system messages in the transcript (preserved in scrollback). v2 uses overlays/screens that vanish on dismiss.
- **Panel scroll:** when v2's overlays grow taller than the viewport, the Composer drops below the screen and terminal scrollback gets clobbered (the original bug that triggered this redesign).
- **Shortcuts bar:** v1 displays `ESC Stop / ^P Parliament / ^L Clear / ^C Quit` persistently (`renderer.ts:60–65`). v2 deleted it.
- **Keyboard shortcuts:** v1 has `Ctrl+L` (clear), `Ctrl+D` (quit on empty composer). v2 has neither.

The user's explicit requirements (gathered during brainstorming):
- Inline live panels above the composer — NOT full-screen mode replacements
- Native terminal scrollback for chat history (mouse wheel + scrollbar work) — no alt-screen
- Centralized command registry with subcommand autocomplete
- Panels capture input while open; in-panel actions like `d` delete, `g` get
- Skip the v1 left status panel (intentionally — out of scope)

**Intended outcome:** every v1 command works in v2; commands open inline panels that don't disrupt scrollback; subcommand and dynamic-arg autocomplete work; shortcuts bar restored.

---

## Architecture

```
src/cli/v2/
  commands/
    registry.ts          # all command declarations
    completion.ts        # registry-driven autocomplete (top-level + subcommand + dynamic args)
    handlers/
      memory.ts          # /memory list/search/get/invalidate/stats/history/export
      helper.ts          # /helper list/show/create/rename/delete/design/capabilities
      mcp.ts             # /mcp list/status/add/remove/enable/disable/tools/reconnect/install
      skills.ts          # /skills list/install
      status.ts          # /status
      clear.ts           # /clear (alias /reset)
      capabilities.ts    # /capabilities
      learning.ts        # /learning
      onboarding.ts      # /onboarding
      owl.ts             # /owl status
      help.ts            # /help — auto-built from registry
  panels/
    Panel.tsx            # generic bordered panel shell + scroll + footer
    PanelHost.tsx        # mounts the active panel above Composer
    focusBus.ts          # active-focus manager: composer | panel
  components/
    ShortcutsBar.tsx     # restored bottom hint line (context-aware)
    Composer.tsx         # registry-driven, focus-aware, subcommand + dynamic-arg autocomplete
    Transcript.tsx       # add system-message rendering for command output that bypasses panels
  state/slices/
    panel.ts             # activePanel: { id, props } | null + focus state
    ui.ts                # drop showSkillsOverlay/showMcpOverlay → panel slice owns it
```

**Key principles:**

- **Single source of truth:** the command registry. Composer's popup, autocomplete, dispatcher, and `/help` all read from it. Adding a command means one entry.
- **Reuse existing handlers:** `gateway/commands/memory-router.ts`, `mcp-router.ts`, `owl-router.ts`, `cmdStatus`, etc. are already implemented. v2 handlers are thin wrappers that call them and translate output to a `panel` or `system-message` result.
- **Focus bus, not boolean stack:** one store key `focus: "composer" | "panel"`. Composer disables `useInput` when focus is elsewhere. No more N-overlay booleans.
- **Panel slice replaces overlay flags:** `activePanel: { id, props } | null` instead of `showSkillsOverlay`, `showMcpOverlay`, etc.

---

## Command registry shape

```ts
// src/cli/v2/commands/registry.ts

export type CommandResult =
  | { kind: "panel"; panelId: string; props?: unknown }   // open inline panel
  | { kind: "system-message"; text: string }              // print to transcript
  | { kind: "action" }                                    // side-effect only (e.g. /clear)
  | { kind: "error"; text: string };

export interface CommandContext {
  gateway: Gateway;             // reuses existing handlers
  store: UiStore;
  bridge: GlobalBridge;
}

export type CommandHandler = (ctx: CommandContext, args: string[]) => Promise<CommandResult>;

export interface SubcommandSpec {
  name: string;
  description: string;
  args?: ArgSpec[];
  complete?: (ctx: CommandContext, partial: string) => Promise<string[]>;  // dynamic completion
  handler: CommandHandler;
}

export interface CommandSpec {
  name: string;                 // "/memory"
  aliases?: string[];           // ["/mem"]
  description: string;
  subcommands?: SubcommandSpec[];
  handler?: CommandHandler;     // for commands without subcommands
}

export interface ArgSpec {
  name: string;                 // "<key>"
  description?: string;
}
```

**Notable:**

- **Dynamic arg completion** via `complete` fn. v1 only had static subcommand names. v2 will add live value completion: `/memory get <Tab>` lists actual memory keys, `/mcp remove <Tab>` lists installed servers, `/helper delete <Tab>` lists helpers.
- **Four result kinds.** `panel` opens an inline panel. `system-message` prints into the transcript. `action` is side-effect only. `error` shows an inline error strip.
- **Aliases** baked in: `/exit` and `/bye` → `/quit`, `/?` → `/help`, `/reset` → `/clear`, `/mem` → `/memory`.
- **`/help` is auto-built** by iterating the registry — no hardcoded text to drift.
- **Unknown commands** show an inline error: `unknown command: /foo · type /help`. No more silent fall-through to `gateway.handle()`.

---

## Panel system

```tsx
// src/cli/v2/panels/Panel.tsx
export interface PanelProps {
  title: string;
  color?: string;
  items: PanelItem[];
  actions?: PanelAction[];
  onDismiss: () => void;
  emptyText?: string;
  renderItem?: (item: PanelItem, selected: boolean) => ReactNode;
}

export interface PanelItem {
  id: string;
  label: string;
  meta?: string;      // dim text (e.g. "12 facts", "● connected")
  data?: unknown;
}

export interface PanelAction {
  key: string;        // "d", "g", "return"
  label: string;      // "delete", "get", "open"
  handler: (item: PanelItem) => void | Promise<void>;
  confirm?: string;   // optional "Type 'yes'" prompt before firing
}
```

### Panel layout

```
┌─ /memory list ──────────────────────
│ ❯ user_role.md       12 facts
│   feedback_testing    3 facts
│   project_tui_v2      8 facts
│ ▼ 47 more
├─────────────────────────────────────
│ ↑↓ nav  d delete  g get  Enter open  Esc close
└─────────────────────────────────────
```

### Behavior

- **Single panel at a time.** Opening a new one while another is open closes the previous.
- **Viewport-clamped height** — `maxVisible = max(3, rows - 12)`. Indicators (`▲ N above` / `▼ N more`) when content overflows.
- **Focus capture** — when `activePanel !== null`, Composer's `useInput` is disabled and rendered with `colors.dim`. Panel's `useInput` reads keys.
- **Async-safe action handlers** — show a spinner overlay on the selected row while running; on completion, refresh the list.
- **Confirm flow** — destructive actions replace the action footer with a `Type 'yes' to confirm:` mini-prompt.

### Per-command panel mappings

| Command | Panel | Actions |
|---|---|---|
| `/skills` | yes | Enter: details |
| `/skills install <name>` | yes | install progress + result |
| `/mcp list` | yes | `t` tools, `r` reconnect, `d` remove, Enter: details |
| `/memory list` | yes | `g` get, `d` invalidate, `s` search, Enter: details |
| `/memory search <q>` | yes | Enter: open match |
| `/helper list` | yes | Enter: show, `d` delete, `r` rename |
| `/owls` | yes | Enter: details, `e` evolve |
| `/owl status` | yes | (read-only) |
| `/sessions` | yes | Enter: resume, `d` delete |
| `/help` | yes | Enter: show command details |
| `/status` | yes (read-only) | — |
| `/capabilities` | yes (read-only) | — |
| `/learning` | yes (read-only) | — |
| `/clear` (`/reset`) | no — one-shot action | — |
| `/onboarding` | no — full-screen `OnboardingScreen` (wizard) | — |
| `/skill-wizard` | no — full-screen `SkillWizardScreen` (wizard) | — |
| `/quit` (`/exit`, `/bye`) | no — exits | — |

---

## Composer + autocomplete

State machine for the input:

| Input state | Behavior |
|---|---|
| empty / typing | composer text + history nav with ↑↓ |
| starts with `/` no space | command popup (top-level commands matching prefix, with descriptions) |
| `/cmd ` (matched, no subcmd typed) | subcommand popup (static names + descriptions) |
| `/cmd sub ` (matched + has args + has `complete`) | arg-completion popup (live values from completer) |
| any state, panel open | input disabled (focus on panel) |

### Popup examples

```
typing "/m"
  ┌─────────────────────────────────────
  │ ❯ /memory   View and manage memory
  │   /mcp      Manage MCP servers
  │   /helper   Manage helpers
  └─────────────────────────────────────
  ❯ /m▋

typing "/memory "
  ┌─────────────────────────────────────
  │ ❯ list        List all memory entries
  │   search      Search by query
  │   get         Show one entry's content
  │   invalidate  Delete a memory entry
  │   stats       Memory statistics
  └─────────────────────────────────────
  ❯ /memory ▋

typing "/memory get "  (dynamic completion)
  ┌─────────────────────────────────────
  │ ❯ user_role.md
  │   feedback_testing
  │   project_tui_v2
  └─────────────────────────────────────
  ❯ /memory get ▋
```

### Composer keys

- `Tab` — accept current popup selection
- `↑` / `↓` — navigate popup (popup open) OR history (no popup)
- `Enter` — dispatch command or submit message
- `Esc` — dismiss popup; if no popup + empty input, close active panel

---

## Keyboard shortcuts + ShortcutsBar

| Key | Where | Action |
|---|---|---|
| `Enter` | composer | submit / accept popup |
| `Shift+Enter` | composer | newline |
| `↑` `↓` | composer | popup nav or history |
| `↑` `↓` | panel | scroll list |
| `Tab` | composer (popup) | accept selection |
| `Esc` | composer (popup open) | dismiss popup |
| `Esc` | panel focused | close panel, return focus to composer |
| `Esc` | generating | stop generation |
| `Ctrl+P` | anywhere | toggle Parliament screen |
| `Ctrl+L` | anywhere | clear chat |
| `Ctrl+C` | anywhere | quit |
| `Ctrl+D` | composer empty | quit |
| `PageUp` / `PgDn` | — | NOT wired — terminal scrollback handles it natively |

**PageUp/PgDn note:** v1 wired these because it used alt-screen (which kills terminal scrollback). v2 doesn't use alt-screen — wiring app-level scroll would conflict with terminal-native scroll.

### ShortcutsBar — context-aware

```
composer idle:   ESC stop · ^P parliament · ^L clear · ^C quit
panel focused:   ↑↓ nav · d delete · g get · Enter open · Esc close
generating:      ESC stop generation
popup open:      Tab accept · ↑↓ navigate · Esc dismiss
```

Single dim line below `StatusBar`. Reads content from focus state + active panel's `actions` + UI state.

---

## Final layout

```
┌─ StackOwl • main ─────────────────────────────┐
│ [Transcript — terminal scrollback]            │
│  ❯ user: …                                    │
│  🦉 owl: …                                    │
│                                               │
│ [Active panel, when open]                     │
│  ┌─ /memory list ─────────                    │
│  │ ❯ user_role.md  12 facts                   │
│  │   …                                        │
│  │ ↑↓ d g Enter Esc                           │
│  └────────────────────────                    │
│                                               │
│ [Composer — disabled/dimmed when panel open]  │
│  ┌──────────────────────────                  │
│  │ ❯ ▋                                        │
│  └──────────────────────────                  │
│                                               │
│ [StatusBar]    owl • model • tokens • cost    │
│ [ShortcutsBar] ESC stop · ^P parliament · …   │
└───────────────────────────────────────────────┘
```

---

## Build order (each phase ships independently)

### Phase 1 — Foundation (~2 days)
- `state/slices/panel.ts` (activePanel slice) + `panels/focusBus.ts`
- Generic `panels/Panel.tsx` + `panels/PanelHost.tsx`
- Migrate existing `SkillsOverlay` + `McpOverlay` to new Panel
- Drop `showSkillsOverlay` / `showMcpOverlay` flags from ui slice
- **Verify:** `/skills` and `/mcp` still work via new panel system

### Phase 2 — Command registry + composer rewrite (~2 days)
- `commands/registry.ts` — types + REGISTRY (initially 7 existing commands)
- `commands/completion.ts` — registry-driven autocomplete (top-level + static subcommand)
- Rewrite Composer popup to read from registry; show descriptions
- Wire focus bus — Composer disables when panel focused
- Add unknown-command error path
- **Verify:** existing commands work, popup shows descriptions

### Phase 3 — Restore missing commands (~3 days)
- Add handlers for: `/status`, `/clear`, `/capabilities`, `/learning`, `/memory`, `/helper`, `/owl`, `/onboarding`
- Each handler is a thin wrapper around existing `gateway/commands/*` modules
- Add subcommand completion specs + dynamic completers (`completeMemoryKeys`, `completeMcpServers`, `completeHelpers`)
- Add subcommand popup mode in Composer
- Auto-build `/help` from registry
- **Verify:** every v1 command works; subcommand autocomplete works for static names

### Phase 4 — Interactive panels (~2 days)
- Add `actions` to PanelSpec + key handler dispatch
- Add confirm-flow for destructive actions
- Wire actions for `/memory list` (d/g), `/mcp list` (t/r/d), `/helper list` (d/r), `/owls` (e), `/sessions` (d)
- **Verify:** in-panel actions work; destructive ones prompt for confirm

### Phase 5 — ShortcutsBar + remaining keys (~1 day)
- Restore `ShortcutsBar.tsx` — context-aware
- Wire `Ctrl+L` (clear), `Ctrl+D` (quit on empty)
- **Verify:** shortcuts bar updates with focus changes; all keys work

### Phase 6 — Polish + tests (~1 day)
- Unit tests for registry dispatch + completion
- Integration tests for key panel flows (open `/memory`, scroll, delete with confirm, dismiss)
- Smoke test all commands end-to-end
- Update `CLAUDE.md` / `docs/`

**Total estimate:** ~11 days. Each phase is independently shippable.

---

## Critical files

**Modify:**
- `src/cli/v2/components/Composer.tsx` — registry-driven popup, focus-aware, subcommand + dynamic arg autocomplete
- `src/cli/v2/screens/ChatScreen.tsx` — replace overlay JSX with `<PanelHost />`, add `<ShortcutsBar />`
- `src/cli/v2/state/slices/ui.ts` — drop `showSkillsOverlay`, `showMcpOverlay`; add focus state
- `src/cli/v2/events/bridge.ts` — generic `openPanel(id, props)` / `closePanel()` replaces per-overlay methods
- `src/cli/v2/events/reducer.ts` — handle new panel slice events

**Create:**
- `src/cli/v2/commands/registry.ts`
- `src/cli/v2/commands/completion.ts`
- `src/cli/v2/commands/handlers/` (10 files)
- `src/cli/v2/panels/Panel.tsx`
- `src/cli/v2/panels/PanelHost.tsx`
- `src/cli/v2/panels/focusBus.ts`
- `src/cli/v2/state/slices/panel.ts`
- `src/cli/v2/components/ShortcutsBar.tsx`

**Delete:**
- `src/cli/v2/components/SkillsOverlay.tsx`
- `src/cli/v2/components/McpOverlay.tsx`

## Reusable handlers (already exist — wrap, don't reimplement)

| Module | Used for |
|---|---|
| `src/gateway/commands/memory-router.ts` — `dispatchMemoryCommand` | `/memory *` |
| `src/gateway/commands/mcp-router.ts` — `McpCommandRouter` | `/mcp *` |
| `src/gateway/commands/owl-router.ts` | `/helper *` |
| `src/cli/commands.ts::cmdStatus` | `/status` |
| `src/intelligence/owl-state-reporter.ts` | `/owl status` |
| `gateway.getEvolution()` | `/capabilities` |
| `gateway.getLearningOrchestrator()` | `/learning` |
| `gateway.handle("/reset")` | `/clear` |

---

## Verification

```bash
# Type + lint
npx tsc --noEmit
npm run lint

# Tests
npm test

# Manual smoke
npm run dev
# 1. Type "/" — popup shows all 13 commands with descriptions
# 2. Type "/m" — popup filters to /memory, /mcp, /helper
# 3. Type "/memory " — subcommand popup: list/search/get/invalidate/stats
# 4. Type "/memory get " then Tab — dynamic key list appears
# 5. Run /memory list — panel opens, ↑↓ navigates, d prompts confirm, g shows value
# 6. Run /skills — panel opens, scrolls with arrows, Esc dismisses, Composer regains focus
# 7. Run /status — read-only panel shows provider/model/owl/tokens
# 8. Run /clear — context cleared (transcript shows confirmation)
# 9. Mouse wheel scrolls terminal scrollback while panel is open
# 10. ShortcutsBar updates: composer-idle vs panel-focused vs generating
```

---

## Top risks

1. **Gateway handlers return formatted text, not structured data.** `/status`, `/learning`, `/capabilities` return text strings. v2 wrappers will need to translate into PanelItems (split by line, parse where appropriate). List commands (`/memory list`, `/mcp list`) already return structured objects internally — those wrappers are straightforward.
2. **Focus bus must fully replace the `disabled` boolean stack.** Currently `disabled = generating || showHelp || showSkillsOverlay || showMcpOverlay`. Phase 2 must replace this entirely with the focus bus — leaving any stale flag in place causes double-disable bugs.
3. **Subcommand autocomplete races.** Dynamic completers hit the gateway async. Mitigation: each completion request carries a generation token; stale results are dropped on arrival.
4. **Panel + popup interaction.** If a panel is open and the user types `/`, composer is disabled (panel has focus) so nothing happens. This is correct by design but needs explicit test coverage.

---

## Out of scope

- v1 left status panel (user explicitly skipped)
- v1 right sessions panel
- Mouse-driven panel interaction (keyboard only)
- Virtualized scrolling beyond existing window+slice approach
- Reworking `OnboardingScreen` or `SkillWizardScreen`
- Surfacing `pellets`, `evolve`, `voice`, `telegram`, `slack`, `web`, `all` as slash commands
