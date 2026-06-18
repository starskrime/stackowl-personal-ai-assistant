# TUI v2 Visual Redesign Spec

**Date:** 2026-05-10  
**Status:** Approved  
**Approach:** Incremental component polish (A) — update each component in-place against this spec

---

## Problem

The existing TUI v2 components are architecturally correct (Ink + Zustand, three-chokepoint design) but visually bare: no indentation hierarchy, generic spinners, no input affordance, all text rendered at the same visual weight. The result looks unfinished compared to Claude Code and other quality CLI tools.

---

## Design Goal

A calm, high-contrast terminal UI with clear visual hierarchy: distinct user vs. owl authorship, readable tool call progression, a clearly afforded input zone, and brand identity through the StackOwl spinner and amber accent.

---

## Color Palette

| Role | Value | Usage |
|------|-------|-------|
| Brand amber | `#F5A623` | StackOwl spinner, owl name accent |
| Heartbeat purple | `#A78BFA` | HeartbeatBanner border + header |
| User green | `#22c55e` | `❯ You` prefix, OwlAvatar user |
| Tool cyan | `#06b6d4` | Spinner during tool call, OwlAvatar name |
| Success green | `#22c55e` | `✓` on completed tool |
| Error red | `#ef4444` | `✗` on failed tool |
| Dim | terminal dimColor | Metadata, timestamps, roles, connectors |
| Normal border | gray | Input box default |
| Plan border | `#06b6d4` (cyan) | Input box in `/plan` mode |
| Shell border | `#22c55e` (green) | Input box in shell mode |

---

## Component Specs

### OwlAvatar

Renders the author chip for an owl turn.

```
🦉 Hoots  strategist
```

- Emoji + space + **bold amber name** + dimColor role (if present)
- Used as the first line of every owl turn in `Transcript` and `LiveTurn`
- No border, no box — inline text only

### Transcript (committed turns)

Each turn is a 2-line block: header + indented content.

**User turn:**
```
❯ You
  deploy the staging worker
```
- `❯ ` in bold green, `You` in bold green
- Content indented 2 spaces, `wrap="wrap"`

**Owl turn:**
```
🦉 Hoots  strategist
  Staging deployed. Worker live at staging.api.example.com
```
- OwlAvatar chip as header (bold amber name)
- Content indented 2 spaces, `wrap="wrap"`

**Tool calls** appear inside owl turns, before the text response, via `ToolCallCard`.

**Heartbeat turns** render via `HeartbeatBanner`, not inline in Transcript.

### ToolCallCard

Three progressive states, each on one line, indented 2 spaces within the owl turn.

**Running:**
```
  ⠙ bash  npm run deploy:staging  1.2s
```
- Amber spinner glyph (from StackOwl spinner set, see below)
- Bold tool name + dimColor args + dimColor elapsed

**Done:**
```
  └ bash  ✓  4.1s
```
- `└ ` dimColor + tool name dimColor + `✓` green + elapsed dimColor

**Failed:**
```
  └ bash  ✗  permission denied
```
- `└ ` dimColor + tool name dimColor + `✗` red + error text dimColor

Long `args` or `output` are truncated at 80 chars in-state (full output goes to disk).

### LiveTurn

Same structure as a committed owl turn in Transcript. Streams token deltas into the text region. Tool calls render as ToolCallCards above the streaming text. When turn commits, `<Static>` takes over.

### HeartbeatBanner

Bordered card with purple left identity stripe.

```
╭─ 🔔 Sage  unsolicited ─────────────────╮
│  Don't forget the deploy window closes │
│  at 5pm today.                         │
╰────────────────────────────────────────╯
```

- `borderStyle="round"`, `borderColor="#A78BFA"` (purple)
- Header: `🔔 ` + bold purple owl name + dimColor `  unsolicited`
- Content indented 1 inside the box
- Visually distinct from all solicited replies — never inline

### Composer (input box)

**Idle state:**
```
╭────────────────────────────────────────╮
│  ❯ type a message▋                     │
│  /help · /owls · /sessions · /skills   │
│  Hoots · sonnet-4-6 · esc esc to stop  │
╰────────────────────────────────────────╯
```

- Full-width box, `borderStyle="round"`
- Border color: gray (normal) / cyan (plan mode) / green (shell mode)
- Row 1: `❯ ` bold green + input text + cyan cursor `▋`
- Row 2 (when empty): dimColor slash command hint row
- Row 2 (when typing with slash): dimColor autocomplete hint
- Footer row: `owlName · modelShort · [tokens · $cost] · esc esc to stop`
  - Tokens and cost omitted when zero (first turn)
  - `esc esc to stop` in yellow when generating

**Generating state (input locked):**
```
╭────────────────────────────────────────╮
│  ✳ generating...                       │
│  Hoots · sonnet-4-6 · 1,234 tok · $0.  │
╰────────────────────────────────────────╯
```

- Row 1: amber spinner glyph + dimColor `generating...`
- Row 2: footer with live token count updating
- Input is `disabled`, no cursor shown

### StackOwl Spinner

6-frame sequence: `·`, `◌`, `◍`, `◉`, `✳`, `✶`  
Color: amber `#F5A623`  
Interval: 80ms  
Used in: ToolCallCard (running), Composer (generating), ParliamentScreen (owl thinking)

### ShortcutsBar

The footer row inside the Composer box (not a separate component):

```
Hoots · sonnet-4-6 · 1,234 tok · $0.0023 · esc esc to stop
```

- All dimColor except `esc esc to stop` which is yellow when generating
- Tokens + cost hidden when both are zero

### ParliamentScreen

Multi-column alt-screen modal during debates.

```
⚖  Parliament  Round 1 of 3  ·  Initial Positions
────────────────────────────────────────────────────
┌─ 🦉 Hoots ──────────┐  ┌─ 🦅 Sage ────────────┐
│ ✳ thinking...       │  │ ✳ thinking...        │
│                     │  │                      │
│ Preparing position  │  │ Preparing position   │
└─────────────────────┘  └──────────────────────┘
────────────────────────────────────────────────────
Ctrl+P — return to chat  ·  Round 1 in progress...
```

- Header: `⚖  Parliament` bold cyan + round info dimColor + phase label yellow
- Columns: `borderStyle="single"`, cyan when position ready, gray while thinking
- Amber spinner in column while thinking, green `[ready]` badge when done
- SynthesisPanel: `borderStyle="double"`, yellow border, `⚖  Parliament Verdict` bold yellow
- Auto-dismisses 3s after synthesis, returns to chat

### SessionsScreen

```
🗂  Recent Sessions   ↑↓ navigate · Enter resume · Esc cancel
────────────────────────────────────────────────────────────
❯  My last conversation                              2m ago
   Another session                                   1h ago  [current]
   Old topic                                         3d ago
────────────────────────────────────────────────────────────
3 sessions
```

- `❯` selector in cyan on selected row
- `[current]` tag in dimColor green
- Relative timestamps dimColor right-aligned

---

## NoticeStrip (instinct/skill firing pill)

One dimColor line above an owl response when an instinct or perch contributed:

```
  · instinct:focus-mode  perch:code-context
```

Dim, no border — visible when relevant, invisible otherwise.

---

## Separator

A single `─` divider (full width, dimColor) separates the transcript from the Composer. No other persistent chrome.

---

## Implementation Approach

**Incremental component polish** — update each file in-place, no structural changes to state/events/bridge/io layers.

Files to update:
1. `src/cli/v2/components/OwlAvatar.tsx` — amber name color
2. `src/cli/v2/components/Transcript.tsx` — `❯ You` header, 2-space indent, OwlAvatar integration
3. `src/cli/v2/components/ToolCallCard.tsx` — StackOwl spinner, `└` connector, 3 states
4. `src/cli/v2/components/Composer.tsx` — bordered box, mode border color, generating state, hint rows, footer inside
5. `src/cli/v2/components/ShortcutsBar.tsx` — move inside Composer box
6. `src/cli/v2/components/HeartbeatBanner.tsx` — purple round border, `🔔` header
7. `src/cli/v2/components/NoticeStrip.tsx` — dim pill (new component if not present)
8. `src/cli/v2/screens/ParliamentScreen.tsx` — amber spinner, phase labels, column border colors
9. `src/cli/v2/screens/SessionsScreen.tsx` — `❯` selector, relative times, `[current]` tag
10. `src/cli/v2/screens/ChatScreen.tsx` — divider above Composer, vertical layout

New shared constant:
- `src/cli/v2/components/spinner.ts` — `STACKOWL_SPINNER = ["·","◌","◍","◉","✳","✶"]` + amber color constant

---

## Verification

```bash
STACKOWL_TUI=v2 npm run dev
# - User turn: ❯ You bold green, content 2-space indented
# - Owl turn: emoji + bold amber name + dim role, content 2-space indented
# - Tool call: amber spinner while running, └ ✓ when done, └ ✗ when failed
# - Composer: bordered box, ❯ cursor, hint row at rest, footer inside
# - Generating: spinner replaces input, footer updates with live tokens
# - Heartbeat: purple bordered card, never inline
# - Parliament: multi-column, amber spinners, synthesis in double border
# - Sessions: ❯ selector, [current] tag
npm run test   # no regressions
npm run lint   # clean
```
