# StackOwl UI Refresh — Premium Terminal Aesthetic

**Date:** 2026-04-21
**Status:** IMPLEMENTED
**Owner:** StackOwl CLI

---

## Goal

Refresh StackOwl's CLI UI from "cheap-looking thin borders" to a **premium terminal aesthetic** (VS Code / Warp inspired). The UI should feel like a polished developer tool — solid visual weight, clear hierarchy, comfortable reading.

---

## Problems Being Fixed

| Problem                     | Root Cause                                               | Fix                                      |
| --------------------------- | -------------------------------------------------------- | ---------------------------------------- |
| Frame feels thin/cheap      | Single-char shadow borders (1 space)                     | Double-char shadows (2 spaces)           |
| Hard to read text hierarchy | Only 2 tiers (W vs D), everything competes               | 3-tier hierarchy with brighter secondary |
| Layout cramped              | No visual gap between zones, single-char panel separator | Heavier panel divider, shortcuts inset   |
| Shortcuts bar too dim       | All dim cyan, no visual weight                           | Bold keys, elevated BG, brighter labels  |

---

## Design System

### Color Palette

**Backgrounds (dark glass):**
| Name | RGB | Usage |
|------|-----|-------|
| `TOP_BG` | `rgb(15, 15, 18)` | Topbar background |
| `PANEL_BG` | `rgb(20, 20, 24)` | Main panel background |
| `SHORT_BG` | `rgb(24, 24, 28)` | Shortcuts bar (elevated, new) |
| `FRAME_BG` | `rgb(28, 28, 32)` | Outer frame shadow |

**Text colors:**
| Name | Chalk | Usage |
|------|-------|-------|
| Primary | `W` (white) | Conversation text, main content |
| Secondary | `Wbr` (chalk.gray, new) | Topbar labels, shortcut labels |
| Tertiary | `D` (dim) | Metadata dots, timestamps, dividers |
| Accent | `C` (cyan) | Input prompt `>`, only here |
| Error | `R` (red) | Error messages |
| Success | `G` (green) | Success indicators |

### Typography

- All text: system monospace (terminal default)
- Body: plain white `W`
- Headers: bold white `Wb`
- Metadata: dim `D`
- No italic, no underline — monospace terminal

### Spatial System

- Panel separator: `│` (3 chars: space-pipe-space) — clear left/right split (was single space)
- Shortcuts bar: 2-char inset on each side — feels less squeezed
- Topbar divider: `━` (U+2501 heavy horizontal) — heavier than thin `─`
- Shortcuts bar: 1-char inset padding on both sides
- Topbar divider: `━` (U+2501 heavy horizontal) — heavier than thin `─`

---

## Changes by File

### `src/cli/ui.ts`

#### 1. New Constants (lines ~40-55)

```typescript
// Existing (replace):
const FRAME_V = FRAME_BG("  "); // was FRAME_BG(" ")
const FRAME_H = FRAME_BG("  "); // was FRAME_BG(" ")
const PANEL_V = PANEL_BG(" │ "); // was PANEL_BG(" ")

// New:
const SHORT_BG = chalk.bgBlack.rgb(24, 24, 28);
const Wbr = chalk.white.bright;

// Existing (replace):
const DIV = "━"; // was "─"
```

#### 2. `_buildTopBar()` (lines ~678-686)

- Topbar content: owl name `Wb`, rest in `W`
- Row 3 divider: `TOP_BG(Wbr(DIV.repeat(c)))` instead of `TOP_BG(W(DIV...))`

#### 3. `_buildTopBarContent()` (lines ~689-701)

- Owl name: `Wb(this.owlEmoji + " " + this.owlName)`
- Model/turn/tokens/cost: use `Wbr` for labels, `W` for values

#### 4. `_buildShortcuts()` (lines ~around shortcuts)

- Background: `SHORT_BG` instead of `PANEL_BG`
- Keys: `Wb("[Esc]")` instead of `C("[Esc]")`
- Labels: `Wbr(...)` instead of `D(...)`
- Add 1-char left padding

---

### `src/cli/home.ts`

Same changes to:

- `FRAME_V`, `FRAME_H`, `PANEL_V`, `DIV`, `SHORT_BG` constants
- Shortcuts bar styling
- Any topbar-like elements

---

## Layout Coordinates After Changes

With 2-char frame shadows:

```
Col 1-2:      left frame shadow
Col 3:        content starts (left panel)
...
Col leftW+1:  1-char gap
Col leftW+2:  PANEL_V separator ( │ )
Col leftW+3:  right panel starts
...
Col cols-1:   right frame shadow (2 chars, cols-1 and cols)
Col cols:
```

**Note:** When `FRAME_V` is 2 chars wide, it occupies cols 1 AND 2. This means:

- Content must start at **col 3** (was col 2)
- Right panel content at `leftW + 4` (was `leftW + 3`)
- `this.leftW` calculation may need adjustment to account for the wider left frame

---

## Implementation Tasks

| #   | Task                                                | Status |
| --- | --------------------------------------------------- | ------ |
| 1   | Write spec doc                                      | DONE   |
| 2   | Update constants in ui.ts                           | DONE   |
| 3   | Update `_buildTopBar()` and `_buildTopBarContent()` | DONE   |
| 4   | Update `_buildShortcuts()`                          | DONE   |
| 5   | Apply same changes to home.ts                       | DONE   |
| 6   | Build + test                                        | DONE   |

---

## Files Changed

- `src/cli/ui.ts` — main active session UI
- `src/cli/home.ts` — home/landing screen
