# TUI v2 Layout & Panel UX Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three TUI v2 UX gaps — pinned header, panel-free command results, and broken wizard flow — making all command output appear inline in the transcript with no Esc required.

**Architecture:** Three independent phases, each touching a small surface. Phase 1 (layout) is a pure Ink constraint fix. Phase 2 (panel→inline) rewires command return types and adds `/owl switch`. Phase 3 (wizard) diagnoses and fixes the `/owl create` / `/owl from-bmad` silent-failure path.

**Tech Stack:** Ink (Yoga flexbox), Zustand (uiStore), TypeScript, `node:child_process` (for Phase 3 debug).

---

## Background: What Is Broken

### B1 — Header eviction (scrolls away)

`ChatScreen.tsx` wraps the middle region in two nested `flexGrow={1}` boxes:

```
<Box flexGrow={1} flexShrink={1} overflow="hidden">   ← outer
  <Box flexGrow={1} flexShrink={1} overflow="hidden">  ← inner
    <Transcript />   ← unconstrained natural height
```

As committed turns accumulate, Transcript's natural height grows. Yoga propagates that growth upward through the two nested flexGrow layers. The outer container expands past `height={rows}`, evicting the Header. Ink clips the top-level box from the **bottom**, so only the Header's single top green rule survives. All logo rows fall off-screen.

### B2 — Panel steals Composer focus

Commands like `/owl list` return `{ kind: "panel" }`. The dispatcher opens a Panel. `panelFocus` becomes `"panel"`. `Composer` receives `disabled={panelFocus === "panel"}`, which sets `isActive: false` on `useInput`. The user must press Esc to pop the panel and restore Composer focus. No auto-dismiss exists.

### B3 — `/owl create` and `/owl from-bmad` are silent

Both commands use a `bridgeAdapter.ask()` → `bridge.prompt()` flow that emits `prompt.requested` to display a question in the Composer. The flow appears to hang before the first `prompt.requested` fires — the user sees neither a question nor an error. Root cause is unconfirmed; Phase 3 adds diagnostics first, then fixes.

---

## Phase 1 — Pin the Header

**Single file:** `src/cli/v2/screens/ChatScreen.tsx`

### Constants (already defined, keep as-is)

```
HEADER_ROWS = 9    // 2 border rules + 6 logo lines + 1 tagline
```

Add two new constants:

```typescript
const COMPOSER_MIN_ROWS = 3;  // border-top + input row + border-bottom
const SHORTCUTS_ROWS    = 1;  // "use Shift+Tab to change current owl"
```

### Layout change

Replace the middle region Box:

```tsx
// BEFORE
<Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">
  ...
  <Box flexDirection="column" paddingX={2} flexGrow={1} flexShrink={1} overflow="hidden">

// AFTER
const contentRows = Math.max(4, rows - HEADER_ROWS - COMPOSER_MIN_ROWS - SHORTCUTS_ROWS);

<Box flexDirection="column" height={contentRows} overflow="hidden">
  ...
  <Box flexDirection="column" paddingX={2} overflow="hidden">
```

`height={contentRows}` gives Yoga a hard upper bound. `overflow="hidden"` clips excess content at the bottom of the region. The inner Box no longer needs `flexGrow` because its parent has an explicit height.

When the Composer grows (completions popup or prompt question appears), the outer Box still has `height={rows}` and the Composer box has no explicit height, so Yoga shrinks `contentRows` naturally — the explicit `height` on the inner box becomes a cap, not a floor.

### Verification

After this change: run the app, send 20+ messages, confirm the STACKOWL logo and tagline never scroll off. Resize the terminal; confirm the header stays pinned.

---

## Phase 2 — Panels → Inline Results

### 2a — Auto-dismiss defense (`Composer.tsx`)

At the top of the slash-command dispatch branch in `Composer.tsx`, close any open panel before dispatching:

```typescript
// Slash command → dispatch
if (trimmed.startsWith("/")) {
  if (uiStore.getState().activePanel) globalBridge.closePanel();
  dispatcher.dispatch(trimmed).then(...)
```

This ensures no panel can coexist with a new command's result, regardless of future changes.

### 2b — Convert `/owl list` to inline (`owl.ts`)

**Current:** Builds `PanelItem[]`, returns `{ kind: "panel", payload: { items, actions } }`.

**After:** Formats owls as a text block, returns `{ kind: "system-message", text }`.

Output format (active owl marked with ←):

```
🦉 Owls (3)

  🦉 Atlas   advisor             ← active
  🐦 Aria    assistant  [bmad]
  🎯 Bolt    coder      [bmad]

Switch with: /owl switch <name>
```

Implementation:

```typescript
export const handleOwlList: CommandHandler = async (ctx, _args) => {
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) return { kind: "error", text: "Specialized owl registry not initialized." };

  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const specs    = owlCtx.registry.listAll();
  const active   = ctx.getStore().activeOwlName.toLowerCase();

  if (specs.length === 0) {
    return { kind: "system-message", text: "No owls registered. Use /owl create to add one." };
  }

  const lines = specs.map((s) => {
    const marker = s.name.toLowerCase() === active ? " ← active" : "";
    const source = s.source ? `  [${s.source}]` : "";
    return `  ${s.emoji} ${s.name.padEnd(12)} ${s.role}${source}${marker}`;
  });

  const text =
    `🦉 Owls (${specs.length})\n\n` +
    lines.join("\n") +
    `\n\nSwitch with: /owl switch <name>`;

  return { kind: "system-message", text };
};
```

Note: `ctx.getStore()` — the CommandContext already has `getStore: () => uiStore.getState()` so `activeOwlName` is available.

### 2c — Convert `/owl show` to inline (`owl.ts`)

**Current:** Builds items via `textToItems(text)`, returns `{ kind: "panel" }`.

**After:** Return the text directly:

```typescript
export const handleOwlShow: CommandHandler = async (ctx, args) => {
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) return { kind: "error", text: "Specialized owl registry not initialized." };
  const text = await dispatchOwlCommand("show", args, owlCtx);
  return { kind: "system-message", text };
};
```

### 2d — Add `/owl switch <name>` (`owl.ts`)

New handler:

```typescript
export const handleOwlSwitch: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlSwitch: entry", { args });
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /owl switch <name>" };

  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) return { kind: "error", text: "Specialized owl registry not initialized." };

  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const spec = owlCtx.registry.listAll().find(
    (s) => s.name.toLowerCase() === name.toLowerCase(),
  );

  if (!spec) {
    log.cli.warn("handleOwlSwitch: owl not found", { name });
    return { kind: "error", text: `Owl "${name}" not found. Use /owl list to see available owls.` };
  }

  ctx.bridge.changeOwl(spec.name, spec.emoji);
  log.cli.debug("handleOwlSwitch: exit", { name: spec.name, emoji: spec.emoji });
  return { kind: "system-message", text: `Switched to ${spec.emoji} ${spec.name}` };
};
```

### 2e — Register `/owl switch` in registry (`registry.ts`)

Add to the `/owl` subcommand list:

```typescript
{
  name: "switch",
  description: "Switch to a different owl persona",
  args: ["<name>"],
  handler: handleOwlSwitch,
},
```

Import `handleOwlSwitch` from `./handlers/owl.js`.

### 2f — Remove `textToItems` helper (`owl.ts`)

`textToItems` is no longer used after 2b and 2c. Remove it.

### Verification

- `/owl list` → inline text, no panel, Composer stays enabled
- `/owl switch Aria` → inline "Switched to 🐦 Aria", owl changes in header
- `/owl switch nobody` → inline error
- `/owl show Atlas` → inline text
- Running any command while a panel is hypothetically open → panel closes first

---

## Phase 3 — Fix `/owl create` and `/owl from-bmad` Wizard Flow

### 3a — Diagnostic logging (first step, before any fix)

Add 4-point logging at the start of `handleOwlCreate` and `handleOwlFromBmad` in `owl.ts`:

```typescript
export const handleOwlCreate: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlCreate: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.error("handleOwlCreate: no registry", new Error("registry null"));
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  log.cli.debug("handleOwlCreate: calling dispatchOwlCommand create");
  const adapter = buildBridgeAdapter(ctx.bridge);
  try {
    const text = await dispatchOwlCommand("create", [], { ...owlCtx, channelAdapter: adapter });
    log.cli.debug("handleOwlCreate: exit", { textLen: text.length });
    return { kind: "system-message", text };
  } catch (err) {
    log.cli.error("handleOwlCreate: dispatchOwlCommand threw", err as Error);
    return { kind: "error", text: `Owl creation failed: ${(err as Error).message}` };
  }
};
```

Same pattern for `handleOwlFromBmad`. Also add a `try/catch` to `buildBridgeAdapter`'s `ask()` callback:

```typescript
ask: async (_userId, prompt) => {
  log.cli.debug("owl.bridgeAdapter.ask: entry", { promptText: prompt.text.slice(0, 60) });
  try {
    const answer = await bridge.prompt(prompt.text, {
      choices: prompt.choices,
      defaultChoice: prompt.defaultChoice,
    });
    log.cli.debug("owl.bridgeAdapter.ask: got answer", { answerLen: answer.length });
    return answer;
  } catch (err) {
    log.cli.error("owl.bridgeAdapter.ask: bridge.prompt threw", err as Error);
    throw err;
  }
},
```

### 3b — Investigate `dispatchOwlCommand("create")` hang point

Read `src/gateway/commands/owl-command.ts`. Find where the wizard calls `context.channelAdapter.ask()`. Add a log immediately before and after each call. Run the app, type `/owl create`, watch the log file:

```bash
tail -f logs/stackowl-$(date +%F).log | jq 'select(.module == "cli") | {msg, fields}'
```

If `handleOwlCreate: calling dispatchOwlCommand create` appears but `owl.bridgeAdapter.ask: entry` never appears, the hang is inside `dispatchOwlCommand` before the first `ask()` call — likely a directory scan or registry operation that throws silently.

### 3c — Fix based on findings

**Case A — BMAD directory scan fails silently:**
Wrap the scan in a try/catch and surface it as a `{ kind: "error" }` with the actual path and error:

```typescript
return { kind: "error", text: `Cannot scan BMAD directory: ${err.message}` };
```

**Case B — `prompt.requested` fires but Composer doesn't respond:**
Confirm `promptQuestion` is set in the store after `bridge.prompt()` is called. If the store update races with the Composer re-render, add a `nextTick` delay or verify the reducer handles `prompt.requested` synchronously (it should).

**Case C — Completions popup intercepts first Enter:**
Add an explicit check in `Composer.tsx` — after completing a subcommand (`/owl create`), verify the "already exact" path fires correctly for multi-word subcommand entries. The current check uses `lastTypedWord === entry.value` which should catch "create" when the full input is "/owl create". Trace with a log if needed.

### Verification

- `/owl create` → first wizard question appears in Composer immediately
- Typing an answer + Enter → next question appears or confirmation shown
- `/owl from-bmad` → same wizard flow works end-to-end
- Errors (missing BMAD dir, bad args) appear as inline error notices

---

## Files Touched

| File | Phase | Change |
|------|-------|--------|
| `src/cli/v2/screens/ChatScreen.tsx` | 1 | Add `COMPOSER_MIN_ROWS`, `SHORTCUTS_ROWS`; replace nested `flexGrow` with explicit `height={contentRows}` |
| `src/cli/v2/components/Composer.tsx` | 2a | Auto-dismiss panel before dispatching |
| `src/cli/v2/commands/handlers/owl.ts` | 2b–2d, 3a–3c | `handleOwlList` inline; `handleOwlShow` inline; add `handleOwlSwitch`; remove `textToItems`; add diagnostics + fix wizard |
| `src/cli/v2/commands/registry.ts` | 2e | Register `/owl switch` subcommand |

Panel infrastructure (`Panel.tsx`, `PanelHost.tsx`, `slices/panel.ts`, `bridge.ts`) is **not touched** — it stays for potential future modal flows.

---

## Non-Goals

- Removing the Panel component or its state machine
- Changing any other command's output format (MCP, Skills, etc.) — only owl commands are in scope
- Redesigning the Composer input model
- Changing the ShortcutsBar content or Footer
