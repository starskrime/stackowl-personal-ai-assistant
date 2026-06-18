# TUI v2 Layout & Panel UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three TUI v2 UX bugs — pin the header so the logo never scrolls away, replace all display panels with inline transcript messages (no Esc needed), and diagnose + fix the silent /owl create wizard hang.

**Architecture:** Phase 1 adds an explicit computed height to the middle region in ChatScreen so Yoga can't evict the Header. Phase 2 rewrites three owl command handlers to return `system-message` instead of `panel`, adds a new `/owl switch` handler, and drops the now-unused `textToItems` helper. Phase 3 adds 4-point diagnostic logging to the wizard handlers, identifies the hang point from logs, and applies the matching targeted fix.

**Tech Stack:** Ink + Yoga (layout), Zustand uiStore, Vitest, TypeScript strict.

---

## File Map

| File | Change |
|------|--------|
| `src/cli/v2/screens/ChatScreen.tsx` | Add `COMPOSER_MIN_ROWS`, `SHORTCUTS_ROWS`; replace nested flexGrow with `height={contentRows}` |
| `src/cli/v2/components/Composer.tsx` | Auto-close panel before dispatching any command |
| `src/cli/v2/commands/handlers/owl.ts` | Rewrite `handleOwlList`, simplify `handleOwlShow`, add `handleOwlSwitch`, remove `textToItems`, add wizard diagnostics + fix |
| `src/cli/v2/commands/registry.ts` | Import `handleOwlSwitch`; add `switch` subcommand to `/owl` |
| `__tests__/cli/v2/commands/handlers/owl.test.ts` | Update existing panel tests → system-message; add tests for `handleOwlSwitch` |
| `__tests__/cli/v2/commands/registry.test.ts` | Add test that `/owl switch` resolves correctly |

---

## Task 1: Pin the Header — Fix Layout in ChatScreen

**Files:**
- Modify: `src/cli/v2/screens/ChatScreen.tsx`

The middle region currently uses two nested `flexGrow={1}` boxes. Yoga propagates Transcript's natural height upward through both layers, expanding past `height={rows}` and evicting the Header. The fix: give the middle region a hard computed height so the Header is protected.

- [ ] **Step 1: Add the two missing chrome constants**

Open `src/cli/v2/screens/ChatScreen.tsx`. After the existing `HEADER_ROWS` and `CHROME_ROWS` constants (lines 38–40), replace them with:

```typescript
/** Approximate header height: 2 rules + 6 logo lines + tagline = 9 rows. */
const HEADER_ROWS      = 9;
/** Composer minimum: border-top + input row + border-bottom = 3 rows. */
const COMPOSER_MIN_ROWS = 3;
/** ShortcutsBar: one text line. */
const SHORTCUTS_ROWS   = 1;
```

(Remove the old `CHROME_ROWS` constant — it is only used in `windowSize` and will be replaced below.)

- [ ] **Step 2: Update windowSize calculation**

`windowSize` used `CHROME_ROWS` (= `HEADER_ROWS + 4`). Replace with the new constants:

```typescript
const windowSize = Math.max(1, Math.floor(
  (rows - HEADER_ROWS - COMPOSER_MIN_ROWS - SHORTCUTS_ROWS) / 3
));
```

- [ ] **Step 3: Apply explicit height to the middle region**

Find the middle region `<Box>` (currently `<Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">`) and replace it with a computed explicit height. Also remove the inner Box's `flexGrow` which is now redundant:

```tsx
{/* Middle region — explicit height pins Header and Composer regardless of content */}
const contentRows = Math.max(4, rows - HEADER_ROWS - COMPOSER_MIN_ROWS - SHORTCUTS_ROWS);

// In the JSX, replace the outer middle Box:
<Box flexDirection="column" height={contentRows} overflow="hidden">
  {/* scroll indicator — shown when not at bottom */}
  {hiddenAbove > 0 && (
    <Box justifyContent="center">
      <Text dimColor>↑ {hiddenAbove} earlier {hiddenAbove === 1 ? "turn" : "turns"} — PageUp to scroll</Text>
    </Box>
  )}

  {/* Messaging area — remove flexGrow/flexShrink from inner Box */}
  <Box flexDirection="column" paddingX={2} overflow="hidden">
    <Transcript turns={visibleTurns} />
    ...
  </Box>

  {hiddenBelow > 0 && (
    <Box justifyContent="center">
      <Text dimColor>↓ {hiddenBelow} newer {hiddenBelow === 1 ? "turn" : "turns"} — PageDown or Esc to follow</Text>
    </Box>
  )}
</Box>
```

The `contentRows` variable is declared inside the component function body, just before the `return` statement. The inner `<Box flexDirection="column" paddingX={2} overflow="hidden">` drops `flexGrow={1} flexShrink={1}` — they are redundant when the parent has an explicit height.

- [ ] **Step 4: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep "ChatScreen"
```

Expected: no output (no errors).

- [ ] **Step 5: Smoke-test manually**

Run the app:
```bash
npm run dev
```

Send 25+ messages. Confirm the STACKOWL logo and "Personal AI Assistant · Challenge Everything" tagline remain visible at all times. Resize the terminal to various widths and heights; confirm the header never disappears.

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/screens/ChatScreen.tsx
git commit -m "fix(tui-v2): pin header with explicit content height — logo never scrolls away"
```

---

## Task 2: Auto-Close Panel in Composer Before Dispatching

**Files:**
- Modify: `src/cli/v2/components/Composer.tsx`

When any panel is open and the user runs a new slash command, the panel should close first so its content doesn't conflict with the new result.

- [ ] **Step 1: Add the one-liner auto-dismiss**

In `src/cli/v2/components/Composer.tsx`, find the slash-command dispatch branch (line ~161):

```typescript
// Slash command → dispatch
if (trimmed.startsWith("/")) {
  dispatcher.dispatch(trimmed).then((result) => {
```

Add the closePanel call immediately before `dispatcher.dispatch`:

```typescript
// Slash command → dispatch
if (trimmed.startsWith("/")) {
  // Close any open panel before dispatching — prevents layout conflicts.
  if (uiStore.getState().activePanel) globalBridge.closePanel();
  dispatcher.dispatch(trimmed).then((result) => {
```

`uiStore` and `globalBridge` are already imported at the top of the file.

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep "Composer"
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/cli/v2/components/Composer.tsx
git commit -m "fix(tui-v2): auto-close open panel before dispatching new command"
```

---

## Task 3: Convert `/owl list` → Inline System Message

**Files:**
- Modify: `src/cli/v2/commands/handlers/owl.ts`
- Modify: `__tests__/cli/v2/commands/handlers/owl.test.ts`

`handleOwlList` currently returns `{ kind: "panel" }` which steals Composer focus. After this task it returns `{ kind: "system-message" }` with a formatted inline list.

- [ ] **Step 1: Update the existing panel tests to expect system-message**

In `__tests__/cli/v2/commands/handlers/owl.test.ts`, find the `describe("handleOwlList")` block. Replace the three existing tests (kind='panel', panel items include names, panel items include emojis, panel has switch action) with:

```typescript
describe("handleOwlList", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlList(ctx, []);
    expect(result.kind).toBe("system-message");
  });

  it("text contains all registered owl names and emojis", async () => {
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("Alice");
    expect(result.text).toContain("📊");
    expect(result.text).toContain("Bob");
    expect(result.text).toContain("🤖");
  });

  it("marks the active owl with '← active'", async () => {
    ctx = makeCtx(
      makeGateway(makeRegistry()),
      bridge,
      "Alice",   // activeOwlName
    );
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("← active");
  });

  it("text contains /owl switch hint", async () => {
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("/owl switch");
  });

  it("returns system-message with empty-state text when no owls", async () => {
    ctx = makeCtx(makeGateway(makeRegistry([])), bridge);
    const result = await handleOwlList(ctx, []);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("No owls");
  });
});
```

Note: `makeRegistry` needs an optional `owls` parameter and `makeCtx` needs an optional `activeOwlName` parameter. Update those factory functions:

```typescript
function makeRegistry(owls?: Partial<SpecializedOwlSpec>[]): SpecializedOwlRegistry {
  const r = new SpecializedOwlRegistry();
  const specs = owls ?? [
    { name: "Alice", emoji: "📊", source: "bmad" },
    { name: "Bob",   emoji: "🤖", source: "custom", type: "coordinator" },
  ];
  for (const o of specs) r.registerSpec(makeSpec(o));
  vi.spyOn(r, "loadAll").mockResolvedValue();
  return r;
}

function makeCtx(
  gateway: Record<string, unknown>,
  bridge: UiBridge,
  activeOwlName = "",
): CommandContext {
  return {
    bridge,
    getStore: () => ({ activeOwlName }) as UiState,
    getMemoryRepo: () => { throw new Error("not used"); },
    getMcpManager: () => { throw new Error("not used"); },
    getOwlGateway: () => gateway as ReturnType<CommandContext["getOwlGateway"]>,
  };
}
```

- [ ] **Step 2: Run the tests — verify they fail**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | grep -A 3 "handleOwlList"
```

Expected: FAIL — `handleOwlList` still returns `kind: "panel"`.

- [ ] **Step 3: Rewrite `handleOwlList` in `owl.ts`**

Replace the entire `handleOwlList` function:

```typescript
export const handleOwlList: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlList: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlList: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const specs  = owlCtx.registry.listAll();
  const active = ctx.getStore().activeOwlName.toLowerCase();
  log.cli.debug("handleOwlList: exit", { count: specs.length });

  if (specs.length === 0) {
    return { kind: "system-message", text: "No owls registered. Use /owl create to add one." };
  }

  const lines = specs.map((s) => {
    const marker = s.name.toLowerCase() === active ? "  ← active" : "";
    const source = s.source ? `  [${s.source}]` : "";
    return `  ${s.emoji} ${s.name.padEnd(14)} ${s.role}${source}${marker}`;
  });
  const text =
    `🦉 Owls (${specs.length})\n\n` +
    lines.join("\n") +
    `\n\nSwitch with: /owl switch <name>`;

  return { kind: "system-message", text };
};
```

Also remove the `textToItems` helper function (it is now unused):

```typescript
// DELETE this entire function:
function textToItems(text: string) {
  return text
    .split("\n")
    .filter((l) => l.trim())
    .map((line, i) => ({ id: `owl-${i}`, label: line }));
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | grep -E "✓|✗|handleOwlList"
```

Expected: all `handleOwlList` tests pass.

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep "owl"
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/commands/handlers/owl.ts __tests__/cli/v2/commands/handlers/owl.test.ts
git commit -m "feat(tui-v2): /owl list → inline system-message, remove textToItems"
```

---

## Task 4: Convert `/owl show` → Inline System Message

**Files:**
- Modify: `src/cli/v2/commands/handlers/owl.ts`
- Modify: `__tests__/cli/v2/commands/handlers/owl.test.ts`

- [ ] **Step 1: Update existing `handleOwlShow` tests to expect system-message**

Find `describe("handleOwlShow")` in the test file. Replace the test that checks `result.kind === "panel"` with:

```typescript
describe("handleOwlShow", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
    mockDispatch.mockResolvedValue("Alice\nrole: advisor\npersonality: professional");
  });

  it("returns kind='system-message'", async () => {
    const result = await handleOwlShow(ctx, ["Alice"]);
    expect(result.kind).toBe("system-message");
  });

  it("text contains the dispatch output", async () => {
    const result = await handleOwlShow(ctx, ["Alice"]);
    if (result.kind !== "system-message") throw new Error("expected system-message");
    expect(result.text).toContain("Alice");
  });
});
```

- [ ] **Step 2: Run tests — verify fail**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | grep -E "handleOwlShow"
```

Expected: FAIL.

- [ ] **Step 3: Simplify `handleOwlShow` in `owl.ts`**

Replace the entire `handleOwlShow` function:

```typescript
export const handleOwlShow: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlShow: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlShow: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const text = await dispatchOwlCommand("show", args, owlCtx);
  log.cli.debug("handleOwlShow: exit", { textLen: text.length });
  return { kind: "system-message", text };
};
```

- [ ] **Step 4: Run tests — verify pass**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | grep -E "✓|✗|handleOwlShow"
```

Expected: all `handleOwlShow` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/commands/handlers/owl.ts __tests__/cli/v2/commands/handlers/owl.test.ts
git commit -m "feat(tui-v2): /owl show → inline system-message"
```

---

## Task 5: Add `/owl switch <name>` Command

**Files:**
- Modify: `src/cli/v2/commands/handlers/owl.ts`
- Modify: `src/cli/v2/commands/registry.ts`
- Modify: `__tests__/cli/v2/commands/handlers/owl.test.ts`
- Modify: `__tests__/cli/v2/commands/registry.test.ts`

- [ ] **Step 1: Write failing handler tests**

Add a new `describe` block to `__tests__/cli/v2/commands/handlers/owl.test.ts`:

```typescript
describe("handleOwlSwitch", () => {
  let bridge: UiBridge;
  let ctx: CommandContext;

  beforeEach(() => {
    bridge = makeBridge();
    ctx = makeCtx(makeGateway(makeRegistry()), bridge);
  });

  it("returns system-message with confirmation text on valid name", async () => {
    const result = await handleOwlSwitch(ctx, ["Alice"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") throw new Error();
    expect(result.text).toContain("Alice");
    expect(result.text).toContain("📊");
  });

  it("calls bridge.changeOwl with correct name and emoji", async () => {
    await handleOwlSwitch(ctx, ["Alice"]);
    expect(bridge.changeOwl).toHaveBeenCalledWith("Alice", "📊");
  });

  it("is case-insensitive — matches 'alice' to 'Alice'", async () => {
    const result = await handleOwlSwitch(ctx, ["alice"]);
    expect(result.kind).toBe("system-message");
    expect(bridge.changeOwl).toHaveBeenCalledWith("Alice", "📊");
  });

  it("returns error for unknown name", async () => {
    const result = await handleOwlSwitch(ctx, ["nobody"]);
    expect(result.kind).toBe("error");
    if (result.kind !== "error") throw new Error();
    expect(result.text).toContain("nobody");
  });

  it("returns error when no name arg provided", async () => {
    const result = await handleOwlSwitch(ctx, []);
    expect(result.kind).toBe("error");
  });

  it("returns error when registry is unavailable", async () => {
    ctx = makeCtx(makeGateway(null), bridge);
    const result = await handleOwlSwitch(ctx, ["Alice"]);
    expect(result.kind).toBe("error");
  });
});
```

Add the import for `handleOwlSwitch` at the top of the test file alongside the existing handler imports.

- [ ] **Step 2: Run tests — verify fail**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | grep "handleOwlSwitch"
```

Expected: FAIL — `handleOwlSwitch is not a function` or similar.

- [ ] **Step 3: Implement `handleOwlSwitch` in `owl.ts`**

Add after `handleOwlUnpin`:

```typescript
// ─── /owl switch <name> ───────────────────────────────────────────────────────

export const handleOwlSwitch: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlSwitch: entry", { args });
  const name = args[0];
  if (!name) {
    log.cli.warn("handleOwlSwitch: no name provided");
    return { kind: "error", text: "Usage: /owl switch <name>" };
  }

  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlSwitch: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }

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

- [ ] **Step 4: Run handler tests — verify pass**

```bash
npx vitest run __tests__/cli/v2/commands/handlers/owl.test.ts 2>&1 | tail -10
```

Expected: all tests pass (the full suite, not just `handleOwlSwitch`).

- [ ] **Step 5: Write failing registry test**

Add to `__tests__/cli/v2/commands/registry.test.ts` in the `/owl` describe block:

```typescript
it("resolves /owl switch as subcommand with name arg", () => {
  const result = resolveCommand("/owl switch Aria");
  expect(result).not.toBeNull();
  expect(result!.subcommand?.name).toBe("switch");
  expect(result!.args).toEqual(["Aria"]);
});
```

Run to verify it fails:

```bash
npx vitest run __tests__/cli/v2/commands/registry.test.ts 2>&1 | grep "switch"
```

Expected: FAIL.

- [ ] **Step 6: Register `/owl switch` in `registry.ts`**

In `registry.ts`, add `handleOwlSwitch` to the import block:

```typescript
import {
  handleOwlList,
  handleOwlShow,
  handleOwlCreate,
  handleOwlFromBmad,
  handleOwlDelete,
  handleOwlPin,
  handleOwlUnpin,
  handleOwlSwitch,   // ← add this
} from "./handlers/owl.js";
```

Then add `switch` to the `/owl` subcommands array (add it after `list`, before `show`):

```typescript
subcommands: [
  { name: "list",      description: "List all owls (BMAD + custom + builtin)", handler: handleOwlList },
  { name: "switch",    description: "Switch to an owl by name", args: [{ name: "<name>" }], handler: handleOwlSwitch },
  { name: "show",      description: "Show owl details",      args: [{ name: "<name>" }], handler: handleOwlShow },
  // ... rest unchanged
```

- [ ] **Step 7: Run full test suite — verify pass**

```bash
npx vitest run __tests__/cli/v2/commands/ 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 8: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep -E "owl|switch|registry"
```

Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add src/cli/v2/commands/handlers/owl.ts src/cli/v2/commands/registry.ts \
        __tests__/cli/v2/commands/handlers/owl.test.ts \
        __tests__/cli/v2/commands/registry.test.ts
git commit -m "feat(tui-v2): add /owl switch <name> command, inline system-message result"
```

---

## Task 6: Diagnose `/owl create` and `/owl from-bmad` Wizard Hang

**Files:**
- Modify: `src/cli/v2/commands/handlers/owl.ts`

The wizard uses `bridge.prompt()` → `prompt.requested` → Composer shows question → user presses Enter → `prompt.submitted`. The user reports nothing appears. This task adds diagnostics, runs the app, reads the log to identify the exact hang point, then applies the fix.

- [ ] **Step 1: Add entry/error logging + try/catch to `handleOwlCreate`**

Replace `handleOwlCreate`:

```typescript
export const handleOwlCreate: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlCreate: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.error("handleOwlCreate: no registry — cannot create owl", new Error("registry null"));
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

- [ ] **Step 2: Add same pattern to `handleOwlFromBmad`**

Replace `handleOwlFromBmad`:

```typescript
export const handleOwlFromBmad: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlFromBmad: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.error("handleOwlFromBmad: no registry", new Error("registry null"));
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  log.cli.debug("handleOwlFromBmad: calling dispatchOwlCommand from-bmad");
  const adapter = buildBridgeAdapter(ctx.bridge);
  try {
    const text = await dispatchOwlCommand("from-bmad", args, { ...owlCtx, channelAdapter: adapter });
    log.cli.debug("handleOwlFromBmad: exit", { textLen: text.length });
    return { kind: "system-message", text };
  } catch (err) {
    log.cli.error("handleOwlFromBmad: dispatchOwlCommand threw", err as Error);
    return { kind: "error", text: `Owl creation from BMAD failed: ${(err as Error).message}` };
  }
};
```

- [ ] **Step 3: Add try/catch to `buildBridgeAdapter`**

Replace the `ask` method inside `buildBridgeAdapter`:

```typescript
ask: async (
  _userId: string,
  prompt: { text: string; choices?: string[]; defaultChoice?: string },
): Promise<string> => {
  log.cli.debug("owl.bridgeAdapter.ask: entry", { promptText: prompt.text.slice(0, 60) });
  try {
    const answer = await bridge.prompt(prompt.text, {
      choices: prompt.choices,
      defaultChoice: prompt.defaultChoice,
    });
    log.cli.debug("owl.bridgeAdapter.ask: received answer", { len: answer.length });
    return answer;
  } catch (err) {
    log.cli.error("owl.bridgeAdapter.ask: bridge.prompt threw", err as Error);
    throw err;
  }
},
```

- [ ] **Step 4: Commit the diagnostic version**

```bash
git add src/cli/v2/commands/handlers/owl.ts
git commit -m "debug(tui-v2): add wizard diagnostics to /owl create and /owl from-bmad"
```

- [ ] **Step 5: Run the app and trigger the bug**

```bash
npm run dev
```

Type `/owl create` and press Enter. Nothing will appear. Then open the log:

```bash
tail -50 logs/stackowl-$(date +%F).log | jq 'select(.module == "cli") | {msg, fields, err}'
```

**Read the output and identify which case applies:**

**Case A — `handleOwlCreate: calling dispatchOwlCommand create` appears but `owl.bridgeAdapter.ask: entry` never appears:**
The hang is inside `dispatchOwlCommand("create")` before the first `ask()` call. Likely a directory scan or file read that throws silently. Go to Step 6A.

**Case B — `owl.bridgeAdapter.ask: entry` appears but user never sees the question:**
`bridge.prompt()` fires but `promptQuestion` isn't displayed in Composer. Go to Step 6B.

**Case C — `handleOwlCreate: entry` never appears:**
The command dispatch itself fails. Check for `handleOwlCreate: no registry` — registry initialization issue. Go to Step 6C.

- [ ] **Step 6A: Fix — `dispatchOwlCommand` throws before first ask (Case A)**

Read `src/gateway/commands/owl-command.ts` to find where the wizard starts. Find the first operation that could fail silently (directory scan, file read, etc.) and wrap it with error propagation. The specific fix depends on what you find, but the pattern is:

```typescript
// BEFORE (example — silent failure):
const bmadDir = path.join(workspacePath, "_bmad");
const files = fs.readdirSync(bmadDir);

// AFTER — propagate error so the try/catch in handleOwlCreate catches it:
let files: string[];
try {
  files = fs.readdirSync(bmadDir);
} catch (err) {
  throw new Error(`Cannot read BMAD directory at ${bmadDir}: ${(err as Error).message}`);
}
```

After applying the fix, re-run and confirm the error message appears inline in the TUI as a notice. If the directory exists and the problem is elsewhere, trace further until you find the throw site.

- [ ] **Step 6B: Fix — prompt fires but Composer doesn't show it (Case B)**

Check `src/cli/v2/events/reducer.ts` for how `prompt.requested` is handled. Confirm it sets `promptQuestion` on the store:

```bash
grep -n "prompt.requested\|promptQuestion" src/cli/v2/events/reducer.ts
```

If the reducer handles the event but `promptQuestion` is never set in the rendered Composer, verify the Composer's selector:

```typescript
const promptQuestion = useUiStore((s) => s.promptQuestion);
```

Check `src/cli/v2/state/slices/ui.ts` for whether `promptQuestion` is in the initial state and the reducer's slice. If it's missing from the initial state type or slice, add it following the same pattern as the existing `promptChoices` and `promptDefault` fields.

- [ ] **Step 6C: Fix — registry not initialized (Case C)**

Check `src/gateway/core.ts` for when `getSpecializedRegistry()` returns null. Add an error notice at startup if the registry can't be initialized, so the user knows before trying `/owl create`. Also ensure the gateway initializes the specialized registry early enough in the startup sequence.

- [ ] **Step 7: Run full test suite**

```bash
npx vitest run 2>&1 | tail -15
```

Expected: all tests pass with no regressions.

- [ ] **Step 8: Commit the fix**

```bash
git add -p  # stage only the fix changes, not the debug logging
git commit -m "fix(tui-v2): /owl create wizard — surface hang point as user-visible error"
```

---

## Final Verification

Run all tests to confirm no regressions across the full suite:

```bash
npx vitest run 2>&1 | tail -10
```

Expected output:
```
Test Files  N passed (N)
     Tests  N passed (N)
```

Manual smoke test checklist:
- [ ] STACKOWL logo stays visible after 20+ messages
- [ ] Resize terminal — header remains pinned
- [ ] `/owl list` → inline text, no panel, Composer still active
- [ ] `/owl show Atlas` → inline text
- [ ] `/owl switch Aria` → "Switched to 🐦 Aria", owl changes in header
- [ ] `/owl switch nobody` → inline error message
- [ ] Running any command while hypothetical panel is open → panel closes first
- [ ] `/owl create` → first wizard question appears immediately in Composer
