# Terminal UI — Separator & Popup Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two UI bugs in StackOwl's terminal interface: (1) middle panel separator not visible due to column collision, (2) autocomplete popup not showing when typing `/` commands near terminal bottom.

**Architecture:** Two targeted fixes in UI rendering code. No structural changes — simply adjust column positions for separator rendering and add boundary enforcement for popup positioning.

**Tech Stack:** TypeScript, Node.js, ANSI escape codes for terminal rendering

---

## Task 1: Fix Separator Column in Active Session UI

**Files:**

- Modify: `src/cli/ui.ts:726-729`

- [ ] **Step 1: Edit `ui.ts` body loop — shift right content start to `lW + 5`**

Locate the body rendering loop in `_doBuildBody()` around line 726-729:

```typescript
// CURRENT (lines 726-729):
out += ansi.pos(row, 2) + lLn.t + lPad; // left content (col 1 = left frame)
out += ansi.pos(row, lW + 2) + PANEL_V; // panel separator
out += ansi.pos(row, lW + 3) + rLn.t + rPad; // right content  <-- WRONG
```

Change `lW + 3` to `lW + 5`:

```typescript
// UPDATED:
out += ansi.pos(row, 2) + lLn.t + lPad; // left content
out += ansi.pos(row, lW + 2) + PANEL_V; // panel separator ( │ at lW+3)
out += ansi.pos(row, lW + 5) + rLn.t + rPad; // right content starts after separator
```

- [ ] **Step 2: Build to verify no errors**

```bash
npm run build
```

Expected: Build completes with no TypeScript errors.

---

## Task 2: Fix Separator Column in Home Screen

**Files:**

- Modify: `src/cli/home.ts:325-328`

- [ ] **Step 1: Edit `home.ts` body loop — shift right content start to `lW + 5`**

Locate the body rendering loop in `_buildBody()` around line 325-328:

```typescript
// CURRENT (lines 325-328):
out += H.pos(row, 2) + lLn.t + lPad;
out += H.pos(row, lW + 2) + PANEL_V;
out += H.pos(row, lW + 3) + rLn.t + rPad;  <-- WRONG
```

Change `lW + 3` to `lW + 5`:

```typescript
// UPDATED:
out += H.pos(row, 2) + lLn.t + lPad;
out += H.pos(row, lW + 2) + PANEL_V;
out += H.pos(row, lW + 5) + rLn.t + rPad;
```

- [ ] **Step 2: Build to verify no errors**

```bash
npm run build
```

Expected: Build completes with no TypeScript errors.

---

## Task 3: Fix Popup Positioning Boundary in Active Session UI

**Files:**

- Modify: `src/cli/ui.ts:758-767`

- [ ] **Step 1: Edit `_getPopupPosition()` — enforce body row boundary when above**

Current code at line 758-767:

```typescript
private _getPopupPosition(): { startRow: number; above: boolean } {
  const inputRow = this.rows - 3; // input panel's text row
  const popupRows = Math.min(8, this._cmdPopupMatches.length);
  const spaceBelow = this.rows - 1 - (inputRow + 1 + popupRows);
  if (spaceBelow >= 0) {
    return { startRow: inputRow + 1, above: false };
  }
  // Shift up by 2 so our bottom border lands at rows-5, keeping rows-4 free for the input panel top border
  return { startRow: inputRow - 2 - popupRows, above: true };
}
```

Replace with:

```typescript
private _getPopupPosition(): { startRow: number; above: boolean } {
  const inputRow = this.rows - 3; // input panel's text row
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

- [ ] **Step 2: Build to verify no errors**

```bash
npm run build
```

Expected: Build completes with no TypeScript errors.

---

## Task 4: Verify

- [ ] **Step 1: Run full build**

```bash
npm run build
```

- [ ] **Step 2: Run tests**

```bash
npm run test
```

Expected: All tests pass.

---

## Summary

| Task | Change                                | File                    |
| ---- | ------------------------------------- | ----------------------- |
| 1    | `lW + 3` → `lW + 5` for right content | `src/cli/ui.ts:728`     |
| 2    | `lW + 3` → `lW + 5` for right content | `src/cli/home.ts:327`   |
| 3    | Enforce `startRow ≥ 4` when above     | `src/cli/ui.ts:758-767` |
