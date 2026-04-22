# Command Popup Border Style — Spec

**Date:** 2026-04-21
**Status:** DRAFT
**Owner:** StackOwl CLI

---

## Goal

Style the command autocomplete popup to match the input panel's visual treatment — amber `▔`/`▁` borders on all 4 sides, with consistent background and alignment.

---

## Current Style (Input Panel — reference)

```
Position: leftW + 2 (panel separator at leftW + 2)
Width: rW + 2 chars
Borders: AMBER("▔") top, AMBER("▁") bottom
Background: PANEL_BG
Content padding: 1 char on each side
```

```
Row topRow:  ▔▔▔▔▔▔▔▔▔▔  (rW + 2 amber blocks)
Row content: │ content  │  (panel bg, 1-char amber side borders)
Row botRow:  ▁▁▁▁▁▁▁▁▁▁  (rW + 2 amber blocks)
```

---

## New Popup Style

### Structure

```
Row 0 (top border):  ▔▔▔▔▔▔▔▔▔▔  (amber top border, rW chars)
Row 1..N (items):    ▌ item text   ▐  (amber side borders, popup bg)
Row N+1 (bot border): ▁▁▁▁▁▁▁▁▁▁  (amber bottom border, rW chars)
```

### Measurements

- **Popup width:** `rW` chars (matching input panel content width, excluding side padding)
- **Horizontal position:** `leftW + 3` (aligned with input panel content, not border)
- **Top border:** `AMBER("▔".repeat(rW))` at `startRow`
- **Item rows:** `leftW + 3` with `▌` prefix and `▐` suffix (amber sides)
- **Bottom border:** `AMBER("▁".repeat(rW))` at `startRow + popupRows`

### Colors

| Element           | Color                | Background |
| ----------------- | -------------------- | ---------- |
| Top/bottom border | AMBER                | POPUP_BG   |
| Side borders (▌▐) | AMBER                | POPUP_BG   |
| Selected item     | AMBER (fg), amber-bg | —          |
| Unselected item   | W (primary text)     | POPUP_BG   |

### POPUP_BG

```typescript
const POPUP_BG = chalk.bgRgb(28, 28, 52); // deep purple-navy
```

---

## Implementation

### Files

- `src/cli/ui.ts:782-823` — `_buildCmdPopup()`
- `src/cli/home.ts` — `_renderInputBox()` popup section

### Changes

1. Add top border row: `▔`.repeat(rW)
2. Add `▐` suffix to each item row (right side border)
3. Add bottom border row: `▁`.repeat(rW)
4. Update positioning comments

---

## Visual Comparison

**Before (current):**

```
▌ help
▌ status
▌ owls
```

**After (new):**

```
▔▔▔▔▔▔▔▔▔▔▔▔▔
▌ help         ▐
▌ status       ▐
▌ owls         ▐
▁▁▁▁▁▁▁▁▁▁▁▁▁
```

---

## Tasks

- [ ] Update `_buildCmdPopup()` in `ui.ts`
- [ ] Update popup rendering in `home.ts`
- [ ] Build and test both screens
