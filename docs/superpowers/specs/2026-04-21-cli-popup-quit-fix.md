# CLI Popup Positioning & Quit Exit Fix — Spec

**Date:** 2026-04-21
**Status:** IMPLEMENTED
**Owner:** StackOwl CLI

---

## 1. Command Popup: Smart Positioning

### Problem

The command autocomplete popup always renders **below** the input row. If the input is near the bottom of the terminal, the popup overflows the visible area and corrupts the frame.

### Layout Context

```
Terminal rows (r):
  Row 1:       top frame shadow
  Row 2:       topbar
  Row 3:       topbar divider
  Rows 4..r-4: conversation (right panel) — _convRows() = r - 4
  Row r-3:     input row
  Row r-2:     input panel bottom
  Row r-1:     shortcuts
  Row r:       bottom frame shadow

Input row = r - 3
_popupRow (current, below) = _convRows() + 3 = (r - 4) + 3 = r - 1
```

With popup of 8 rows: `r - 1 + 8 = r + 7` — overflows by ~7 rows.

### Solution

**Render below if space, above if not.**

```
popupRows = min(8, _cmdPopupMatches.length)
spaceBelow = (rows - 1) - (inputRow + 1 + popupRows)  // rows between popup bottom and frame

if (spaceBelow >= 0) {
  // Render below input
  startRow = inputRow + 1
} else {
  // Render above input
  startRow = inputRow - 1 - popupRows
}
```

Key behaviors:

- When opening **below**: `startRow = inputRow + 1`, rows extend downward
- When opening **above**: `startRow = inputRow - 1 - popupRows`, rows extend upward — selected item is closest to input
- Clearing loop uses `popupRows + 1` rows starting at `startRow`
- Rendered in `_buildCmdPopup()` for full redraw AND `_renderCmdPopup()` for incremental

### Changes

**`src/cli/ui.ts`**

- Add `_getPopupPosition(): { startRow: number; above: boolean }` helper
- Update `_buildCmdPopup()` to use the helper
- Update `_renderCmdPopup()` to use the helper

---

## 2. Quit: Skip Post-Processing

### Problem

`/quit` or Ctrl+C triggers `_gracefulShutdown()` which calls `gateway.endSession()`. That method runs 7 sequential expensive operations (episodic memory extraction, learning orchestrator, inner life reflection, DNA evolution, knowledge extraction, pattern analysis, micro-learner save). Each `await` can hang indefinitely, causing the app to never exit even after `ui.close()` and `process.exit(0)` are called.

The "Saving session…" message appears but the process stalls.

### Solution

**Skip `endSession()` entirely on explicit quit.** Sessions are already saved periodically (every N messages or on a timer). Explicit quit does not need the full consolidation pipeline — the session data is already persisted.

```
_gracefulShutdown():
  if (_shuttingDown) return
  _shuttingDown = true
  ui.close()
  process.exit(0)   // <— exit immediately, no endSession()
```

All cleanup that matters (session messages already in memory, periodic saves) continues to work via the normal save cycle. The explicit quit path is for the user — they should get immediate exit feedback, not a multi-second consolidation wait.

### Changes

**`src/gateway/adapters/cli.ts`**

- Remove `await this.gateway.endSession(this.sessionId).catch(() => {})` from `_gracefulShutdown()`
- Remove the `printInfo("Saving session…")` call — no longer applicable
- Keep `_shuttingDown` guard

---

## Implementation Order

| #   | Task                              | Status  |
| --- | --------------------------------- | ------- |
| 1   | Implement smart popup positioning | DONE    |
| 2   | Fix quit to skip post-processing  | DONE    |
| 3   | Build (`npm run build`)           | DONE    |
| 4   | Test popup at bottom of terminal  | PENDING |
| 5   | Test /quit exits immediately      | PENDING |

---

## Files Changed

- `src/cli/ui.ts` — popup positioning logic
- `src/gateway/adapters/cli.ts` — remove endSession from \_gracefulShutdown
