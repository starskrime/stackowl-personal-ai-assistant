# StackOwl TUI v2 — Design System

Single source of truth: `src/cli/v2/theme/tokens.ts`. All design decisions live there; this document explains the intent.

---

## Brand

One owned color: **amber `#F5A623`**.

Used exclusively for:
- Owl avatar name in conversations
- Composer prompt caret (`❯`)
- Parliament owl name in the debate header

Every other color in the UI is a semantic role. Do not use amber for anything else.

---

## Color Roles

| Token | Value | Usage |
|---|---|---|
| `brand` | `#F5A623` | Owl avatar, prompt caret, parliament owl name |
| `brandDim` | `#A07418` | Secondary brand moments (subdued amber) |
| `user` | `green` | `❯ You` author tag, success ticks |
| `success` | `green` | Confirmation / done states |
| `warning` | `yellow` | Escape-to-stop hint, parliament round labels, skills overlay |
| `error` | `red` | Error messages, failed tool calls |
| `heartbeat` | `#A78BFA` | Heartbeat banner AND MCP overlay (both = external / proactive) |
| `accent` | `cyan` | Overlay titles, cursor block |
| `dim` | `gray` | Chrome, dividers, captions |
| `verdict` | `yellow` | Parliament synthesis verdict border + text |

---

## Border Semantics

| Role | Style | Where |
|---|---|---|
| `surface` | `round` | Composer box, all overlays (CommandPalette, Heartbeat, MCP, Skills) |
| `emphasis` | `double` | Parliament verdict only |
| `subdivision` | `single` | Parliament owl columns |
| _(none)_ | no border | Body / message list |

---

## Spacing Scale

| Token | Value | When to use |
|---|---|---|
| `xs` | 0 | Flush / no gap |
| `sm` | 1 | Tight intra-element gaps |
| `md` | 2 | Default padding inside surfaces |
| `lg` | 4 | Section separation, large containers |

---

## Layout

| Token | Value | Meaning |
|---|---|---|
| `maxContentCols` | 100 | Max width for Composer and Frame |
| `gutterX` | 2 | Horizontal padding inside Frame |
| `topBarLines` | 2 | TopBar height (rows + divider) |

---

## When to Add a Token

**Rule:** If you are about to write a hex literal or a named color string directly inside a component, stop. Add a semantic role to `tokens.ts` first, then use that role in the component.

Good: `color={colors.heartbeat}` — the intent is clear, the value is centralized.

Bad: `color="#A78BFA"` — magic string baked into a component; impossible to audit or retheme.
