# TUI v2 Restoration + Interactive Panels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore v1 command parity in TUI v2 with a centralized CommandRegistry, generic inline Panel system with modal focus and in-panel actions, three-level autocomplete (command → subcommand → dynamic args), and a context-aware ShortcutsBar.

**Architecture:** A `CommandDispatcher` (created in `CliV2Adapter`, provided via React context) intercepts all `/cmd` inputs in Composer and routes them through a typed `REGISTRY`. Commands return a `CommandResult` that either opens an inline `Panel` above the Composer (with focus captured) or prints a system message into the Transcript. A `PanelSliceState` in the Zustand store tracks the single active panel and focus state.

**Tech Stack:** Ink v6, React, Zustand vanilla store, TypeScript ESM (`.js` extensions on all imports), Vitest

**Spec:** `docs/superpowers/specs/2026-05-10-tui-v2-redesign.md`

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `src/cli/v2/state/slices/panel.ts` | Zustand slice: `activePanel` + `focus` state + `applyPanelEvent()` |
| `src/cli/v2/panels/Panel.tsx` | Generic bordered panel: items list, scroll, action footer |
| `src/cli/v2/panels/PanelHost.tsx` | Reads `activePanel` from store, renders `<Panel>` above Composer |
| `src/cli/v2/commands/registry.ts` | `CommandSpec` types + `REGISTRY` array + `dispatchCommand()` |
| `src/cli/v2/commands/completion.ts` | `getCompletions(input)` → top-level, subcommand, or dynamic arg completions |
| `src/cli/v2/commands/dispatcher.ts` | `CommandDispatcher` interface + `createCommandDispatcher()` factory |
| `src/cli/v2/providers/CommandDispatcherProvider.tsx` | React context providing `CommandDispatcher` to the tree |
| `src/cli/v2/commands/handlers/memory.ts` | `/memory *` — wraps `dispatchMemoryCommand` from `gateway/commands/memory-router.ts` |
| `src/cli/v2/commands/handlers/mcp.ts` | `/mcp *` — wraps `McpCommandRouter.dispatch` from `gateway/commands/mcp-router.ts` |
| `src/cli/v2/commands/handlers/skills.ts` | `/skills` — reads `installedSkills` from store |
| `src/cli/v2/commands/handlers/status.ts` | `/status` — reads store + gateway config |
| `src/cli/v2/commands/handlers/clear.ts` | `/clear` — calls `gateway.handle({ text: "/reset", ... })` |
| `src/cli/v2/commands/handlers/capabilities.ts` | `/capabilities` — calls `gateway.getEvolution()` |
| `src/cli/v2/commands/handlers/learning.ts` | `/learning` — calls `gateway.getLearningOrchestrator()` |
| `src/cli/v2/commands/handlers/onboarding.ts` | `/onboarding` — emits `onboarding.view.requested` via bridge |
| `src/cli/v2/commands/handlers/owl.ts` | `/owl status` — reads store |
| `src/cli/v2/components/ShortcutsBar.tsx` | Single dim hint line below StatusBar, context-aware |

### Modified files
| File | Changes |
|---|---|
| `src/cli/v2/events/UiEvent.ts` | Add `PanelOpenedEvent`, `PanelClosedEvent`, `OnboardingViewRequestedEvent` to union |
| `src/cli/v2/events/bridge.ts` | Add `openPanel()`, `closePanel()`, `requestOnboardingView()`, `dismissOnboardingView()` |
| `src/cli/v2/events/reducer.ts` | Import + call `applyPanelEvent()` |
| `src/cli/v2/state/store.ts` | Add `PanelSliceState` to `UiState` interface + `initialState` |
| `src/cli/v2/state/slices/ui.ts` | Remove `showSkillsOverlay`, `showMcpOverlay` fields + their reducer cases |
| `src/cli/v2/components/Composer.tsx` | Use registry for all `/` dispatch; add subcommand popup; wire focus bus; add Ctrl+L/Ctrl+D |
| `src/cli/v2/screens/ChatScreen.tsx` | Add `<PanelHost />`, `<ShortcutsBar />`; remove `<SkillsOverlay>`, `<McpOverlay>` |
| `src/cli/v2/app.tsx` | Accept + provide `CommandDispatcher` via context; add onboarding mode routing |
| `src/gateway/adapters/cli-v2.ts` | Add `dispatchCommand()` method; create and expose dispatcher |

### Deleted files
| File | Replaced by |
|---|---|
| `src/cli/v2/components/SkillsOverlay.tsx` | `panels/PanelHost.tsx` + `commands/handlers/skills.ts` |
| `src/cli/v2/components/McpOverlay.tsx` | `panels/PanelHost.tsx` + `commands/handlers/mcp.ts` |

### Test files
| File | What it tests |
|---|---|
| `__tests__/cli/v2/state/panel.test.ts` | `applyPanelEvent()` reducer |
| `__tests__/cli/v2/commands/registry.test.ts` | `dispatchCommand()` — known, unknown, aliases |
| `__tests__/cli/v2/commands/completion.test.ts` | All three completion modes |

---

## Phase 1 — Panel Foundation

### Task 1: Panel slice — state + reducer

**Files:**
- Create: `src/cli/v2/state/slices/panel.ts`
- Modify: `src/cli/v2/events/UiEvent.ts`
- Modify: `src/cli/v2/events/bridge.ts`
- Modify: `src/cli/v2/events/reducer.ts`
- Modify: `src/cli/v2/state/store.ts`
- Modify: `src/cli/v2/state/slices/ui.ts`
- Test: `__tests__/cli/v2/state/panel.test.ts`

- [ ] **Step 1: Write failing tests for the panel slice reducer**

Create `__tests__/cli/v2/state/panel.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { applyPanelEvent } from "../../../../src/cli/v2/state/slices/panel.js";
import type { PanelSliceState } from "../../../../src/cli/v2/state/slices/panel.js";
import type { UiState } from "../../../../src/cli/v2/state/store.js";

const baseState = (): UiState => ({
  // minimal stub — panel slice only reads/writes panel fields
  activePanel: null,
  panelFocus: "composer",
} as unknown as UiState);

describe("applyPanelEvent", () => {
  it("opens a panel and sets focus to panel", () => {
    const state = baseState();
    const next = applyPanelEvent(state, {
      kind: "panel.opened",
      id: "skills",
      props: { title: "Skills", items: [] },
    });
    expect(next.activePanel).toEqual({ id: "skills", props: { title: "Skills", items: [] } });
    expect(next.panelFocus).toBe("panel");
  });

  it("closes a panel and returns focus to composer", () => {
    const state = { ...baseState(), activePanel: { id: "skills", props: {} }, panelFocus: "panel" as const };
    const next = applyPanelEvent(state as unknown as UiState, { kind: "panel.closed" });
    expect(next.activePanel).toBeNull();
    expect(next.panelFocus).toBe("composer");
  });

  it("returns state unchanged for unrelated events", () => {
    const state = baseState();
    const next = applyPanelEvent(state, { kind: "token.delta", turnId: "t1", text: "hi" } as any);
    expect(next).toBe(state);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/cli/v2/state/panel.test.ts 2>&1 | tail -20
```
Expected: FAIL with "Cannot find module" or similar.

- [ ] **Step 3: Add two new UiEvent types to `src/cli/v2/events/UiEvent.ts`**

Append to the interfaces section (before the `// ─── Union` comment):

```typescript
// ─── Panel ────────────────────────────────────────────────────────────────────

export interface PanelOpenedEvent {
  kind: "panel.opened";
  id: string;
  props: unknown;  // PanelProps without onDismiss — added by PanelHost
}

export interface PanelClosedEvent {
  kind: "panel.closed";
}

// ─── Onboarding ───────────────────────────────────────────────────────────────

export interface OnboardingViewRequestedEvent {
  kind: "onboarding.view.requested";
}

export interface OnboardingViewDismissedEvent {
  kind: "onboarding.view.dismissed";
}
```

Add all four to the `UiEvent` union at the bottom of the file:

```typescript
  | PanelOpenedEvent
  | PanelClosedEvent
  | OnboardingViewRequestedEvent
  | OnboardingViewDismissedEvent;
```

- [ ] **Step 4: Create `src/cli/v2/state/slices/panel.ts`**

```typescript
import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface ActivePanel {
  id: string;
  props: unknown;
}

export interface PanelSliceState {
  activePanel: ActivePanel | null;
  panelFocus: "composer" | "panel";
}

export const initialPanelSliceState: PanelSliceState = {
  activePanel: null,
  panelFocus: "composer",
};

export function applyPanelEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "panel.opened":
      return { ...state, activePanel: { id: event.id, props: event.props }, panelFocus: "panel" };
    case "panel.closed":
      return { ...state, activePanel: null, panelFocus: "composer" };
    case "onboarding.view.requested":
      return { ...state, mode: "onboarding" };
    case "onboarding.view.dismissed":
      return { ...state, mode: "chat" };
    default:
      return state;
  }
}
```

- [ ] **Step 5: Wire the panel slice into the store**

In `src/cli/v2/state/store.ts`, add the import and extend `UiState`:

```typescript
import type { PanelSliceState } from "./slices/panel.js";
import { initialPanelSliceState } from "./slices/panel.js";

export interface UiState
  extends TurnsState,
    ToolsState,
    ParliamentState,
    HeartbeatState,
    SessionState,
    UiSliceState,
    PaletteState,
    PanelSliceState {}   // ← add this line

export const initialState: UiState = {
  ...initialTurnsState,
  ...initialToolsState,
  ...initialParliamentState,
  ...initialHeartbeatState,
  ...initialSessionState,
  ...initialUiSliceState,
  ...initialPaletteState,
  ...initialPanelSliceState,  // ← add this line
};
```

- [ ] **Step 6: Wire `applyPanelEvent` into the root reducer**

In `src/cli/v2/events/reducer.ts`, add the import and call:

```typescript
import { applyPanelEvent } from "../state/slices/panel.js";

export function reduce(state: UiState, event: UiEvent): UiState {
  let next = state;
  next = applyTurnsEvent(next, event);
  next = applyToolsEvent(next, event);
  next = applyParliamentEvent(next, event);
  next = applyHeartbeatEvent(next, event);
  next = applySessionEvent(next, event);
  next = applyUiEvent(next, event);
  next = applyPaletteEvent(next, event);
  next = applyPanelEvent(next, event);   // ← add this line
  return next;
}
```

- [ ] **Step 7: Add `openPanel`, `closePanel`, `requestOnboardingView`, `dismissOnboardingView` to `src/cli/v2/events/bridge.ts`**

Append these methods to the `UiBridge` class (after `dismissHelpView`):

```typescript
// ─── Panel ────────────────────────────────────────────────────────────────────

openPanel(id: string, props: unknown): void {
  this.emit({ kind: "panel.opened", id, props });
}

closePanel(): void {
  this.emit({ kind: "panel.closed" });
}

// ─── Onboarding ───────────────────────────────────────────────────────────────

requestOnboardingView(): void {
  this.emit({ kind: "onboarding.view.requested" });
}

dismissOnboardingView(): void {
  this.emit({ kind: "onboarding.view.dismissed" });
}
```

- [ ] **Step 8: Remove `showSkillsOverlay` and `showMcpOverlay` from the ui slice**

In `src/cli/v2/state/slices/ui.ts`:

Remove from `UiSliceState` interface:
```typescript
// DELETE THESE TWO LINES:
showSkillsOverlay: boolean;
showMcpOverlay: boolean;
```

Remove from `initialUiSliceState`:
```typescript
// DELETE THESE TWO LINES:
showSkillsOverlay: false,
showMcpOverlay: false,
```

Remove from `applyUiEvent` switch (delete the four cases):
```typescript
// DELETE: case "skills.view.requested" → showSkillsOverlay: true
// DELETE: case "skills.view.dismissed" → showSkillsOverlay: false
// DELETE: case "mcp.view.requested"    → showMcpOverlay: true
// DELETE: case "mcp.view.dismissed"    → showMcpOverlay: false
```

- [ ] **Step 9: Run the tests — expect PASS**

```bash
npx vitest run __tests__/cli/v2/state/panel.test.ts 2>&1 | tail -20
```
Expected: 3 tests pass.

- [ ] **Step 10: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | head -40
```
Fix any errors before committing. Common issue: anything still referencing `showSkillsOverlay` or `showMcpOverlay` — find and fix them.

```bash
grep -rn "showSkillsOverlay\|showMcpOverlay" src/cli/v2/
```
Each hit needs to be removed or updated.

- [ ] **Step 11: Commit**

```bash
git add src/cli/v2/state/slices/panel.ts \
        src/cli/v2/state/store.ts \
        src/cli/v2/events/UiEvent.ts \
        src/cli/v2/events/bridge.ts \
        src/cli/v2/events/reducer.ts \
        src/cli/v2/state/slices/ui.ts \
        __tests__/cli/v2/state/panel.test.ts
git commit -m "feat(tui-v2): add PanelSlice + panel.opened/closed events + onboarding mode"
```

---

### Task 2: Generic `Panel.tsx` component

**Files:**
- Create: `src/cli/v2/panels/Panel.tsx`

- [ ] **Step 1: Create `src/cli/v2/panels/Panel.tsx`**

```tsx
import { useState } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

export interface PanelItem {
  id: string;
  label: string;
  meta?: string;
  data?: unknown;
}

export interface PanelAction {
  key: string;          // single char or "return"
  label: string;
  handler: (item: PanelItem) => void | Promise<void>;
  confirm?: string;     // if set, show "Type 'yes' to confirm:" before firing
  destructive?: boolean;
}

export interface PanelProps {
  title: string;
  color?: string;
  items: PanelItem[];
  actions?: PanelAction[];
  onDismiss: () => void;
  emptyText?: string;
}

export function Panel({ title, color, items, actions = [], onDismiss, emptyText = "No items." }: PanelProps) {
  const { colors } = useTheme();
  const { stdout } = useStdout();
  const [scrollTop, setScrollTop] = useState(0);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [confirming, setConfirming] = useState<PanelAction | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [working, setWorking] = useState(false);

  const rows = stdout?.rows ?? 24;
  // Reserve: TopBar(2) + Composer(4) + StatusBar(1) + ShortcutsBar(1) + panel header(2) + footer(2) + padding(2)
  const maxVisible = Math.max(3, rows - 14);

  const visibleItems = items.slice(scrollTop, scrollTop + maxVisible);
  const hasAbove = scrollTop > 0;
  const hasBelow = scrollTop + maxVisible < items.length;
  const borderColor = color ?? colors.accent;

  // Clamp selectedIdx to valid range when items change
  const clampedIdx = items.length > 0 ? Math.min(selectedIdx, items.length - 1) : 0;

  useInput((_input, key) => {
    if (confirming) {
      if (key.escape) { setConfirming(null); setConfirmInput(""); return; }
      if (key.return) {
        if (confirmInput.toLowerCase() === "yes") {
          setWorking(true);
          Promise.resolve(confirming.handler(items[clampedIdx]!)).finally(() => {
            setWorking(false);
            setConfirming(null);
            setConfirmInput("");
          });
        } else {
          setConfirming(null);
          setConfirmInput("");
        }
        return;
      }
      if (key.backspace || key.delete) { setConfirmInput((v) => v.slice(0, -1)); return; }
      if (!key.ctrl && !key.meta && _input.length === 1) { setConfirmInput((v) => v + _input); return; }
      return;
    }

    if (key.escape) { onDismiss(); return; }

    if (key.upArrow) {
      const newIdx = Math.max(0, clampedIdx - 1);
      setSelectedIdx(newIdx);
      if (newIdx < scrollTop) setScrollTop(newIdx);
      return;
    }
    if (key.downArrow) {
      const newIdx = Math.min(items.length - 1, clampedIdx + 1);
      setSelectedIdx(newIdx);
      if (newIdx >= scrollTop + maxVisible) setScrollTop(newIdx - maxVisible + 1);
      return;
    }

    // Action key dispatch
    const selectedItem = items[clampedIdx];
    if (!selectedItem) return;
    for (const action of actions) {
      const matches =
        action.key === "return" ? key.return :
        (_input === action.key && !key.ctrl && !key.meta);
      if (matches) {
        if (action.confirm) {
          setConfirming(action);
          setConfirmInput("");
        } else {
          setWorking(true);
          Promise.resolve(action.handler(selectedItem)).finally(() => setWorking(false));
        }
        return;
      }
    }
  });

  const footerActions = confirming
    ? `Type 'yes' to confirm ${confirming.label} (Enter/Esc):`
    : [
        "↑↓ nav",
        ...actions.map((a) => `${a.key === "return" ? "Enter" : a.key} ${a.label}`),
        "Esc close",
      ].join("  ·  ");

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={borderColor} paddingX={1}>
      <Box>
        <Text bold color={borderColor}>{title}</Text>
      </Box>

      {hasAbove && (
        <Box paddingLeft={1}>
          <Text dimColor>▲ {scrollTop} above</Text>
        </Box>
      )}

      {items.length === 0 ? (
        <Box paddingLeft={1}>
          <Text dimColor>{emptyText}</Text>
        </Box>
      ) : (
        <Box flexDirection="column">
          {visibleItems.map((item, i) => {
            const absIdx = scrollTop + i;
            const isSelected = absIdx === clampedIdx;
            const isWorking = working && isSelected;
            return (
              <Box key={item.id}>
                <Text color={isSelected ? borderColor : undefined} bold={isSelected}>
                  {isSelected ? "❯ " : "  "}
                </Text>
                <Text bold={isSelected}>{isWorking ? "⟳ " : ""}{item.label}</Text>
                {item.meta && <Text dimColor>{"  " + item.meta}</Text>}
              </Box>
            );
          })}
        </Box>
      )}

      {hasBelow && (
        <Box paddingLeft={1}>
          <Text dimColor>▼ {items.length - scrollTop - maxVisible} more</Text>
        </Box>
      )}

      <Box marginTop={1}>
        {confirming ? (
          <Box>
            <Text color={colors.warning}>{footerActions} </Text>
            <Text>{confirmInput}</Text>
            <Text color={colors.accent}>▋</Text>
          </Box>
        ) : (
          <Text dimColor>{footerActions}</Text>
        )}
      </Box>
    </Box>
  );
}
```

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "panels/Panel"
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/cli/v2/panels/Panel.tsx
git commit -m "feat(tui-v2): add generic Panel component with scroll, actions, confirm flow"
```

---

### Task 3: `PanelHost.tsx` + wire into ChatScreen

**Files:**
- Create: `src/cli/v2/panels/PanelHost.tsx`
- Modify: `src/cli/v2/screens/ChatScreen.tsx`

- [ ] **Step 1: Create `src/cli/v2/panels/PanelHost.tsx`**

```tsx
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Panel } from "./Panel.js";
import type { PanelProps } from "./Panel.js";

export function PanelHost() {
  const activePanel = useUiStore((s) => s.activePanel);

  if (!activePanel) return null;

  const props = activePanel.props as Omit<PanelProps, "onDismiss">;

  return (
    <Panel
      {...props}
      onDismiss={() => globalBridge.closePanel()}
    />
  );
}
```

- [ ] **Step 2: Update `src/cli/v2/screens/ChatScreen.tsx`**

Read the current ChatScreen, then make these changes:

Remove these imports:
```typescript
import { SkillsOverlay } from "../components/SkillsOverlay.js";
import { McpOverlay } from "../components/McpOverlay.js";
```

Add this import:
```typescript
import { PanelHost } from "../panels/PanelHost.js";
```

Remove these store reads:
```typescript
const showSkillsOverlay = useUiStore((s) => s.showSkillsOverlay);
const showMcpOverlay    = useUiStore((s) => s.showMcpOverlay);
```

Add this store read:
```typescript
const panelFocus = useUiStore((s) => s.panelFocus);
```

In the JSX, replace:
```tsx
{showSkillsOverlay && <SkillsOverlay />}
{showMcpOverlay && <McpOverlay />}
<Composer
  onSubmit={onSubmit}
  disabled={generating || showHelp || showSkillsOverlay || showMcpOverlay}
/>
```

With:
```tsx
<PanelHost />
<Composer
  onSubmit={onSubmit}
  disabled={generating || showHelp || panelFocus === "panel"}
/>
```

- [ ] **Step 3: Delete the old overlay files**

```bash
rm src/cli/v2/components/SkillsOverlay.tsx src/cli/v2/components/McpOverlay.tsx
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | head -30
```
Fix any remaining references to deleted overlays.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/panels/PanelHost.tsx src/cli/v2/screens/ChatScreen.tsx
git rm src/cli/v2/components/SkillsOverlay.tsx src/cli/v2/components/McpOverlay.tsx
git commit -m "feat(tui-v2): add PanelHost, wire into ChatScreen, delete overlay components"
```

---

### Task 4: Migrate `/skills` and `/mcp` to use the new Panel system

> The bridge methods `requestSkillsView()` / `requestMcpView()` now need to open panels instead of flipping overlay booleans. We'll update the bridge to do this, and the Composer's existing `dispatchSlash()` will call these same bridge methods — so `/skills` and `/mcp` work immediately without touching the Composer yet.

**Files:**
- Modify: `src/cli/v2/events/bridge.ts`

- [ ] **Step 1: Update `requestSkillsView()` in `src/cli/v2/events/bridge.ts`**

Replace the existing `requestSkillsView()` body:
```typescript
requestSkillsView(): void {
  this.emit({ kind: "skills.view.requested" });
}
```
With:
```typescript
requestSkillsView(): void {
  // Legacy compatibility: read skills from store and open a panel.
  // Full implementation is in commands/handlers/skills.ts (Phase 3).
  // Interim: emit the legacy event which is now handled by panel.opened in applyPanelEvent.
  this.emit({ kind: "skills.view.requested" });
}
```

Actually — the legacy `skills.view.requested` event no longer has a handler in the ui slice (we deleted those reducer cases in Task 1). So we need to handle it differently now.

Replace the **full** `requestSkillsView()` body with one that opens a panel:
```typescript
requestSkillsView(): void {
  // Snapshot current skills from store and open a panel.
  // The store is already populated by loadSkills() called at startup.
  const { installedSkills } = (await import("../state/store.js")).uiStore.getState();
  const items = installedSkills.map((s) => ({
    id: s.name,
    label: s.name,
    meta: s.enabled ? "✓ enabled" : "✗ disabled",
    data: s,
  }));
  this.openPanel("skills", {
    title: "/skills",
    color: undefined,
    items,
    emptyText: "No skills loaded. Check your skills directory.",
  });
}
```

Wait — `requestSkillsView()` is not async and `import()` is async. Simpler: read from store synchronously:

```typescript
requestSkillsView(): void {
  // Read current skills snapshot from the vanilla store (synchronous).
  // Full handler with subcommands lives in commands/handlers/skills.ts.
  import("../state/store.js").then(({ uiStore }) => {
    const { installedSkills } = uiStore.getState();
    const items = installedSkills.map((s) => ({
      id: s.name,
      label: s.name,
      meta: s.enabled ? "✓ enabled" : "✗ disabled",
    }));
    this.openPanel("skills", {
      title: "/skills",
      items,
      emptyText: "No skills loaded. Check your skills directory.",
    });
  });
}

dismissSkillsView(): void {
  this.closePanel();
}
```

And similarly update `requestMcpView()`:

```typescript
requestMcpView(): void {
  import("../state/store.js").then(({ uiStore }) => {
    const { mcpServers } = uiStore.getState();
    const items = mcpServers.map((s) => ({
      id: s.name,
      label: s.name,
      meta: `${s.connected ? "● connected" : "○ disconnected"}  ${s.toolCount} tool${s.toolCount !== 1 ? "s" : ""}  ${s.transport}`,
    }));
    this.openPanel("mcp", {
      title: "/mcp",
      items,
      emptyText: "No MCP servers configured.",
    });
  });
}

dismissMcpView(): void {
  this.closePanel();
}
```

- [ ] **Step 2: TypeScript check + verify `/skills` and `/mcp` still dispatch**

```bash
npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Quick smoke test**

```bash
npm run dev
```
Type `/skills` → panel appears with list. Arrow keys scroll. Esc dismisses. Type `/mcp` → panel appears. Everything should still work from the Composer perspective.

- [ ] **Step 4: Commit**

```bash
git add src/cli/v2/events/bridge.ts
git commit -m "feat(tui-v2): migrate /skills and /mcp to panel system via bridge"
```

---

## Phase 2 — Command Registry + Composer Rewrite

### Task 5: Command registry types + REGISTRY

**Files:**
- Create: `src/cli/v2/commands/registry.ts`
- Test: `__tests__/cli/v2/commands/registry.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/cli/v2/commands/registry.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { REGISTRY, resolveCommand, type CommandContext } from "../../../../src/cli/v2/commands/registry.js";

const ctx = {} as CommandContext;

describe("REGISTRY", () => {
  it("has at least 7 commands", () => {
    expect(REGISTRY.length).toBeGreaterThanOrEqual(7);
  });

  it("resolves /quit by name", () => {
    const result = resolveCommand("/quit");
    expect(result).not.toBeNull();
    expect(result!.spec.name).toBe("/quit");
  });

  it("resolves /exit as alias for /quit", () => {
    const result = resolveCommand("/exit");
    expect(result).not.toBeNull();
    expect(result!.spec.name).toBe("/quit");
  });

  it("resolves /memory subcommand list", () => {
    const result = resolveCommand("/memory list");
    expect(result).not.toBeNull();
    expect(result!.subcommand?.name).toBe("list");
  });

  it("returns null for unknown command", () => {
    expect(resolveCommand("/nonexistent")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to confirm fail**

```bash
npx vitest run __tests__/cli/v2/commands/registry.test.ts 2>&1 | tail -10
```
Expected: FAIL with "Cannot find module".

- [ ] **Step 3: Create `src/cli/v2/commands/registry.ts`**

```typescript
import type { UiBridge } from "../events/bridge.js";
import type { UiState } from "../state/store.js";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PanelPayload {
  title: string;
  color?: string;
  items: Array<{ id: string; label: string; meta?: string; data?: unknown }>;
  actions?: Array<{
    key: string;
    label: string;
    handler: (item: { id: string; label: string; meta?: string; data?: unknown }) => void | Promise<void>;
    confirm?: string;
    destructive?: boolean;
  }>;
  emptyText?: string;
}

export type CommandResult =
  | { kind: "panel"; payload: PanelPayload }
  | { kind: "system-message"; text: string }
  | { kind: "action" }
  | { kind: "error"; text: string };

export interface CommandContext {
  // Gateway access for commands that need runtime data
  getMemoryRepo: () => import("../../memory/repository.js").MemoryRepository;
  getMcpManager: () => import("../../tools/mcp/manager.js").MCPManager;
  getOwlGateway: () => import("../../gateway/core.js").OwlGateway;
  bridge: UiBridge;
  getStore: () => UiState;
}

export type CommandHandler = (ctx: CommandContext, args: string[]) => Promise<CommandResult>;

export interface ArgSpec {
  name: string;
  description?: string;
}

export interface SubcommandSpec {
  name: string;
  description: string;
  args?: ArgSpec[];
  complete?: (ctx: CommandContext, partial: string) => Promise<string[]>;
  handler: CommandHandler;
}

export interface CommandSpec {
  name: string;
  aliases?: string[];
  description: string;
  subcommands?: SubcommandSpec[];
  handler?: CommandHandler;
}

// ─── Resolve helper ───────────────────────────────────────────────────────────

export interface ResolvedCommand {
  spec: CommandSpec;
  subcommand?: SubcommandSpec;
  args: string[];
}

export function resolveCommand(input: string): ResolvedCommand | null {
  const parts = input.trim().split(/\s+/);
  const cmdName = parts[0] ?? "";

  const spec = REGISTRY.find(
    (s) => s.name === cmdName || (s.aliases ?? []).includes(cmdName),
  );
  if (!spec) return null;

  if (spec.subcommands && parts[1]) {
    const sub = spec.subcommands.find((s) => s.name === parts[1]);
    if (sub) return { spec, subcommand: sub, args: parts.slice(2) };
    // If no subcommand matched and the command has subcommands, dispatch to first or return error
    return { spec, args: parts.slice(1) };
  }

  return { spec, args: parts.slice(1) };
}

// ─── Placeholder handlers (replaced phase by phase) ──────────────────────────

async function notImplemented(_ctx: CommandContext, _args: string[]): Promise<CommandResult> {
  return { kind: "error", text: "Command not yet implemented in v2." };
}

// ─── Registry ────────────────────────────────────────────────────────────────

export const REGISTRY: CommandSpec[] = [
  {
    name: "/help",
    aliases: ["/?"],
    description: "Show available commands",
    handler: notImplemented, // replaced in Phase 3
  },
  {
    name: "/sessions",
    description: "Browse and resume sessions",
    handler: async (ctx) => {
      const { recentSessions } = ctx.getStore();
      const items = recentSessions.map((s) => ({
        id: s.sessionId,
        label: s.title || s.sessionId.slice(0, 24),
        meta: new Date(s.lastActiveAt).toLocaleDateString(),
        data: s,
      }));
      return {
        kind: "panel",
        payload: { title: "/sessions", items, emptyText: "No sessions yet." },
      };
    },
  },
  {
    name: "/owls",
    description: "Browse and switch owl personas",
    handler: async (ctx) => {
      const { owls } = ctx.getStore();
      const items = owls.map((o) => ({
        id: o.name,
        label: `${o.emoji} ${o.name}`,
        meta: o.isActive ? "active" : o.description.slice(0, 40),
        data: o,
      }));
      return {
        kind: "panel",
        payload: { title: "/owls", items, emptyText: "No owls loaded." },
      };
    },
  },
  {
    name: "/skills",
    description: "List installed skills",
    handler: async (ctx) => {
      const { installedSkills } = ctx.getStore();
      const items = installedSkills.map((s) => ({
        id: s.name,
        label: s.name,
        meta: s.enabled ? "✓ enabled" : "✗ disabled",
        data: s,
      }));
      return {
        kind: "panel",
        payload: { title: "/skills", items, emptyText: "No skills loaded." },
      };
    },
  },
  {
    name: "/mcp",
    description: "Manage MCP servers",
    subcommands: [
      { name: "list",      description: "List all configured MCP servers", handler: notImplemented },
      { name: "status",    description: "Full status report",              handler: notImplemented },
      { name: "add",       description: "Install + connect a server",      args: [{ name: "<package>" }], handler: notImplemented },
      { name: "remove",    description: "Remove a server",                 args: [{ name: "<name>" }],    handler: notImplemented },
      { name: "enable",    description: "Enable a server",                 args: [{ name: "<name>" }],    handler: notImplemented },
      { name: "disable",   description: "Disable a server",                args: [{ name: "<name>" }],    handler: notImplemented },
      { name: "tools",     description: "List tools for a server",         args: [{ name: "<name>" }],    handler: notImplemented },
      { name: "reconnect", description: "Reconnect a server",              args: [{ name: "<name>" }],    handler: notImplemented },
    ],
    handler: async (ctx) => {
      // bare /mcp → list
      const { mcpServers } = ctx.getStore();
      const items = mcpServers.map((s) => ({
        id: s.name,
        label: s.name,
        meta: `${s.connected ? "● connected" : "○ disconnected"}  ${s.toolCount} tool${s.toolCount !== 1 ? "s" : ""}`,
        data: s,
      }));
      return { kind: "panel", payload: { title: "/mcp list", items, emptyText: "No MCP servers configured." } };
    },
  },
  {
    name: "/memory",
    aliases: ["/mem"],
    description: "View and manage memory",
    subcommands: [
      { name: "list",       description: "List all memory entries",              handler: notImplemented },
      { name: "search",     description: "Search memory",  args: [{ name: "<query>" }], handler: notImplemented },
      { name: "get",        description: "Show one entry", args: [{ name: "<key>" }],   handler: notImplemented },
      { name: "invalidate", description: "Delete an entry",args: [{ name: "<key>" }],   handler: notImplemented },
      { name: "stats",      description: "Memory statistics",                    handler: notImplemented },
      { name: "history",    description: "View invalidations", args: [{ name: "<id>" }], handler: notImplemented },
      { name: "export",     description: "JSON dump of all valid memories",      handler: notImplemented },
    ],
    handler: async (ctx) => {
      // bare /memory → list (same as /memory list)
      return notImplemented(ctx, []);
    },
  },
  {
    name: "/helper",
    description: "Manage helper owl personas",
    subcommands: [
      { name: "list",         description: "List all helpers",             handler: notImplemented },
      { name: "show",         description: "Show helper details", args: [{ name: "<name>" }], handler: notImplemented },
      { name: "create",       description: "Create a new helper",          handler: notImplemented },
      { name: "rename",       description: "Rename a helper",  args: [{ name: "<old>" }, { name: "<new>" }], handler: notImplemented },
      { name: "delete",       description: "Delete a helper",  args: [{ name: "<name>" }], handler: notImplemented },
      { name: "design",       description: "Redesign a helper",args: [{ name: "<name>" }], handler: notImplemented },
      { name: "capabilities", description: "List helper capabilities",     handler: notImplemented },
    ],
    handler: notImplemented,
  },
  {
    name: "/owl",
    description: "Show current owl status",
    subcommands: [
      { name: "status", description: "Show owl state + memory stats", handler: notImplemented },
    ],
    handler: notImplemented,
  },
  {
    name: "/status",
    description: "Show provider, model, and owl info",
    handler: notImplemented,
  },
  {
    name: "/clear",
    aliases: ["/reset"],
    description: "Clear conversation context",
    handler: notImplemented,
  },
  {
    name: "/capabilities",
    description: "List synthesized capabilities",
    handler: notImplemented,
  },
  {
    name: "/learning",
    description: "Show learning report",
    handler: notImplemented,
  },
  {
    name: "/onboarding",
    description: "Re-run setup wizard",
    handler: async (ctx) => {
      ctx.bridge.requestOnboardingView();
      return { kind: "action" };
    },
  },
  {
    name: "/quit",
    aliases: ["/exit", "/bye"],
    description: "Save session and exit",
    handler: async (_ctx) => {
      // Handled specially by Composer (calls app exit)
      return { kind: "action" };
    },
  },
];
```

- [ ] **Step 4: Run registry tests**

```bash
npx vitest run __tests__/cli/v2/commands/registry.test.ts 2>&1 | tail -20
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/commands/registry.ts __tests__/cli/v2/commands/registry.test.ts
git commit -m "feat(tui-v2): add CommandRegistry with resolveCommand + placeholder handlers"
```

---

### Task 6: Completion engine

**Files:**
- Create: `src/cli/v2/commands/completion.ts`
- Test: `__tests__/cli/v2/commands/completion.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/cli/v2/commands/completion.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { getCompletions } from "../../../../src/cli/v2/commands/completion.js";
import type { CommandContext } from "../../../../src/cli/v2/commands/registry.js";

const ctx = {} as CommandContext;

describe("getCompletions", () => {
  it("returns all commands for '/'", async () => {
    const results = await getCompletions("/", ctx);
    expect(results.length).toBeGreaterThanOrEqual(7);
    expect(results.every((r) => r.kind === "command")).toBe(true);
  });

  it("filters by prefix '/me'", async () => {
    const results = await getCompletions("/me", ctx);
    const names = results.map((r) => r.value);
    expect(names).toContain("/memory");
    expect(names.every((n) => n.startsWith("/me"))).toBe(true);
  });

  it("returns subcommands for '/memory '", async () => {
    const results = await getCompletions("/memory ", ctx);
    expect(results.every((r) => r.kind === "subcommand")).toBe(true);
    const names = results.map((r) => r.value);
    expect(names).toContain("list");
    expect(names).toContain("search");
  });

  it("filters subcommands by prefix '/memory li'", async () => {
    const results = await getCompletions("/memory li", ctx);
    expect(results.map((r) => r.value)).toContain("list");
  });

  it("returns empty array for '/unknown'", async () => {
    const results = await getCompletions("/unknown", ctx);
    expect(results).toHaveLength(0);
  });

  it("returns empty for plain text (no slash)", async () => {
    const results = await getCompletions("hello", ctx);
    expect(results).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to confirm fail**

```bash
npx vitest run __tests__/cli/v2/commands/completion.test.ts 2>&1 | tail -10
```

- [ ] **Step 3: Create `src/cli/v2/commands/completion.ts`**

```typescript
import { REGISTRY } from "./registry.js";
import type { CommandContext } from "./registry.js";

export type CompletionKind = "command" | "subcommand" | "arg";

export interface CompletionEntry {
  kind: CompletionKind;
  value: string;
  description?: string;
}

/**
 * Returns completions for the current input string.
 *
 * Three modes:
 * 1. starts with "/" + no space → complete top-level command names
 * 2. "/cmd " (matched, trailing space, no subcmd typed) → complete subcommand names
 * 3. "/cmd sub " (matched + subcommand has `complete` fn) → dynamic arg values
 */
export async function getCompletions(input: string, ctx: CommandContext): Promise<CompletionEntry[]> {
  if (!input.startsWith("/")) return [];

  const parts = input.split(/\s+/);
  const cmdPart = parts[0] ?? "";
  const hasTrailingSpace = input.endsWith(" ");

  // Mode 1: completing command name (no space yet)
  if (parts.length === 1 && !hasTrailingSpace) {
    return REGISTRY.flatMap((spec) => {
      const names = [spec.name, ...(spec.aliases ?? [])];
      return names
        .filter((n) => n.startsWith(cmdPart))
        .map((n) => ({ kind: "command" as const, value: n, description: spec.description }));
    });
  }

  // Find the command spec
  const spec = REGISTRY.find(
    (s) => s.name === cmdPart || (s.aliases ?? []).includes(cmdPart),
  );
  if (!spec || !spec.subcommands) return [];

  // Mode 2: completing subcommand name
  if (parts.length === 1 && hasTrailingSpace) {
    // "/cmd " — show all subcommands
    return spec.subcommands.map((sub) => ({
      kind: "subcommand",
      value: sub.name,
      description: sub.description,
    }));
  }

  if (parts.length === 2 && !hasTrailingSpace) {
    // "/cmd par" — filter subcommands
    const partial = parts[1] ?? "";
    return spec.subcommands
      .filter((sub) => sub.name.startsWith(partial))
      .map((sub) => ({ kind: "subcommand", value: sub.name, description: sub.description }));
  }

  // Mode 3: dynamic arg completion
  if (parts.length >= 2) {
    const subcmdName = parts[1] ?? "";
    const sub = spec.subcommands.find((s) => s.name === subcmdName);
    if (sub?.complete && (parts.length > 2 || hasTrailingSpace)) {
      const partial = hasTrailingSpace ? "" : (parts[parts.length - 1] ?? "");
      const values = await sub.complete(ctx, partial);
      return values.map((v) => ({ kind: "arg", value: v }));
    }
  }

  return [];
}
```

- [ ] **Step 4: Run completion tests**

```bash
npx vitest run __tests__/cli/v2/commands/completion.test.ts 2>&1 | tail -20
```
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/commands/completion.ts __tests__/cli/v2/commands/completion.test.ts
git commit -m "feat(tui-v2): add completion engine — top-level, subcommand, dynamic arg modes"
```

---

### Task 7: CommandDispatcher + context provider

**Files:**
- Create: `src/cli/v2/commands/dispatcher.ts`
- Create: `src/cli/v2/providers/CommandDispatcherProvider.tsx`
- Modify: `src/cli/v2/app.tsx`
- Modify: `src/gateway/adapters/cli-v2.ts`

- [ ] **Step 1: Create `src/cli/v2/commands/dispatcher.ts`**

```typescript
import { resolveCommand } from "./registry.js";
import type { CommandContext, CommandResult } from "./registry.js";
import { globalBridge } from "../events/bridge.js";
import { uiStore } from "../state/store.js";

export interface CommandDispatcher {
  dispatch(input: string): Promise<CommandResult>;
}

export function createCommandDispatcher(
  ctxFactory: () => Omit<CommandContext, "bridge" | "getStore">,
): CommandDispatcher {
  const ctx: CommandContext = {
    ...ctxFactory(),
    bridge: globalBridge,
    getStore: () => uiStore.getState(),
  };

  return {
    async dispatch(input: string): Promise<CommandResult> {
      const resolved = resolveCommand(input);
      if (!resolved) {
        return { kind: "error", text: `unknown command: ${input.split(" ")[0]} · type /help` };
      }

      const handler = resolved.subcommand?.handler ?? resolved.spec.handler;
      if (!handler) {
        return { kind: "error", text: `${resolved.spec.name}: missing handler` };
      }

      const result = await handler(ctx, resolved.args);

      // Side-effects for panel and error results
      if (result.kind === "panel") {
        globalBridge.openPanel(resolved.subcommand?.name ?? resolved.spec.name, result.payload);
      }

      return result;
    },
  };
}
```

- [ ] **Step 2: Create `src/cli/v2/providers/CommandDispatcherProvider.tsx`**

```tsx
import React, { createContext, useContext } from "react";
import type { CommandDispatcher } from "../commands/dispatcher.js";

const CommandDispatcherContext = createContext<CommandDispatcher | null>(null);

export function CommandDispatcherProvider({
  dispatcher,
  children,
}: {
  dispatcher: CommandDispatcher;
  children: React.ReactNode;
}) {
  return (
    <CommandDispatcherContext.Provider value={dispatcher}>
      {children}
    </CommandDispatcherContext.Provider>
  );
}

export function useCommandDispatcher(): CommandDispatcher {
  const d = useContext(CommandDispatcherContext);
  if (!d) throw new Error("useCommandDispatcher must be used within CommandDispatcherProvider");
  return d;
}
```

- [ ] **Step 3: Update `src/cli/v2/app.tsx` to accept + provide the dispatcher**

```tsx
import { CommandDispatcherProvider } from "./providers/CommandDispatcherProvider.js";
import type { CommandDispatcher } from "./commands/dispatcher.js";

export interface AppProps {
  onSubmit: (text: string) => void;
  onResume: (sessionId: string, title: string) => void;
  commandDispatcher: CommandDispatcher;  // ← add
}

export function App({ onSubmit, onResume, commandDispatcher }: AppProps) {
  return (
    <ThemeProvider>
      <UiStoreProvider>
        <EventBusProvider>
          <CommandDispatcherProvider dispatcher={commandDispatcher}>
            <ActiveScreen onSubmit={onSubmit} onResume={onResume} />
          </CommandDispatcherProvider>
        </EventBusProvider>
      </UiStoreProvider>
    </ThemeProvider>
  );
}
```

- [ ] **Step 4: Update `src/gateway/adapters/cli-v2.ts` to create and expose the dispatcher**

Find the `submitMessage` method. Add a `commandDispatcher` getter below the constructor.

First, add the import at the top of the file:
```typescript
import { createCommandDispatcher } from "../../cli/v2/commands/dispatcher.js";
import type { CommandDispatcher } from "../../cli/v2/commands/dispatcher.js";
```

Add a property and getter to `CliV2Adapter`:
```typescript
private _commandDispatcher: CommandDispatcher | null = null;

getCommandDispatcher(): CommandDispatcher {
  if (!this._commandDispatcher) {
    this._commandDispatcher = createCommandDispatcher(() => ({
      getMemoryRepo: () => this._gateway.getMemoryRepo(),
      getMcpManager: () => this._gateway.getMcpManager(),
      getOwlGateway: () => this._gateway,
    }));
  }
  return this._commandDispatcher;
}
```

- [ ] **Step 5: Find where `render(<App ...>)` is called and pass the dispatcher**

Search for the Ink render call in the codebase:
```bash
grep -rn "render.*App\|ink.*render" src/ --include="*.ts" --include="*.tsx" | head -10
```

In whatever file calls `render(<App ...>)`, add the dispatcher:
```typescript
const dispatcher = adapter.getCommandDispatcher();
render(<App onSubmit={...} onResume={...} commandDispatcher={dispatcher} />);
```

- [ ] **Step 6: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 7: Commit**

```bash
git add src/cli/v2/commands/dispatcher.ts \
        src/cli/v2/providers/CommandDispatcherProvider.tsx \
        src/cli/v2/app.tsx \
        src/gateway/adapters/cli-v2.ts
git commit -m "feat(tui-v2): add CommandDispatcher + provider, wire into CliV2Adapter and App"
```

---

### Task 8: Rewrite Composer with registry-driven popup

**Files:**
- Modify: `src/cli/v2/components/Composer.tsx`

- [ ] **Step 1: Read the current Composer.tsx in full before editing**

```bash
cat -n src/cli/v2/components/Composer.tsx
```

- [ ] **Step 2: Replace `src/cli/v2/components/Composer.tsx` with registry-driven version**

Key changes from the current file:
1. Remove `SLASH_COMMANDS` constant and `dispatchSlash()` function
2. Import `getCompletions` and `useCommandDispatcher`
3. Replace popup state (popupIdx only) with a `completions` state array
4. On every keystroke, call `getCompletions(value, ctx)` asynchronously
5. On Enter: if completions open, accept; else if starts with `/`, dispatch via registry; else `onSubmit`
6. Ctrl+L calls dispatcher with `/clear`
7. Ctrl+D on empty input calls `exit()`
8. Add focus-awareness: disable input when `panelFocus === "panel"`

```tsx
import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { useCommandDispatcher } from "../providers/CommandDispatcherProvider.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "./spinner.js";
import { getCompletions } from "../commands/completion.js";
import type { CompletionEntry } from "../commands/completion.js";
import type { CommandContext } from "../commands/registry.js";
import { uiStore } from "../state/store.js";

export interface ComposerProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSubmit, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const [genFrame, setGenFrame] = useState(0);
  const [completions, setCompletions] = useState<CompletionEntry[]>([]);
  const [completionIdx, setCompletionIdx] = useState(0);
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { colors } = useTheme();
  const dispatcher = useCommandDispatcher();

  const mode       = useUiStore((s) => s.mode);
  const generating = useUiStore((s) => s.generating);
  const panelFocus = useUiStore((s) => s.panelFocus);

  // CommandContext shell — only bridge + getStore needed for completions
  const completionCtx: CommandContext = {
    bridge: globalBridge,
    getStore: () => uiStore.getState(),
    getMemoryRepo: () => { throw new Error("not available in Composer"); },
    getMcpManager: () => { throw new Error("not available in Composer"); },
    getOwlGateway: () => { throw new Error("not available in Composer"); },
  };

  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [generating]);

  // Recompute completions whenever value changes
  useEffect(() => {
    if (!value.startsWith("/")) { setCompletions([]); setCompletionIdx(0); return; }
    let cancelled = false;
    getCompletions(value, completionCtx).then((results) => {
      if (!cancelled) { setCompletions(results); setCompletionIdx(0); }
    });
    return () => { cancelled = true; };
  }, [value]);

  const showPopup = completions.length > 0 && value !== (completions[0]?.value ?? "");

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") { exit(); return; }
      if (key.ctrl && input === "d" && value === "") { exit(); return; }
      if (key.ctrl && input === "l") {
        dispatcher.dispatch("/clear");
        return;
      }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") globalBridge.dismissParliamentView();
        else                       globalBridge.requestParliamentView();
        return;
      }

      // Arrow navigation inside completions popup
      if (showPopup) {
        if (key.upArrow)   { setCompletionIdx((i) => (i - 1 + completions.length) % completions.length); return; }
        if (key.downArrow) { setCompletionIdx((i) => (i + 1) % completions.length); return; }
        if (key.escape)    { setValue(""); return; }
        if (key.tab) {
          const entry = completions[completionIdx];
          if (entry) {
            if (entry.kind === "command") setValue(entry.value);
            else if (entry.kind === "subcommand") setValue(value.replace(/\S+$/, entry.value).trimEnd() + " ");
            else setValue(value.replace(/\S*$/, entry.value) + " ");
          }
          return;
        }
      }

      if (key.return && !key.shift) {
        const trimmed = value.trim();

        // Popup open + Enter → accept selected completion
        if (showPopup && completions.length > 0) {
          const entry = completions[completionIdx];
          if (entry) {
            if (entry.kind === "command") setValue(entry.value + " ");
            else if (entry.kind === "subcommand") setValue(value.replace(/\S+$/, "").trimEnd() + " " + entry.value + " ");
          }
          return;
        }

        // Slash command → dispatch
        if (trimmed.startsWith("/")) {
          dispatcher.dispatch(trimmed).then((result) => {
            if (result.kind === "error") {
              globalBridge.emit({ kind: "notice", source: "command", text: result.text, severity: "error" });
            }
            if (trimmed === "/quit" || trimmed === "/exit" || trimmed === "/bye") exit();
          });
          historyRef.current.push(trimmed);
          setValue("");
          return;
        }

        // AI message
        if (trimmed) { historyRef.current.push(trimmed); onSubmit(trimmed); }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) { setValue((v) => v.slice(0, -1)); return; }

      // Up/down arrow = history navigation when popup NOT open
      if (!showPopup) {
        if (key.upArrow)   { const p = historyRef.current.prev(value); if (p !== null) setValue(p); return; }
        if (key.downArrow) { const n = historyRef.current.next(); setValue(n !== null ? n : ""); return; }
      }

      if (isPasteChunk(input)) { setValue((v) => v + stripPasteMarkers(input)); return; }
      if (!key.ctrl && !key.meta && input.length === 1) { setValue((v) => v + input); return; }
    },
    { isActive: !disabled },
  );

  return (
    <Box flexDirection="column">
      {/* Completions popup */}
      {showPopup && (
        <Box flexDirection="column" borderStyle="round" borderColor={colors.accent} paddingX={1} marginBottom={0}>
          {completions.map((entry, i) => (
            <Box key={entry.value}>
              <Text color={i === completionIdx ? colors.accent : undefined} bold={i === completionIdx}>
                {i === completionIdx ? "❯ " : "  "}
                {entry.value}
              </Text>
              {entry.description && (
                <Text dimColor>{"  " + entry.description.slice(0, 45)}</Text>
              )}
            </Box>
          ))}
        </Box>
      )}

      {/* Main input box */}
      <Box
        flexDirection="column"
        borderStyle="round"
        borderColor={panelFocus === "panel" ? colors.dim : colors.dim}
      >
        {generating ? (
          <Box paddingLeft={1}>
            <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[genFrame]} </Text>
            <Text dimColor>generating...</Text>
          </Box>
        ) : (
          <>
            <Box paddingLeft={1}>
              <Text bold color={panelFocus === "panel" ? colors.dim : colors.user}>❯ </Text>
              <Text color={panelFocus === "panel" ? colors.dim : undefined}>{value}</Text>
              {panelFocus !== "panel" && <Text color={colors.accent}>▋</Text>}
            </Box>
            {value === "" && panelFocus !== "panel" && (
              <Box paddingLeft={1}>
                <Text dimColor>/help · /owls · /sessions · /memory · /skills · /mcp</Text>
              </Box>
            )}
          </>
        )}
      </Box>
    </Box>
  );
}
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "Composer"
```
Fix any errors.

- [ ] **Step 4: Smoke test — completions and dispatch**

```bash
npm run dev
```
Verify:
- Type `/` → popup shows all commands with descriptions
- Type `/me` → filters to `/memory`
- Type `/memory ` → subcommand popup shows list/search/get/...
- Type `/sessions` + Enter → sessions panel opens
- Type `/skills` + Enter → skills panel opens
- Type `/unknown` + Enter → error notice appears
- Type a normal message → AI message sent normally

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/components/Composer.tsx
git commit -m "feat(tui-v2): rewrite Composer with registry-driven completions, Ctrl+L/D, focus bus"
```

---

## Phase 3 — Restore Missing Commands

### Task 9: `/status` and `/clear` handlers

**Files:**
- Create: `src/cli/v2/commands/handlers/status.ts`
- Create: `src/cli/v2/commands/handlers/clear.ts`
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Create `src/cli/v2/commands/handlers/status.ts`**

```typescript
import type { CommandHandler } from "../registry.js";

export const handleStatus: CommandHandler = async (ctx, _args) => {
  const store = ctx.getStore();
  const gateway = ctx.getOwlGateway();
  const config = gateway.getConfig();

  const lines = [
    `Owl:      ${store.activeOwlEmoji} ${store.activeOwlName}`,
    `Model:    ${store.activeModel || config.defaultModel || "unknown"}`,
    `Provider: ${store.activeProvider || "default"}`,
    `Tokens:   ${store.totalTokens.toLocaleString()} (session)`,
    `Cost:     $${store.totalCostUsd.toFixed(4)} (session)`,
    `Context:  ${store.contextWindowPct}% used`,
  ];

  const items = lines.map((line, i) => ({
    id: `status-${i}`,
    label: line,
  }));

  return {
    kind: "panel",
    payload: { title: "/status", items },
  };
};
```

- [ ] **Step 2: Create `src/cli/v2/commands/handlers/clear.ts`**

```typescript
import type { CommandHandler } from "../registry.js";
import { makeMessage, makeSessionId } from "../../../gateway/core.js";

export const handleClear: CommandHandler = async (ctx, _args) => {
  const gateway = ctx.getOwlGateway();
  // gateway.handle() with /reset clears the session context
  await gateway.handle(
    makeMessage({ text: "/reset", sessionId: makeSessionId("cli-v2", "local") }),
  );
  return { kind: "action" };
};
```

> **Note:** `makeMessage` and `makeSessionId` are utility functions in `src/gateway/core.ts`. If they don't exist or have different names, look at the existing `submitMessage()` in CliV2Adapter for the correct pattern to create a GatewayMessage, and use that pattern here.

- [ ] **Step 3: Wire handlers into the registry**

In `src/cli/v2/commands/registry.ts`, add imports at the top:
```typescript
import { handleStatus } from "./handlers/status.js";
import { handleClear }  from "./handlers/clear.js";
```

In the REGISTRY array, replace `handler: notImplemented` for `/status` and `/clear`:
```typescript
{ name: "/status", description: "Show provider, model, and owl info", handler: handleStatus },
{ name: "/clear", aliases: ["/reset"], description: "Clear conversation context", handler: handleClear },
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "handlers/status\|handlers/clear"
```

- [ ] **Step 5: Smoke test**

```bash
npm run dev
```
Type `/status` → panel shows owl/model/tokens. Type `/clear` → context clears (verify by checking if prior messages are gone in next AI response).

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/commands/handlers/status.ts \
        src/cli/v2/commands/handlers/clear.ts \
        src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): restore /status and /clear commands"
```

---

### Task 10: `/memory` handler with dynamic key completion

**Files:**
- Create: `src/cli/v2/commands/handlers/memory.ts`
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Create `src/cli/v2/commands/handlers/memory.ts`**

```typescript
import type { CommandHandler, CommandContext, SubcommandSpec } from "../registry.js";
import { dispatchMemoryCommand } from "../../../gateway/commands/memory-router.js";

async function getDeps(ctx: CommandContext) {
  return { repo: ctx.getMemoryRepo() };
}

// Dynamic completer — returns actual memory IDs for /memory get <Tab> etc.
export async function completeMemoryKeys(ctx: CommandContext, partial: string): Promise<string[]> {
  const deps = await getDeps(ctx);
  const records = await deps.repo.search("", { topK: 50 });
  return records.map((r: { id: string }) => r.id).filter((id: string) => id.startsWith(partial));
}

function textToItems(text: string): Array<{ id: string; label: string }> {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line, i) => ({ id: `line-${i}`, label: line }));
}

export const handleMemoryList: CommandHandler = async (ctx, _args) => {
  const deps = await getDeps(ctx);
  const text = await dispatchMemoryCommand("list", [], deps);
  const lines = text.split("\n").filter((l) => l.trim());

  // First line is "N memories:" header; rest are items
  const headerLine = lines[0] ?? "";
  const itemLines = lines.slice(1);

  const items = itemLines.map((line, i) => {
    // Line format: "  [kind] id — content"
    const match = line.match(/\[(\w+)\]\s+(\S+)\s+—\s+(.*)/);
    return match
      ? { id: `mem-${i}`, label: match[2]!, meta: `[${match[1]}] ${match[3]!.slice(0, 50)}`, data: { rawId: match[2] } }
      : { id: `mem-${i}`, label: line.trim() };
  });

  return {
    kind: "panel",
    payload: {
      title: `/memory list — ${headerLine.trim()}`,
      items,
      emptyText: "No memories stored yet.",
    },
  };
};

export const handleMemorySearch: CommandHandler = async (ctx, args) => {
  const deps = await getDeps(ctx);
  const query = args.join(" ");
  if (!query) return { kind: "error", text: "Usage: /memory search <query>" };
  const text = await dispatchMemoryCommand("search", args, deps);
  return { kind: "panel", payload: { title: `/memory search "${query}"`, items: textToItems(text) } };
};

export const handleMemoryGet: CommandHandler = async (ctx, args) => {
  const deps = await getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory get <key>" };
  const text = await dispatchMemoryCommand("get", args, deps);
  return { kind: "panel", payload: { title: `/memory get ${id}`, items: textToItems(text) } };
};

export const handleMemoryInvalidate: CommandHandler = async (ctx, args) => {
  const deps = await getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory invalidate <key>" };
  const text = await dispatchMemoryCommand("invalidate", args, deps);
  return { kind: "system-message", text };
};

export const handleMemoryStats: CommandHandler = async (ctx, _args) => {
  const deps = await getDeps(ctx);
  const text = await dispatchMemoryCommand("stats", [], deps);
  return { kind: "panel", payload: { title: "/memory stats", items: textToItems(text) } };
};

export const handleMemoryHistory: CommandHandler = async (ctx, args) => {
  const deps = await getDeps(ctx);
  const id = args[0];
  if (!id) return { kind: "error", text: "Usage: /memory history <id>" };
  const text = await dispatchMemoryCommand("history", args, deps);
  return { kind: "panel", payload: { title: `/memory history ${id}`, items: textToItems(text) } };
};

export const handleMemoryExport: CommandHandler = async (ctx, _args) => {
  const deps = await getDeps(ctx);
  const text = await dispatchMemoryCommand("export", [], deps);
  return { kind: "panel", payload: { title: "/memory export", items: textToItems(text) } };
};
```

- [ ] **Step 2: Wire into registry**

In `src/cli/v2/commands/registry.ts`, add imports:
```typescript
import {
  handleMemoryList, handleMemorySearch, handleMemoryGet,
  handleMemoryInvalidate, handleMemoryStats, handleMemoryHistory,
  handleMemoryExport, completeMemoryKeys,
} from "./handlers/memory.js";
```

Replace the `/memory` entry in REGISTRY:
```typescript
{
  name: "/memory",
  aliases: ["/mem"],
  description: "View and manage memory",
  subcommands: [
    { name: "list",       description: "List all memory entries",              handler: handleMemoryList },
    { name: "search",     description: "Search memory",  args: [{ name: "<query>" }], handler: handleMemorySearch },
    { name: "get",        description: "Show one entry", args: [{ name: "<key>" }],   handler: handleMemoryGet, complete: completeMemoryKeys },
    { name: "invalidate", description: "Delete an entry",args: [{ name: "<key>" }],   handler: handleMemoryInvalidate, complete: completeMemoryKeys },
    { name: "stats",      description: "Memory statistics",                    handler: handleMemoryStats },
    { name: "history",    description: "View invalidation history", args: [{ name: "<id>" }], handler: handleMemoryHistory, complete: completeMemoryKeys },
    { name: "export",     description: "JSON dump of all valid memories",      handler: handleMemoryExport },
  ],
  handler: handleMemoryList,  // bare /memory → list
},
```

- [ ] **Step 3: TypeScript check + smoke test**

```bash
npx tsc --noEmit 2>&1 | grep "handlers/memory"
npm run dev
# Type: /memory list → panel with memory entries
# Type: /memory get <Tab> → dynamic key completions
```

- [ ] **Step 4: Commit**

```bash
git add src/cli/v2/commands/handlers/memory.ts src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): restore /memory with all subcommands + dynamic key completion"
```

---

### Task 11: `/mcp` full handler

**Files:**
- Create: `src/cli/v2/commands/handlers/mcp.ts`
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Create `src/cli/v2/commands/handlers/mcp.ts`**

```typescript
import type { CommandHandler, CommandContext } from "../registry.js";
import { McpCommandRouter } from "../../../gateway/commands/mcp-router.js";

function getDeps(ctx: CommandContext) {
  const gateway = ctx.getOwlGateway();
  return {
    mcpManager: gateway.getMcpManager(),
    toolRegistry: gateway.getToolRegistry(),
    config: gateway.getConfig(),
    basePath: (gateway as any).ctx?.basePath ?? process.cwd(),
    saveConfig: async () => {},  // config persistence handled by gateway — no-op here
  };
}

export async function completeMcpServers(ctx: CommandContext, partial: string): Promise<string[]> {
  const { mcpManager } = getDeps(ctx);
  return mcpManager.listServers()
    .map((s: { name: string }) => s.name)
    .filter((n: string) => n.startsWith(partial));
}

function textToItems(text: string): Array<{ id: string; label: string }> {
  return text.split("\n").filter((l) => l.trim()).map((line, i) => ({ id: `line-${i}`, label: line }));
}

export const handleMcpList: CommandHandler = async (ctx, _args) => {
  const text = await McpCommandRouter.dispatch("list", [], getDeps(ctx));
  const { mcpServers } = ctx.getStore();
  const items = mcpServers.map((s) => ({
    id: s.name,
    label: s.name,
    meta: `${s.connected ? "● connected" : "○ disconnected"}  ${s.toolCount} tool${s.toolCount !== 1 ? "s" : ""}  ${s.transport}`,
    data: s,
  }));
  return { kind: "panel", payload: { title: "/mcp list", items, emptyText: text } };
};

export const handleMcpStatus: CommandHandler = async (ctx, _args) => {
  const text = await McpCommandRouter.dispatch("status", [], getDeps(ctx));
  return { kind: "panel", payload: { title: "/mcp status", items: textToItems(text) } };
};

export const handleMcpAdd: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp add <package> [args…]" };
  const text = await McpCommandRouter.dispatch("add", args, getDeps(ctx));
  return { kind: "system-message", text };
};

export const handleMcpRemove: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp remove <server-name>" };
  const text = await McpCommandRouter.dispatch("remove", args, getDeps(ctx));
  return { kind: "system-message", text };
};

export const handleMcpEnable: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp enable <server-name>" };
  const text = await McpCommandRouter.dispatch("enable", args, getDeps(ctx));
  return { kind: "system-message", text };
};

export const handleMcpDisable: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp disable <server-name>" };
  const text = await McpCommandRouter.dispatch("disable", args, getDeps(ctx));
  return { kind: "system-message", text };
};

export const handleMcpTools: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp tools <server-name>" };
  const text = await McpCommandRouter.dispatch("tools", args, getDeps(ctx));
  return { kind: "panel", payload: { title: `/mcp tools ${args[0]}`, items: textToItems(text) } };
};

export const handleMcpReconnect: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp reconnect <server-name>" };
  const text = await McpCommandRouter.dispatch("reconnect", args, getDeps(ctx));
  return { kind: "system-message", text };
};
```

- [ ] **Step 2: Wire into registry**

```typescript
import {
  handleMcpList, handleMcpStatus, handleMcpAdd, handleMcpRemove,
  handleMcpEnable, handleMcpDisable, handleMcpTools, handleMcpReconnect,
  completeMcpServers,
} from "./handlers/mcp.js";
```

Replace `/mcp` subcommand handlers in REGISTRY:
```typescript
subcommands: [
  { name: "list",      description: "List configured servers",   handler: handleMcpList },
  { name: "status",    description: "Full status report",        handler: handleMcpStatus },
  { name: "add",       description: "Install a server", args: [{ name: "<package>" }], handler: handleMcpAdd },
  { name: "remove",    description: "Remove a server",  args: [{ name: "<name>" }],    handler: handleMcpRemove, complete: completeMcpServers },
  { name: "enable",    description: "Enable a server",  args: [{ name: "<name>" }],    handler: handleMcpEnable, complete: completeMcpServers },
  { name: "disable",   description: "Disable a server", args: [{ name: "<name>" }],    handler: handleMcpDisable, complete: completeMcpServers },
  { name: "tools",     description: "List server tools",args: [{ name: "<name>" }],    handler: handleMcpTools, complete: completeMcpServers },
  { name: "reconnect", description: "Reconnect server", args: [{ name: "<name>" }],    handler: handleMcpReconnect, complete: completeMcpServers },
],
handler: handleMcpList,
```

- [ ] **Step 3: TypeScript check + commit**

```bash
npx tsc --noEmit 2>&1 | grep "handlers/mcp"
git add src/cli/v2/commands/handlers/mcp.ts src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): restore /mcp with all subcommands + dynamic server completion"
```

---

### Task 12: `/capabilities`, `/learning`, `/owl`, `/onboarding`, `/help` handlers

**Files:**
- Create: `src/cli/v2/commands/handlers/capabilities.ts`
- Create: `src/cli/v2/commands/handlers/learning.ts`
- Create: `src/cli/v2/commands/handlers/owl.ts`
- Create: `src/cli/v2/commands/handlers/onboarding.ts`
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Create `src/cli/v2/commands/handlers/capabilities.ts`**

```typescript
import type { CommandHandler } from "../registry.js";

export const handleCapabilities: CommandHandler = async (ctx, _args) => {
  const evolution = ctx.getOwlGateway().getEvolution();
  // getEvolution() returns the OwlEvolution instance.
  // Call its method to get synthesized capabilities as text.
  // The v1 handler in commands.ts uses: evolution.getSynthesizedCapabilities() or similar.
  // Check the actual method by reading src/cognition/owl-evolution.ts if needed.
  const text: string = await (evolution as any).formatCapabilities?.()
    ?? "Capabilities report unavailable.";
  const items = text.split("\n").filter((l: string) => l.trim()).map((line: string, i: number) => ({
    id: `cap-${i}`,
    label: line,
  }));
  return { kind: "panel", payload: { title: "/capabilities", items } };
};
```

- [ ] **Step 2: Create `src/cli/v2/commands/handlers/learning.ts`**

```typescript
import type { CommandHandler } from "../registry.js";

export const handleLearning: CommandHandler = async (ctx, _args) => {
  const learner = ctx.getOwlGateway().getLearningOrchestrator();
  const text: string = await (learner as any).formatReport?.()
    ?? "Learning report unavailable.";
  const items = text.split("\n").filter((l: string) => l.trim()).map((line: string, i: number) => ({
    id: `learn-${i}`,
    label: line,
  }));
  return { kind: "panel", payload: { title: "/learning", items } };
};
```

> **Note for implementer:** If `getEvolution()` or `getLearningOrchestrator()` don't have `formatCapabilities()` / `formatReport()` methods, find the correct method names by reading `src/cognition/` and `src/learning/` directories, then update these handlers accordingly.

- [ ] **Step 3: Create `src/cli/v2/commands/handlers/owl.ts`**

```typescript
import type { CommandHandler } from "../registry.js";

export const handleOwlStatus: CommandHandler = async (ctx, _args) => {
  const store = ctx.getStore();
  const items = [
    { id: "name",     label: `Name:   ${store.activeOwlEmoji} ${store.activeOwlName}` },
    { id: "model",    label: `Model:  ${store.activeModel}` },
    { id: "provider", label: `Provider: ${store.activeProvider}` },
    { id: "tokens",   label: `Tokens: ${store.totalTokens.toLocaleString()} this session` },
    { id: "cost",     label: `Cost:   $${store.totalCostUsd.toFixed(4)} this session` },
  ];
  return { kind: "panel", payload: { title: "/owl status", items } };
};
```

- [ ] **Step 4: Create `src/cli/v2/commands/handlers/onboarding.ts`**

```typescript
import type { CommandHandler } from "../registry.js";

export const handleOnboarding: CommandHandler = async (ctx, _args) => {
  ctx.bridge.requestOnboardingView();
  return { kind: "action" };
};
```

- [ ] **Step 5: Build `/help` auto-generated from registry**

Add a `buildHelpItems` function at the top of `src/cli/v2/commands/registry.ts` (before REGISTRY declaration) and update the `/help` handler:

```typescript
function buildHelpItems(): Array<{ id: string; label: string; meta: string }> {
  return REGISTRY.map((spec) => ({
    id: spec.name,
    label: spec.name + (spec.aliases?.length ? `  (${spec.aliases.join(", ")})` : ""),
    meta: spec.description,
  }));
}

// Then in REGISTRY:
{
  name: "/help",
  aliases: ["/?"],
  description: "Show available commands",
  handler: async (_ctx, _args) => ({
    kind: "panel",
    payload: {
      title: "/help — available commands",
      items: buildHelpItems(),
    },
  }),
},
```

Note: `buildHelpItems()` references `REGISTRY` which is declared below. Move the `/help` handler to be a function defined after the REGISTRY constant, and replace `handler: notImplemented` in the REGISTRY array with the real handler afterwards. Or, use a closure that captures REGISTRY lazily:

```typescript
// In REGISTRY array:
{
  name: "/help",
  aliases: ["/?"],
  description: "Show available commands",
  handler: async (_ctx, _args) => ({
    kind: "panel" as const,
    payload: {
      title: "/help",
      items: REGISTRY.map((spec) => ({
        id: spec.name,
        label: spec.name + (spec.aliases?.length ? `  (${spec.aliases.join(", ")})` : ""),
        meta: spec.description,
      })),
    },
  }),
},
```

- [ ] **Step 6: Wire all new handlers into registry**

```typescript
import { handleCapabilities } from "./handlers/capabilities.js";
import { handleLearning }     from "./handlers/learning.js";
import { handleOwlStatus }    from "./handlers/owl.js";
import { handleOnboarding }   from "./handlers/onboarding.js";
```

Replace `handler: notImplemented` in REGISTRY for:
- `/capabilities` → `handler: handleCapabilities`
- `/learning` → `handler: handleLearning`
- `/owl` → `subcommands: [{ name: "status", ..., handler: handleOwlStatus }], handler: handleOwlStatus`
- `/onboarding` → `handler: handleOnboarding`

- [ ] **Step 7: TypeScript check + commit**

```bash
npx tsc --noEmit 2>&1 | head -30
git add src/cli/v2/commands/handlers/capabilities.ts \
        src/cli/v2/commands/handlers/learning.ts \
        src/cli/v2/commands/handlers/owl.ts \
        src/cli/v2/commands/handlers/onboarding.ts \
        src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): restore /capabilities, /learning, /owl, /onboarding, auto-build /help"
```

---

### Task 13: `/helper` handler

**Files:**
- Create: `src/cli/v2/commands/handlers/helper.ts`
- Modify: `src/cli/v2/commands/registry.ts`

- [ ] **Step 1: Find the owl-router API**

```bash
grep -n "export\|async function\|function " src/gateway/commands/owl-router.ts | head -30
```

Note the exported function/class name and signature. Then create the handler accordingly.

- [ ] **Step 2: Create `src/cli/v2/commands/handlers/helper.ts`**

The exact implementation depends on the owl-router API (check in step 1). The general shape is:

```typescript
import type { CommandHandler, CommandContext } from "../registry.js";

// Import the owl-router handler — check src/gateway/commands/owl-router.ts for exact name
// import { dispatchOwlCommand } from "../../../gateway/commands/owl-router.js";

export async function completeHelperNames(ctx: CommandContext, partial: string): Promise<string[]> {
  const gateway = ctx.getOwlGateway();
  const registry = gateway.getSpecializedRegistry();
  // getSpecializedRegistry() returns OwlRegistry or similar
  // Adjust if the method name is different
  const owls: Array<{ name: string }> = (registry as any).list?.() ?? [];
  return owls.map((o) => o.name).filter((n: string) => n.startsWith(partial));
}

export const handleHelperList: CommandHandler = async (ctx, _args) => {
  // Adapt to match actual owl-router API
  const gateway = ctx.getOwlGateway();
  const registry = await gateway.getOwlRegistry();
  const owls = registry.getAll ? registry.getAll() : [];
  const items = owls.map((o: { name: string; emoji?: string; description?: string }) => ({
    id: o.name,
    label: `${o.emoji ?? "🦉"} ${o.name}`,
    meta: (o.description ?? "").slice(0, 50),
    data: o,
  }));
  return { kind: "panel", payload: { title: "/helper list", items, emptyText: "No helpers configured." } };
};

export const handleHelperShow: CommandHandler = async (ctx, args) => {
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /helper show <name>" };
  const gateway = ctx.getOwlGateway();
  const registry = await gateway.getOwlRegistry();
  const owl = (registry as any).get?.(name);
  if (!owl) return { kind: "error", text: `Helper "${name}" not found.` };
  const items = [
    { id: "name",  label: `Name: ${owl.name}` },
    { id: "emoji", label: `Emoji: ${owl.emoji ?? "🦉"}` },
    { id: "desc",  label: `Desc: ${owl.description ?? ""}` },
  ];
  return { kind: "panel", payload: { title: `/helper show ${name}`, items } };
};

export const handleHelperCreate: CommandHandler = async (_ctx, _args) => {
  // Trigger the SkillWizardScreen (helper creation wizard)
  // The wizard is at mode "skill-wizard" — same OnboardingScreen pattern
  _ctx.bridge.emit({ kind: "onboarding.view.requested" } as any); // adjust event if needed
  return { kind: "action" };
};

export const handleHelperDelete: CommandHandler = async (ctx, args) => {
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /helper delete <name>" };
  // Adapt to actual API
  return { kind: "system-message", text: `Deleted helper "${name}". (implement via owl-router)` };
};
```

> **Note for implementer:** The exact API for listing/creating/deleting helpers depends on `src/gateway/commands/owl-router.ts`. Read that file and adapt the handler to call the correct functions. The pattern above shows the structure; the method names may differ.

- [ ] **Step 3: Wire into registry + typecheck + commit**

```typescript
import {
  handleHelperList, handleHelperShow, handleHelperCreate, handleHelperDelete,
  completeHelperNames,
} from "./handlers/helper.js";
```

```bash
npx tsc --noEmit 2>&1 | grep "handlers/helper"
git add src/cli/v2/commands/handlers/helper.ts src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): restore /helper command (list/show/create/delete)"
```

---

## Phase 4 — Interactive Panel Actions

### Task 14: In-panel actions for `/memory list`

**Files:**
- Modify: `src/cli/v2/commands/handlers/memory.ts`

- [ ] **Step 1: Update `handleMemoryList` to include actions**

In `src/cli/v2/commands/handlers/memory.ts`, update `handleMemoryList` to include actions in the returned payload:

```typescript
export const handleMemoryList: CommandHandler = async (ctx, _args) => {
  const deps = await getDeps(ctx);
  const text = await dispatchMemoryCommand("list", [], deps);
  const lines = text.split("\n").filter((l) => l.trim());
  const headerLine = lines[0] ?? "";
  const itemLines = lines.slice(1);

  const items = itemLines.map((line, i) => {
    const match = line.match(/\[(\w+)\]\s+(\S+)\s+—\s+(.*)/);
    return match
      ? { id: `mem-${i}`, label: match[2]!, meta: `[${match[1]}] ${match[3]!.slice(0, 50)}`, data: { rawId: match[2] } }
      : { id: `mem-${i}`, label: line.trim() };
  });

  const makeActions = (ctxRef: typeof ctx) => [
    {
      key: "g",
      label: "get",
      handler: async (item: { id: string; label: string; data?: unknown }) => {
        const rawId = (item.data as any)?.rawId ?? item.label;
        const result = await handleMemoryGet(ctxRef, [rawId]);
        if (result.kind === "panel") ctxRef.bridge.openPanel("memory-get", result.payload);
      },
    },
    {
      key: "d",
      label: "invalidate",
      confirm: "yes",
      destructive: true,
      handler: async (item: { id: string; label: string; data?: unknown }) => {
        const rawId = (item.data as any)?.rawId ?? item.label;
        await handleMemoryInvalidate(ctxRef, [rawId, "deleted via /memory list"]);
      },
    },
  ];

  return {
    kind: "panel",
    payload: {
      title: `/memory list — ${headerLine.trim()}`,
      items,
      actions: makeActions(ctx),
      emptyText: "No memories stored yet.",
    },
  };
};
```

- [ ] **Step 2: TypeScript check + smoke test**

```bash
npx tsc --noEmit 2>&1 | grep "handlers/memory"
npm run dev
# /memory list → panel opens
# Arrow nav works
# Press 'g' on an entry → shows memory content in nested panel
# Press 'd' on an entry → "Type 'yes' to confirm" prompt appears
# Type 'yes' + Enter → entry invalidated
# Esc → cancel without deleting
```

- [ ] **Step 3: Commit**

```bash
git add src/cli/v2/commands/handlers/memory.ts
git commit -m "feat(tui-v2): add in-panel actions to /memory list (g get, d invalidate with confirm)"
```

---

### Task 15: In-panel actions for `/mcp list`, `/sessions`, `/owls`

**Files:**
- Modify: `src/cli/v2/commands/handlers/mcp.ts`
- Modify: `src/cli/v2/commands/registry.ts` (sessions + owls handlers inline)

- [ ] **Step 1: Add actions to `handleMcpList` in `mcp.ts`**

Update `handleMcpList` to include reconnect + remove actions:

```typescript
export const handleMcpList: CommandHandler = async (ctx, _args) => {
  const { mcpServers } = ctx.getStore();
  const items = mcpServers.map((s) => ({
    id: s.name,
    label: s.name,
    meta: `${s.connected ? "● connected" : "○ disconnected"}  ${s.toolCount} tool${s.toolCount !== 1 ? "s" : ""}  ${s.transport}`,
    data: s,
  }));

  const actions = [
    {
      key: "t",
      label: "tools",
      handler: async (item: { id: string }) => {
        const result = await handleMcpTools(ctx, [item.id]);
        if (result.kind === "panel") ctx.bridge.openPanel("mcp-tools", result.payload);
      },
    },
    {
      key: "r",
      label: "reconnect",
      handler: async (item: { id: string }) => {
        await handleMcpReconnect(ctx, [item.id]);
      },
    },
    {
      key: "d",
      label: "remove",
      confirm: "yes",
      destructive: true,
      handler: async (item: { id: string }) => {
        await handleMcpRemove(ctx, [item.id]);
      },
    },
  ];

  return {
    kind: "panel",
    payload: { title: "/mcp list", items, actions, emptyText: "No MCP servers configured." },
  };
};
```

- [ ] **Step 2: Add resume + delete actions to `/sessions` handler in `registry.ts`**

Update the `/sessions` handler in REGISTRY:

```typescript
handler: async (ctx) => {
  const { recentSessions } = ctx.getStore();
  const items = recentSessions.map((s) => ({
    id: s.sessionId,
    label: s.title || s.sessionId.slice(0, 24),
    meta: new Date(s.lastActiveAt).toLocaleDateString(),
    data: s,
  }));

  const actions = [
    {
      key: "return",
      label: "resume",
      handler: async (item: { id: string; label: string }) => {
        ctx.bridge.emit({ kind: "sessions.view.dismissed" });
        // The adapter's resumeSession is not accessible here.
        // Emit a custom event that cli-v2 adapter listens to,
        // OR dispatch through a bridge method.
        // For now: emit a notice and close panel.
        ctx.bridge.emit({ kind: "notice", source: "command", text: `Session resume: use /sessions in the sessions screen for now.`, severity: "info" });
        ctx.bridge.closePanel();
      },
    },
  ];

  return {
    kind: "panel",
    payload: { title: "/sessions", items, actions, emptyText: "No sessions yet." },
  };
},
```

> **Note:** Full session resume from a panel requires wiring the `onResume` callback into `CommandContext`. This can be done in a follow-up — add `onResume?: (sessionId: string, title: string) => void` to `CommandContext` and populate it in `createCommandDispatcher`. For this task, the panel opens and shows sessions; pressing Enter shows a notice.

- [ ] **Step 3: Add evolve action to `/owls` handler**

```typescript
handler: async (ctx) => {
  const { owls } = ctx.getStore();
  const items = owls.map((o) => ({
    id: o.name,
    label: `${o.emoji} ${o.name}`,
    meta: o.isActive ? "active" : o.description.slice(0, 40),
    data: o,
  }));

  const actions = [
    {
      key: "return",
      label: "switch",
      handler: async (item: { id: string; label: string; data?: unknown }) => {
        const owlData = item.data as { name: string; emoji: string };
        ctx.bridge.changeOwl(owlData.name, owlData.emoji);
        ctx.bridge.closePanel();
      },
    },
    {
      key: "e",
      label: "evolve",
      handler: async (item: { id: string }) => {
        ctx.bridge.emit({ kind: "notice", source: "command", text: `Evolving owl ${item.id}…`, severity: "info" });
        // Trigger evolution async
        const gateway = ctx.getOwlGateway();
        const evolution = gateway.getEvolution();
        (evolution as any).evolveOwl?.(item.id).catch(console.error);
      },
    },
  ];

  return {
    kind: "panel",
    payload: { title: "/owls", items, actions, emptyText: "No owls loaded." },
  };
},
```

- [ ] **Step 4: TypeScript check + smoke test + commit**

```bash
npx tsc --noEmit 2>&1 | head -20
npm run dev
# /mcp list → panel with t/r/d actions
# /owls → panel with Enter (switch) and e (evolve)
# /sessions → panel shows sessions

git add src/cli/v2/commands/handlers/mcp.ts src/cli/v2/commands/registry.ts
git commit -m "feat(tui-v2): add in-panel actions to /mcp, /sessions, /owls"
```

---

## Phase 5 — ShortcutsBar + Remaining Keys

### Task 16: `ShortcutsBar.tsx`

**Files:**
- Create: `src/cli/v2/components/ShortcutsBar.tsx`
- Modify: `src/cli/v2/screens/ChatScreen.tsx`

- [ ] **Step 1: Create `src/cli/v2/components/ShortcutsBar.tsx`**

```tsx
import { Box, Text } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { useTheme } from "../providers/ThemeProvider.js";
import type { PanelAction } from "../panels/Panel.js";

export function ShortcutsBar() {
  const { colors } = useTheme();
  const generating = useUiStore((s) => s.generating);
  const panelFocus = useUiStore((s) => s.panelFocus);
  const activePanel = useUiStore((s) => s.activePanel);
  const showHelp   = useUiStore((s) => s.showHelp);

  let hints: string;

  if (generating) {
    hints = "Esc stop generation";
  } else if (panelFocus === "panel") {
    const actions = ((activePanel?.props as any)?.actions ?? []) as PanelAction[];
    const actionHints = actions.map((a) => `${a.key === "return" ? "Enter" : a.key} ${a.label}`).join("  ·  ");
    hints = ["↑↓ nav", actionHints, "Esc close"].filter(Boolean).join("  ·  ");
  } else if (showHelp) {
    hints = "Esc close  ·  ↑↓ navigate  ·  Enter select";
  } else {
    hints = "Esc stop  ·  ^P parliament  ·  ^L clear  ·  ^C quit";
  }

  return (
    <Box paddingLeft={1}>
      <Text dimColor>{hints}</Text>
    </Box>
  );
}
```

- [ ] **Step 2: Add `<ShortcutsBar />` to ChatScreen**

In `src/cli/v2/screens/ChatScreen.tsx`, import and add below `<StatusBar />`:

```tsx
import { ShortcutsBar } from "../components/ShortcutsBar.js";

// In JSX, after <StatusBar />:
<StatusBar />
<ShortcutsBar />
```

- [ ] **Step 3: TypeScript check + smoke test**

```bash
npx tsc --noEmit 2>&1 | grep "ShortcutsBar"
npm run dev
# Verify: ShortcutsBar shows at bottom
# Open /memory list → bar changes to ↑↓ nav · g get · d invalidate · Esc close
# Close panel → bar reverts to Esc stop · ^P parliament · ^L clear · ^C quit
# Start generation → bar shows "Esc stop generation"
```

- [ ] **Step 4: Commit**

```bash
git add src/cli/v2/components/ShortcutsBar.tsx src/cli/v2/screens/ChatScreen.tsx
git commit -m "feat(tui-v2): restore ShortcutsBar — context-aware hint line"
```

---

## Phase 6 — Polish + Tests

### Task 17: Test suite + typecheck + lint

**Files:**
- Modify: `__tests__/cli/v2/state/panel.test.ts` (expand)
- Modify: `__tests__/cli/v2/commands/registry.test.ts` (expand)

- [ ] **Step 1: Expand panel slice tests**

Add to `__tests__/cli/v2/state/panel.test.ts`:

```typescript
it("opening a second panel replaces the first", () => {
  let state = baseState();
  state = applyPanelEvent(state, { kind: "panel.opened", id: "skills", props: {} });
  state = applyPanelEvent(state, { kind: "panel.opened", id: "memory", props: {} });
  expect(state.activePanel!.id).toBe("memory");
  expect(state.panelFocus).toBe("panel");
});

it("closing a panel that was not open is a no-op", () => {
  const state = baseState();
  const next = applyPanelEvent(state, { kind: "panel.closed" });
  expect(next.activePanel).toBeNull();
  expect(next.panelFocus).toBe("composer");
});
```

- [ ] **Step 2: Expand registry tests**

Add to `__tests__/cli/v2/commands/registry.test.ts`:

```typescript
it("resolves /mem as alias for /memory", () => {
  const result = resolveCommand("/mem list");
  expect(result).not.toBeNull();
  expect(result!.spec.name).toBe("/memory");
  expect(result!.subcommand?.name).toBe("list");
});

it("resolves bare /memory to no subcommand with empty args", () => {
  const result = resolveCommand("/memory");
  expect(result!.subcommand).toBeUndefined();
  expect(result!.args).toHaveLength(0);
});

it("resolves /memory get with args", () => {
  const result = resolveCommand("/memory get user_role.md");
  expect(result!.subcommand?.name).toBe("get");
  expect(result!.args).toEqual(["user_role.md"]);
});

it("resolves /help via alias /?", () => {
  const result = resolveCommand("/?");
  expect(result!.spec.name).toBe("/help");
});
```

- [ ] **Step 3: Run full test suite**

```bash
npm test 2>&1 | tail -30
```
Expected: all tests pass. Fix any failures.

- [ ] **Step 4: Full TypeScript typecheck**

```bash
npx tsc --noEmit 2>&1
```
Expected: zero errors. Fix all type errors before committing.

- [ ] **Step 5: Lint**

```bash
npm run lint 2>&1
```
Fix any lint issues.

- [ ] **Step 6: Final smoke test checklist**

```bash
npm run dev
```

Run through this checklist manually:
- [ ] Type `/` → popup shows ≥13 commands with descriptions
- [ ] Type `/me` → filters to `/memory`
- [ ] Type `/memory ` → subcommand popup: list/search/get/invalidate/stats/history/export
- [ ] Type `/memory get ` then Tab → dynamic key list appears (if memories exist)
- [ ] `/memory list` → panel opens, ↑↓ nav, `g` get, `d` invalidate (confirm prompt), Esc dismiss
- [ ] `/skills` → panel, scroll, Esc dismiss
- [ ] `/mcp` (or `/mcp list`) → panel, `t` tools, `r` reconnect, `d` remove
- [ ] `/status` → read-only panel with owl/model/tokens
- [ ] `/clear` (or Ctrl+L) → context cleared
- [ ] `/owls` → panel, Enter switch owl, `e` evolve
- [ ] `/sessions` → panel with sessions list
- [ ] `/help` (or `/?`) → panel with all commands listed
- [ ] `/unknown` → error notice inline, no crash
- [ ] ShortcutsBar: idle → "Esc stop · ^P parliament · ^L clear · ^C quit"
- [ ] ShortcutsBar: panel open → shows panel actions
- [ ] ShortcutsBar: generating → "Esc stop generation"
- [ ] Ctrl+D on empty composer → exits
- [ ] Mouse wheel scrolls terminal history while panel is open

- [ ] **Step 7: Final commit**

```bash
git add __tests__/cli/v2/state/panel.test.ts __tests__/cli/v2/commands/registry.test.ts
git commit -m "test(tui-v2): expand panel slice + registry tests"

git add -A
git commit -m "chore(tui-v2): final polish — typecheck, lint, test suite green"
```

---

## Quick reference — event kinds added

| Event kind | Triggered by | Effect |
|---|---|---|
| `panel.opened` | `bridge.openPanel(id, props)` | Sets `activePanel`, focus → `"panel"` |
| `panel.closed` | `bridge.closePanel()` | Clears `activePanel`, focus → `"composer"` |
| `onboarding.view.requested` | `bridge.requestOnboardingView()` | `mode → "onboarding"` |
| `onboarding.view.dismissed` | `bridge.dismissOnboardingView()` | `mode → "chat"` |

## Quick reference — new store fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `activePanel` | `{ id: string; props: unknown } \| null` | `null` | Currently open inline panel |
| `panelFocus` | `"composer" \| "panel"` | `"composer"` | Which surface holds keyboard focus |
