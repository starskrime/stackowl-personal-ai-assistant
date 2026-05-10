# TUI v2 Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the existing TUI v2 component layer to match the approved visual spec — amber brand accent, StackOwl spinner, bordered input box with footer inside, and purple heartbeat card.

**Architecture:** Incremental in-place updates to 8 component/screen files with no changes to state, events, bridge, or io layers. A new `spinner.ts` constants file is the only new file. `ShortcutsBar.tsx` is deleted after its logic is absorbed into `Composer.tsx`.

**Tech Stack:** Ink v6, React, ink-testing-library (tests), Vitest

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| **Create** | `src/cli/v2/components/spinner.ts` | Shared `STACKOWL_SPINNER` array + `SPINNER_AMBER` color constant |
| **Modify** | `src/cli/v2/components/OwlAvatar.tsx` | Default `color` prop: `"cyan"` → `SPINNER_AMBER` |
| **Modify** | `src/cli/v2/components/ToolCallCard.tsx` | Replace braille SPINNER with `STACKOWL_SPINNER` + amber color |
| **Modify** | `src/cli/v2/components/Composer.tsx` | Replace flat separator with bordered Box; absorb ShortcutsBar footer; read footer data from store |
| **Delete** | `src/cli/v2/components/ShortcutsBar.tsx` | Logic absorbed into Composer |
| **Modify** | `src/cli/v2/components/HeartbeatBanner.tsx` | `"magenta"` → `"#A78BFA"` (purple) |
| **Modify** | `src/cli/v2/screens/ChatScreen.tsx` | Remove `ShortcutsBar` import + render |
| **Modify** | `src/cli/v2/screens/ParliamentScreen.tsx` | Replace braille SPINNER with `STACKOWL_SPINNER` + amber |
| **Create** | `src/cli/v2/testing/OwlAvatar.test.tsx` | Render tests |
| **Create** | `src/cli/v2/testing/ToolCallCard.test.tsx` | Running/done/failed state tests |
| **Create** | `src/cli/v2/testing/HeartbeatBanner.test.tsx` | Border + header render tests |
| **Create** | `src/cli/v2/testing/Composer.test.tsx` | Bordered box + generating state tests |

---

### Task 1: Create `src/cli/v2/components/spinner.ts`

**Files:**
- Create: `src/cli/v2/components/spinner.ts`

- [ ] **Step 1: Create the file**

```ts
/** Shared spinner constants for all TUI v2 animated components. */
export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;
export const SPINNER_AMBER = "#F5A623";
export const SPINNER_INTERVAL_MS = 80;
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/cli/v2/components/spinner.ts
git commit -m "feat(tui-v2): add shared StackOwl spinner constants"
```

---

### Task 2: Update `OwlAvatar` — amber default name color

**Files:**
- Modify: `src/cli/v2/components/OwlAvatar.tsx`
- Create: `src/cli/v2/testing/OwlAvatar.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `src/cli/v2/testing/OwlAvatar.test.tsx`:

```tsx
import React from "react";
import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { OwlAvatar } from "../components/OwlAvatar.js";

describe("OwlAvatar", () => {
  it("renders emoji, name, and role", () => {
    const { lastFrame } = render(
      <OwlAvatar emoji="🦉" name="Hoots" role="strategist" />
    );
    expect(lastFrame()).toContain("🦉");
    expect(lastFrame()).toContain("Hoots");
    expect(lastFrame()).toContain("strategist");
  });

  it("renders without role when omitted", () => {
    const { lastFrame } = render(<OwlAvatar emoji="🦉" name="Hoots" />);
    expect(lastFrame()).toContain("🦉");
    expect(lastFrame()).toContain("Hoots");
  });

  it("accepts a custom color override", () => {
    // Just verifies it renders without error when color is passed
    const { lastFrame } = render(
      <OwlAvatar emoji="🦅" name="Sage" color="cyan" />
    );
    expect(lastFrame()).toContain("Sage");
  });
});
```

- [ ] **Step 2: Run to verify it fails (or passes — these are render-correctness tests)**

```bash
npx vitest run src/cli/v2/testing/OwlAvatar.test.tsx
```

Expected: tests pass (component renders correctly already)

- [ ] **Step 3: Update `OwlAvatar.tsx` — change default color to amber**

Replace the entire file content:

```tsx
/** Signed message author chip: emoji + bold name + dim role. */

import { Box, Text } from "ink";
import { SPINNER_AMBER } from "./spinner.js";

export interface OwlAvatarProps {
  emoji: string;
  name: string;
  role?: string;
  /** Override name color. Defaults to amber brand accent. */
  color?: string;
}

export function OwlAvatar({ emoji, name, role, color = SPINNER_AMBER }: OwlAvatarProps) {
  return (
    <Box>
      <Text>{emoji} </Text>
      <Text bold color={color}>{name}</Text>
      {role ? <Text dimColor>  {role}</Text> : null}
    </Box>
  );
}
```

- [ ] **Step 4: Run tests again to confirm they still pass**

```bash
npx vitest run src/cli/v2/testing/OwlAvatar.test.tsx
```

Expected: all 3 tests pass

- [ ] **Step 5: Full type-check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/components/OwlAvatar.tsx src/cli/v2/testing/OwlAvatar.test.tsx
git commit -m "feat(tui-v2): owl name uses amber brand accent (#F5A623)"
```

---

### Task 3: Update `ToolCallCard` — StackOwl spinner + amber

**Files:**
- Modify: `src/cli/v2/components/ToolCallCard.tsx`
- Create: `src/cli/v2/testing/ToolCallCard.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `src/cli/v2/testing/ToolCallCard.test.tsx`:

```tsx
import React from "react";
import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { ToolCallCard } from "../components/ToolCallCard.js";
import type { ToolCall } from "../state/slices/tools.js";

const baseCall: ToolCall = {
  toolCallId: "tc-1",
  turnId: "turn-1",
  toolName: "bash",
  status: "running",
  startedAt: Date.now(),
  elapsedMs: 0,
};

describe("ToolCallCard", () => {
  it("running state: shows tool name and elapsed time", () => {
    const { lastFrame } = render(
      <ToolCallCard tool={{ ...baseCall, status: "running", elapsedMs: 1200 }} />
    );
    expect(lastFrame()).toContain("bash");
    expect(lastFrame()).toContain("1.2s");
  });

  it("running state: shows progress message when present", () => {
    const { lastFrame } = render(
      <ToolCallCard
        tool={{ ...baseCall, status: "running", elapsedMs: 0, progressMessage: "reading file" }}
      />
    );
    expect(lastFrame()).toContain("reading file");
  });

  it("done state: shows └ connector and ✓ checkmark with time", () => {
    const { lastFrame } = render(
      <ToolCallCard tool={{ ...baseCall, status: "done", elapsedMs: 4100 }} />
    );
    expect(lastFrame()).toContain("└");
    expect(lastFrame()).toContain("✓");
    expect(lastFrame()).toContain("4.1s");
  });

  it("failed state: shows └ connector, ✗ mark, and error text", () => {
    const { lastFrame } = render(
      <ToolCallCard
        tool={{ ...baseCall, status: "failed", elapsedMs: 0, error: "permission denied" }}
      />
    );
    expect(lastFrame()).toContain("└");
    expect(lastFrame()).toContain("✗");
    expect(lastFrame()).toContain("permission denied");
  });
});
```

- [ ] **Step 2: Run to verify current behavior**

```bash
npx vitest run src/cli/v2/testing/ToolCallCard.test.tsx
```

Expected: all 4 tests pass (functional behavior is unchanged)

- [ ] **Step 3: Update `ToolCallCard.tsx` — swap spinner to StackOwl + amber**

Replace the entire file:

```tsx
/**
 * ToolCallCard — inline tool status card.
 *
 * States:
 *   running  · toolName  progress msg   2.3s    ← StackOwl amber spinner + name
 *   done     └ toolName  ✓ 2.3s                 ← dim with green checkmark
 *   failed   └ toolName  ✗ error message         ← red error
 */

import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import type { ToolCall } from "../state/slices/tools.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "./spinner.js";

function fmtTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export interface ToolCallCardProps {
  tool: ToolCall;
}

export function ToolCallCard({ tool }: ToolCallCardProps) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (tool.status !== "running" && tool.status !== "pending") return;
    const t = setInterval(() => setFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [tool.status]);

  if (tool.status === "running" || tool.status === "pending") {
    return (
      <Box paddingLeft={2}>
        <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[frame]} </Text>
        <Text bold>{tool.toolName}</Text>
        {tool.progressMessage ? (
          <Text dimColor>  {tool.progressMessage}</Text>
        ) : null}
        {tool.elapsedMs > 0 ? (
          <Text dimColor>  {fmtTime(tool.elapsedMs)}</Text>
        ) : null}
      </Box>
    );
  }

  if (tool.status === "done") {
    return (
      <Box paddingLeft={2}>
        <Text dimColor>└ {tool.toolName}  </Text>
        <Text color="green">✓</Text>
        <Text dimColor>  {fmtTime(tool.elapsedMs)}</Text>
      </Box>
    );
  }

  return (
    <Box paddingLeft={2}>
      <Text dimColor>└ {tool.toolName}  </Text>
      <Text color="red">✗  {tool.error ?? "error"}</Text>
    </Box>
  );
}
```

- [ ] **Step 4: Run tests again**

```bash
npx vitest run src/cli/v2/testing/ToolCallCard.test.tsx
```

Expected: all 4 tests pass

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/components/ToolCallCard.tsx src/cli/v2/testing/ToolCallCard.test.tsx
git commit -m "feat(tui-v2): ToolCallCard uses StackOwl amber spinner"
```

---

### Task 4: Update `HeartbeatBanner` — purple color

**Files:**
- Modify: `src/cli/v2/components/HeartbeatBanner.tsx`
- Create: `src/cli/v2/testing/HeartbeatBanner.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `src/cli/v2/testing/HeartbeatBanner.test.tsx`:

```tsx
import React from "react";
import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

const msg: HeartbeatMessage = {
  id: "hb-1",
  owlId: "owl-sage",
  owlName: "Sage",
  owlEmoji: "🦅",
  text: "Deploy window closes at 5pm.",
  read: false,
  timestamp: Date.now(),
};

describe("HeartbeatBanner", () => {
  it("renders owl name and message text", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("Sage");
    expect(lastFrame()).toContain("Deploy window closes at 5pm.");
  });

  it("renders the unsolicited label", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("unsolicited");
  });

  it("renders the owl emoji", () => {
    const { lastFrame } = render(<HeartbeatBanner msg={msg} />);
    expect(lastFrame()).toContain("🦅");
  });
});
```

- [ ] **Step 2: Run to confirm current behavior**

```bash
npx vitest run src/cli/v2/testing/HeartbeatBanner.test.tsx
```

Expected: all 3 tests pass

- [ ] **Step 3: Update `HeartbeatBanner.tsx` — change magenta to purple**

Replace the entire file:

```tsx
/**
 * HeartbeatBanner — bordered card for unsolicited owl proactive messages.
 *
 *   ╭─────────────────────────────────────────╮
 *   │  🔔 Hoots  unsolicited                  │
 *   │                                         │
 *   │  Your reminder text here                │
 *   ╰─────────────────────────────────────────╯
 *
 * Purple (#A78BFA) border distinguishes proactive messages from all solicited turns.
 */

import { Box, Text } from "ink";
import type { HeartbeatMessage } from "../state/slices/heartbeat.js";

const HEARTBEAT_PURPLE = "#A78BFA";

export interface HeartbeatBannerProps {
  msg: HeartbeatMessage;
}

export function HeartbeatBanner({ msg }: HeartbeatBannerProps) {
  const emoji = msg.owlEmoji ?? "🔔";
  return (
    <Box
      borderStyle="round"
      borderColor={HEARTBEAT_PURPLE}
      flexDirection="column"
      paddingX={1}
      paddingY={0}
      marginTop={0}
      marginBottom={1}
    >
      <Box>
        <Text>{emoji} </Text>
        <Text bold color={HEARTBEAT_PURPLE}>{msg.owlName}</Text>
        <Text dimColor>  unsolicited</Text>
      </Box>
      <Box marginTop={0} paddingLeft={0}>
        <Text wrap="wrap">{msg.text}</Text>
      </Box>
    </Box>
  );
}
```

- [ ] **Step 4: Run tests again**

```bash
npx vitest run src/cli/v2/testing/HeartbeatBanner.test.tsx
```

Expected: all 3 tests pass

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/components/HeartbeatBanner.tsx src/cli/v2/testing/HeartbeatBanner.test.tsx
git commit -m "feat(tui-v2): HeartbeatBanner uses purple (#A78BFA) for proactive identity"
```

---

### Task 5: Refactor `Composer` — bordered box + footer inside

This task absorbs `ShortcutsBar` into `Composer`. `Composer` gains no new props — it reads footer data directly from the Zustand store (all values are already in `UiSliceState`).

**Files:**
- Modify: `src/cli/v2/components/Composer.tsx`
- Create: `src/cli/v2/testing/Composer.test.tsx`

The `HeartbeatMessage` type used in the test file imports from the heartbeat slice. Check the type at `src/cli/v2/state/slices/heartbeat.ts` if needed — the relevant fields are `id`, `owlName`, `owlEmoji?`, `text`, `read`, `receivedAt`.

- [ ] **Step 1: Write tests**

Create `src/cli/v2/testing/Composer.test.tsx`:

```tsx
import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render } from "ink-testing-library";
import { UiStoreProvider } from "../providers/UiStoreProvider.js";
import { uiStore } from "../state/store.js";
import { Composer } from "../components/Composer.js";

function ComposerUnderTest({ disabled }: { disabled: boolean }) {
  return (
    <UiStoreProvider>
      <Composer onSubmit={() => {}} disabled={disabled} />
    </UiStoreProvider>
  );
}

beforeEach(() => {
  // Reset store to clean state before each test
  uiStore.setState({
    generating: false,
    activeOwlName: "Hoots",
    activeOwlEmoji: "🦉",
    activeModel: "sonnet-4-6",
    totalTokens: 0,
    totalCostUsd: 0,
  });
});

describe("Composer", () => {
  it("idle state: renders ❯ prompt and cursor", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("❯");
    expect(lastFrame()).toContain("▋");
  });

  it("idle state: renders bordered box (╭ and ╰)", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("╭");
    expect(lastFrame()).toContain("╰");
  });

  it("idle state: renders slash hint row when value is empty", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("/help");
  });

  it("idle state: renders footer with owl name and model", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("Hoots");
    expect(lastFrame()).toContain("sonnet-4-6");
  });

  it("generating state: shows generating text instead of ❯", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("generating...");
    expect(lastFrame()).not.toContain("❯");
  });

  it("generating state: footer shows esc esc to stop when generating=true in store", () => {
    uiStore.setState({ generating: true });
    const { lastFrame } = render(<ComposerUnderTest disabled={true} />);
    expect(lastFrame()).toContain("esc esc to stop");
  });

  it("footer omits tokens and cost when both are zero", () => {
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).not.toContain("tok");
    expect(lastFrame()).not.toContain("$");
  });

  it("footer shows tokens and cost when non-zero", () => {
    uiStore.setState({ totalTokens: 1234, totalCostUsd: 0.0023 });
    const { lastFrame } = render(<ComposerUnderTest disabled={false} />);
    expect(lastFrame()).toContain("1,234 tok");
    expect(lastFrame()).toContain("$0.0023");
  });
});
```

- [ ] **Step 2: Run to verify they fail (Composer doesn't have footer inside yet)**

```bash
npx vitest run src/cli/v2/testing/Composer.test.tsx
```

Expected: `bordered box` and `footer` tests fail; `❯` and `generating` tests may pass

- [ ] **Step 3: Rewrite `Composer.tsx`**

Replace the entire file:

```tsx
/**
 * Composer — multi-line input editor + generation state indicator.
 *
 * Idle layout (bordered box):
 *   ╭─────────────────────────────────────────────────╮
 *   │  ❯ your message here▋                           │
 *   │  /help · /owls · /sessions · /skills · /mcp    │
 *   │  Hoots · sonnet-4-6 · ? for help               │
 *   ╰─────────────────────────────────────────────────╯
 *
 * Generating layout:
 *   ╭─────────────────────────────────────────────────╮
 *   │  ✳ generating...                               │
 *   │  Hoots · sonnet-4-6 · 1,234 tok · esc esc stop │
 *   ╰─────────────────────────────────────────────────╯
 */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "./spinner.js";

const SLASH_COMMANDS = ["/help", "/owls", "/skills", "/mcp", "/sessions", "/quit", "/exit"];

export interface ComposerProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSubmit, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const [genFrame, setGenFrame] = useState(0);
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);

  // Store values for footer
  const mode       = useUiStore((s) => s.mode);
  const generating = useUiStore((s) => s.generating);
  const owlEmoji   = useUiStore((s) => s.activeOwlEmoji);
  const owlName    = useUiStore((s) => s.activeOwlName);
  const model      = useUiStore((s) => s.activeModel);
  const totalTokens   = useUiStore((s) => s.totalTokens);
  const totalCostUsd  = useUiStore((s) => s.totalCostUsd);

  useEffect(() => {
    const handler = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", handler);
    return () => { stdout?.off("resize", handler); };
  }, [stdout]);

  useEffect(() => {
    if (!disabled) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [disabled]);

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") { exit(); return; }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") globalBridge.dismissParliamentView();
        else                       globalBridge.requestParliamentView();
        return;
      }

      if (key.return && !key.shift) {
        const trimmed = value.trim();
        if (trimmed === "/sessions") { globalBridge.requestSessionsView(); setValue(""); return; }
        if (trimmed === "/help")     { globalBridge.requestHelpView();     setValue(""); return; }
        if (trimmed === "/owls")     { globalBridge.requestOwlsView();     setValue(""); return; }
        if (trimmed === "/skills")   { globalBridge.requestSkillsView();   setValue(""); return; }
        if (trimmed === "/mcp")      { globalBridge.requestMcpView();      setValue(""); return; }
        if (trimmed === "/quit" || trimmed === "/exit") { exit(); return; }
        if (trimmed) { historyRef.current.push(trimmed); onSubmit(trimmed); }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) { setValue((v) => v.slice(0, -1)); return; }
      if (key.upArrow)   { const p = historyRef.current.prev(value); if (p !== null) setValue(p); return; }
      if (key.downArrow) { const n = historyRef.current.next(); setValue(n !== null ? n : ""); return; }

      if (isPasteChunk(input)) { setValue((v) => v + stripPasteMarkers(input)); return; }
      if (!key.ctrl && !key.meta && input.length === 1) { setValue((v) => v + input); return; }
    },
    { isActive: !disabled },
  );

  const slashHint = (() => {
    if (!value.startsWith("/") || value.includes(" ")) return null;
    const match = SLASH_COMMANDS.find((cmd) => cmd.startsWith(value) && cmd !== value);
    return match ? match.slice(value.length) : null;
  })();

  const footerTokens = totalTokens > 0
    ? ` · ${totalTokens.toLocaleString()} tok · $${totalCostUsd.toFixed(4)}`
    : "";

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="gray"
      width={cols}
    >
      {disabled ? (
        <Box paddingLeft={1}>
          <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[genFrame]} </Text>
          <Text dimColor>generating...</Text>
        </Box>
      ) : (
        <>
          <Box paddingLeft={1}>
            <Text bold color="green">❯ </Text>
            <Text>{value}</Text>
            {slashHint ? <Text dimColor>{slashHint}</Text> : null}
            <Text color="cyan">▋</Text>
          </Box>
          {value === "" && (
            <Box paddingLeft={1}>
              <Text dimColor>/help · /owls · /sessions · /skills · /mcp</Text>
            </Box>
          )}
        </>
      )}
      <Box paddingLeft={1}>
        <Text dimColor>
          {owlEmoji} {owlName}
          {model ? ` · ${model}` : ""}
          {footerTokens}
          {" · "}
        </Text>
        {generating ? (
          <Text color="yellow">esc esc to stop</Text>
        ) : (
          <Text dimColor>? for help</Text>
        )}
      </Box>
    </Box>
  );
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run src/cli/v2/testing/Composer.test.tsx
```

Expected: all 8 tests pass

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/cli/v2/components/Composer.tsx src/cli/v2/testing/Composer.test.tsx
git commit -m "feat(tui-v2): Composer bordered box with footer and StackOwl spinner"
```

---

### Task 6: Update `ChatScreen` + delete `ShortcutsBar`

`Composer` now owns its own footer. `ShortcutsBar` is dead code.

**Files:**
- Modify: `src/cli/v2/screens/ChatScreen.tsx`
- Delete: `src/cli/v2/components/ShortcutsBar.tsx`

- [ ] **Step 1: Remove `ShortcutsBar` from `ChatScreen.tsx`**

Replace the entire file:

```tsx
/**
 * ChatScreen — the default inline-scroll chat surface.
 *
 * Layout:
 *   <Transcript />        ← <Static> committed turns, native scrollback
 *   [heartbeat banners]   ← HeartbeatBanner per unread unsolicited message (last 3)
 *   [notice strips]       ← NoticeStrip for instincts/perches/skills (last 3)
 *   <LiveTurn />          ← streaming live region (token.delta)
 *   <Composer />          ← bordered input box with footer inside
 */

import { Box } from "ink";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { Transcript } from "../components/Transcript.js";
import { HeartbeatBanner } from "../components/HeartbeatBanner.js";
import { NoticeStrip } from "../components/NoticeStrip.js";
import { LiveTurn } from "../components/LiveTurn.js";
import { Composer } from "../components/Composer.js";
import { CommandPalette } from "../components/CommandPalette.js";
import { SkillsOverlay } from "../components/SkillsOverlay.js";
import { McpOverlay } from "../components/McpOverlay.js";
import { globalBridge } from "../events/bridge.js";

export interface ChatScreenProps {
  onSubmit: (text: string) => void;
}

export function ChatScreen({ onSubmit }: ChatScreenProps) {
  const turns        = useUiStore((s) => s.turns);
  const liveTurn     = useUiStore((s) => s.liveTurn);
  const toolCalls    = useUiStore((s) => s.toolCalls);
  const heartbeats   = useUiStore((s) => s.heartbeats);
  const notices      = useUiStore((s) => s.notices);
  const generating   = useUiStore((s) => s.generating);
  const showHelp     = useUiStore((s) => s.showHelp);
  const showSkillsOverlay = useUiStore((s) => s.showSkillsOverlay);
  const showMcpOverlay    = useUiStore((s) => s.showMcpOverlay);

  const unreadHeartbeats = heartbeats.filter((msg) => !msg.read).slice(-3);
  const recentNotices    = notices.slice(-3);
  const activeCalls      = Array.from(toolCalls.values());

  return (
    <Box flexDirection="column">
      <Transcript turns={turns} />
      {unreadHeartbeats.map((msg) => (
        <HeartbeatBanner key={msg.id} msg={msg} />
      ))}
      {recentNotices.map((n) => (
        <NoticeStrip key={n.id} notice={n} />
      ))}
      <LiveTurn turn={liveTurn} toolCalls={activeCalls} />
      {showHelp && <CommandPalette onClose={() => globalBridge.dismissHelpView()} />}
      {showSkillsOverlay && <SkillsOverlay />}
      {showMcpOverlay && <McpOverlay />}
      <Composer
        onSubmit={onSubmit}
        disabled={generating || showHelp || showSkillsOverlay || showMcpOverlay}
      />
    </Box>
  );
}
```

- [ ] **Step 2: Delete `ShortcutsBar.tsx`**

```bash
git rm src/cli/v2/components/ShortcutsBar.tsx
```

- [ ] **Step 3: Type-check — confirms no remaining imports of ShortcutsBar**

```bash
npx tsc --noEmit
```

Expected: no errors. If you see "cannot find module ShortcutsBar", grep for stray imports:
```bash
grep -r "ShortcutsBar" src/
```
Fix any found imports.

- [ ] **Step 4: Run full test suite**

```bash
npm run test
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/screens/ChatScreen.tsx
git commit -m "feat(tui-v2): remove ShortcutsBar; footer now lives inside Composer box"
```

---

### Task 7: Update `ParliamentScreen` — StackOwl spinner + amber

**Files:**
- Modify: `src/cli/v2/screens/ParliamentScreen.tsx`

- [ ] **Step 1: Open `src/cli/v2/screens/ParliamentScreen.tsx`**

Current line 23 defines:
```ts
const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
```

- [ ] **Step 2: Replace the braille SPINNER with StackOwl spinner + amber in `ParliamentScreen.tsx`**

Make three edits:

**Edit 1** — replace the SPINNER constant (line 23):

Old:
```ts
const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
```

New:
```ts
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "../components/spinner.js";
```

Move the import to the top of the file alongside the other imports. Add it after the last existing import line.

**Edit 2** — in `OwlColumn` (around line 69), replace the spinner color and array reference:

Old:
```tsx
spinning && !ready
  ? `${SPINNER[spinFrame]} ${status}`
  : `[${status}]`
```

New:
```tsx
spinning && !ready
  ? `${STACKOWL_SPINNER[spinFrame]} ${status}`
  : `[${status}]`
```

**Edit 3** — in `ParliamentScreen` useEffect (around line 125), replace the interval constant:

Old:
```ts
const t = setInterval(() => setSpinFrame((f) => (f + 1) % SPINNER.length), 80);
```

New:
```ts
const t = setInterval(() => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
```

**Edit 4** — in `OwlColumn` component header, add amber color to the spinner text. Find the spinner glyph text (inside the `<Text>` that renders the status string containing the spinner). The current rendering is inside a single `<Text>` element. To color the spinner glyph amber, split it:

Old (around line 68-70):
```tsx
<Text color={ready ? (challenge ? "yellow" : "green") : "gray"} dimColor={!ready}>
  {spinning && !ready
    ? `${STACKOWL_SPINNER[spinFrame]} ${status}`
    : `[${status}]`
  }
</Text>
```

New:
```tsx
{spinning && !ready ? (
  <>
    <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[spinFrame]} </Text>
    <Text dimColor>{status}</Text>
  </>
) : (
  <Text color={ready ? (challenge ? "yellow" : "green") : "gray"} dimColor={!ready}>
    [{status}]
  </Text>
)}
```

- [ ] **Step 3: Type-check**

```bash
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 4: Run full test suite**

```bash
npm run test
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/screens/ParliamentScreen.tsx
git commit -m "feat(tui-v2): ParliamentScreen uses StackOwl amber spinner"
```

---

### Task 8: Smoke test the full TUI

- [ ] **Step 1: Start the dev server**

```bash
STACKOWL_TUI=v2 npm run dev
```

Expected: TUI boots without error, shows Composer bordered box in terminal

- [ ] **Step 2: Verify idle Composer**

- Box border visible (`╭` top-left, `╰` bottom-left)
- `❯ ` bold green cursor visible
- `/help · /owls · /sessions · /skills · /mcp` hint row visible
- Footer row with owl name + model visible

- [ ] **Step 3: Type a message and send**

Type any message, press Enter.

Expected:
- User turn appears: `❯ You` bold green, message indented 2 spaces
- Composer spinner animates during generation (`·◌◍◉✳✶` in amber)
- Owl response appears: emoji + **bold amber name** + dim role, text indented 2 spaces
- After response, Composer returns to idle state

- [ ] **Step 4: Trigger a tool call**

Ask something that triggers a tool (e.g. "what files are in the current directory").

Expected:
- Tool card shows amber spinner while running: `✳ bash  ls ...`
- After done: `└ bash  ✓  0.3s`

- [ ] **Step 5: Verify heartbeat banner (if active)**

If a heartbeat fires, verify the banner uses purple (`#A78BFA`) border instead of magenta.

- [ ] **Step 6: Open Parliament (Ctrl+P)**

Press `Ctrl+P` to open Parliament screen.

Expected: owl columns render with amber spinner while thinking, `[ready]` green badge when done.

- [ ] **Step 7: Run lint**

```bash
npm run lint
```

Expected: no errors

- [ ] **Step 8: Final commit if any clean-up needed**

```bash
git add -p
git commit -m "chore(tui-v2): post-polish smoke-test fixes"
```
