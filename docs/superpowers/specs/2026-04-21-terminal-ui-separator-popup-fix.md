# Terminal UI — Separator & Popup Fixes

**Date:** 2026-04-21
**Status:** DRAFT
**Owner:** StackOwl CLI

---

## Issue 1: Middle Panel Separator Not Visible

### Root Cause

In both `src/cli/ui.ts` (active session) and `src/cli/home.ts` (home screen), the panel separator `PANEL_V = AMBER(" │ ")` is placed at column `lW + 2`, but the right panel content starts at `lW + 3` — immediately overwriting the `│` character.

```
PANEL_V = " │ "  (3 chars: space, vertical bar, space)
Position of separator start: lW + 2
Position of │ character: lW + 3
Position of right content start: lW + 3 (COLLISION)
```

### Fix

**`src/cli/ui.ts:728`** and **`src/cli/home.ts:327`**:
Change right content start from `lW + 3` → `lW + 5` to preserve the full 3-char separator.

```typescript
// BEFORE (ui.ts:728, home.ts:327):
out += ansi.pos(row, lW + 3) + rLn.t + rPad;

// AFTER:
out += ansi.pos(row, lW + 5) + rLn.t + rPad;
```

This leaves `lW+2` (space), `lW+3` (│), `lW+4` (space) intact for the separator.

---

## Issue 2: Autocomplete Popup Not Showing

### Root Cause

When typing `/` near the bottom of the terminal, `_getPopupPosition()` returns `above: true` with `startRow = inputRow - 2 - popupRows`. The calculation at `ui.ts:766`:

```typescript
return { startRow: inputRow - 2 - popupRows, above: true };
```

With `inputRow = rows - 3` and `popupRows = 8`:

- `startRow = (rows - 3) - 2 - 8 = rows - 13`

If the terminal has ≥30 rows, `rows - 13 ≥ 17` — this is above the body start (row 4), so it should be visible. However, the popup rows may be getting clipped by the frame shadow or not properly cleared.

The popup only renders content rows (lines 782-798 in `_buildCmdPopup`) — it does **not** clear the space it occupies. When rendering above the input, old content remains visible.

### Fix

**`src/cli/ui.ts:769-805`** — `_buildCmdPopup()`:

1. Add a clearing loop before rendering popup rows when `above: true`
2. Ensure `startRow` when above is calculated to keep popup within visible body area (rows 4 through `rows - 5`)

```typescript
private _getPopupPosition(): { startRow: number; above: boolean } {
  const inputRow = this.rows - 3;
  const popupRows = Math.min(8, this._cmdPopupMatches.length);
  const spaceBelow = this.rows - 1 - (inputRow + 1 + popupRows);
  if (spaceBelow >= 0) {
    return { startRow: inputRow + 1, above: false };
  }
  // Render above: keep popup within body rows (4 through rows-5)
  const minStartRow = 4;
  const rawStart = inputRow - 1 - popupRows;
  return { startRow: Math.max(minStartRow, rawStart), above: true };
}
```

---

## Implementation Order

| #   | Task                                                        | Status |
| --- | ----------------------------------------------------------- | ------ |
| 1   | Fix separator positioning in `ui.ts:728` and `home.ts:327`  | DRAFT  |
| 2   | Fix `_getPopupPosition()` boundary check in `ui.ts:758-767` | DRAFT  |
| 3   | Build (`npm run build`)                                     | -      |
| 4   | Test separator visibility in both screens                   | -      |
| 5   | Test popup at bottom of terminal                            | -      |

---

## Files Changed

- `src/cli/ui.ts` — separator column + popup positioning
- `src/cli/home.ts` — separator column
