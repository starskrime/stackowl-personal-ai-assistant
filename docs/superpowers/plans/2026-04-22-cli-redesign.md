# CLI Redesign — Component-Based Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the CLI layer so `CLIAdapter` is a pure transport (like TelegramAdapter), backed by a component-based `TerminalRenderer` where each UI piece is an isolated pure function.

**Architecture:** Extract all rendering into `TerminalRenderer` which composes six stateless components (TopBar, LeftPanel, RightPanel, InputBox, CmdPopup, ShortcutsBar). A separate `InputHandler` owns raw stdin capture. Shared utilities (ansi, palette, text) replace duplicated code across the deleted `ui.ts` and `home.ts`.

**Tech Stack:** TypeScript (NodeNext modules), chalk, vitest, Node.js process.stdout/stdin

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `src/cli/shared/ansi.ts` | ANSI escape constants and helpers |
| Create | `src/cli/shared/palette.ts` | Color constants (AMBER, BLUE, etc.) |
| Create | `src/cli/shared/text.ts` | stripAnsi, visLen, padR, trunc, wrapText |
| Create | `src/cli/layout.ts` | Terminal geometry (cols, rows, leftW, rightW) |
| Create | `src/cli/components/top-bar.ts` | Pure render: owl badge + stats row |
| Create | `src/cli/components/input-box.ts` | Pure render: amber-bordered prompt |
| Create | `src/cli/components/shortcuts-bar.ts` | Pure render: key hints bar |
| Create | `src/cli/components/cmd-popup.ts` | Pure render: /command autocomplete |
| Create | `src/cli/components/left-panel.ts` | Pure render: sidebar (home + session modes) |
| Create | `src/cli/components/right-panel.ts` | Pure render: conversation area |
| Create | `src/cli/input-handler.ts` | Raw keystroke capture, buffer, history |
| Create | `src/cli/renderer.ts` | Stateful compositor — owns all components |
| Modify | `src/gateway/adapters/cli.ts` | Slim to pure transport |
| Modify | `src/cli/onboarding-flow.ts` | Use TerminalRenderer instead of TerminalUI |
| Delete | `src/cli/ui.ts` | Replaced by renderer + components |
| Delete | `src/cli/home.ts` | Replaced by renderer home-mode |
| Create | `__tests__/cli/text.test.ts` | Tests for shared text utils |
| Create | `__tests__/cli/layout.test.ts` | Tests for layout geometry |
| Create | `__tests__/cli/top-bar.test.ts` | Tests for TopBar component |
| Create | `__tests__/cli/input-box.test.ts` | Tests for InputBox component |
| Create | `__tests__/cli/cmd-popup.test.ts` | Tests for CmdPopup component |
| Create | `__tests__/cli/left-panel.test.ts` | Tests for LeftPanel component |
| Create | `__tests__/cli/right-panel.test.ts` | Tests for RightPanel component |
| Create | `__tests__/cli/input-handler.test.ts` | Tests for InputHandler |

---

## Task 1: Shared utilities

**Files:**
- Create: `src/cli/shared/ansi.ts`
- Create: `src/cli/shared/palette.ts`
- Create: `src/cli/shared/text.ts`
- Create: `__tests__/cli/text.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/text.test.ts
import { describe, it, expect } from "vitest";
import { stripAnsi, visLen, padR, trunc, wrapText } from "../../src/cli/shared/text.js";

describe("stripAnsi", () => {
  it("removes ANSI escape sequences", () => {
    expect(stripAnsi("\x1B[32mhello\x1B[0m")).toBe("hello");
  });
  it("returns plain string unchanged", () => {
    expect(stripAnsi("hello")).toBe("hello");
  });
});

describe("visLen", () => {
  it("measures plain string length", () => {
    expect(visLen("hello")).toBe(5);
  });
  it("ignores ANSI codes", () => {
    expect(visLen("\x1B[32mhi\x1B[0m")).toBe(2);
  });
});

describe("padR", () => {
  it("pads to target width", () => {
    expect(padR("ab", 5)).toBe("ab   ");
  });
  it("does not truncate if already wide", () => {
    expect(padR("abcde", 3)).toBe("abcde");
  });
});

describe("trunc", () => {
  it("truncates long strings with ellipsis", () => {
    expect(trunc("hello world", 7)).toBe("hello w…");
  });
  it("leaves short strings unchanged", () => {
    expect(trunc("hi", 10)).toBe("hi");
  });
});

describe("wrapText", () => {
  it("wraps long lines at word boundaries", () => {
    const result = wrapText("hello world foo", 11);
    expect(result).toEqual(["hello world", "foo"]);
  });
  it("preserves empty lines", () => {
    expect(wrapText("a\n\nb", 80)).toEqual(["a", "", "b"]);
  });
  it("hard-wraps when no space available", () => {
    const result = wrapText("abcdefghij", 5);
    expect(result[0]).toBe("abcde");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/text.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create shared/ansi.ts**

```typescript
// src/cli/shared/ansi.ts
export const ESC = "\x1B";

export const ansi = {
  altIn:  `${ESC}[?1049h`,
  altOut: `${ESC}[?1049l`,
  hide:   `${ESC}[?25l`,
  show:   `${ESC}[?25h`,
  clear:  `${ESC}[2J\x1B[1;1H`,
  el:     `${ESC}[2K`,
  pos:    (r: number, c = 1) => `${ESC}[${r};${c}H`,
};
```

- [ ] **Step 4: Create shared/palette.ts**

```typescript
// src/cli/shared/palette.ts
import chalk from "chalk";

export const AMBER    = chalk.rgb(250, 179, 135);
export const BLUE     = chalk.rgb(137, 180, 250);
export const GREEN    = chalk.rgb(166, 227, 161);
export const PURPLE   = chalk.rgb(203, 166, 247);
export const W        = chalk.rgb(205, 214, 244);
export const LBL      = chalk.rgb(69, 71, 90);
export const MUT      = chalk.rgb(46, 46, 69);
export const R        = chalk.rgb(243, 139, 168);
export const PANEL_BG   = chalk.bgRgb(12, 12, 24);
export const CONTENT_BG = chalk.bgRgb(8, 8, 16);

export const DIV = "━";
export const PANEL_V = AMBER(" │ ");
export const SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
```

- [ ] **Step 5: Create shared/text.ts**

```typescript
// src/cli/shared/text.ts
export function stripAnsi(s: string): string {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}

export function visLen(s: string): number {
  const plain = stripAnsi(s);
  let len = 0;
  for (const ch of plain) {
    const cp = ch.codePointAt(0) ?? 0;
    len += cp > 0xffff ? 2 : 1;
  }
  return len;
}

export function padR(s: string, w: number): string {
  return s + " ".repeat(Math.max(0, w - visLen(s)));
}

export function trunc(s: string, max: number): string {
  const plain = stripAnsi(s);
  return plain.length > max ? plain.slice(0, max - 1) + "…" : s;
}

export function wrapText(text: string, maxCols: number): string[] {
  const result: string[] = [];
  for (const para of text.split("\n")) {
    if (!para) { result.push(""); continue; }
    let rem = para;
    while (rem.length > maxCols) {
      let bp = rem.lastIndexOf(" ", maxCols);
      if (bp < 0) bp = maxCols;
      result.push(rem.slice(0, bp));
      rem = rem.slice(bp).trimStart();
    }
    if (rem) result.push(rem);
  }
  return result;
}
```

- [ ] **Step 6: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/text.test.ts
```
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/cli/shared/ __tests__/cli/text.test.ts
git commit -m "feat(cli): add shared ansi, palette, text utilities"
```

---

## Task 2: Layout

**Files:**
- Create: `src/cli/layout.ts`
- Create: `__tests__/cli/layout.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/layout.test.ts
import { describe, it, expect } from "vitest";
import { computeLayout } from "../../src/cli/layout.js";

describe("computeLayout", () => {
  it("respects minimum cols/rows", () => {
    const layout = computeLayout(40, 10);
    expect(layout.cols).toBe(80);
    expect(layout.rows).toBe(20);
  });

  it("computes leftW and rightW that sum to cols - 4", () => {
    const layout = computeLayout(120, 40);
    expect(layout.leftW + layout.rightW).toBe(layout.cols - 4);
  });

  it("leftW is at least 32", () => {
    const layout = computeLayout(80, 24);
    expect(layout.leftW).toBeGreaterThanOrEqual(32);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/layout.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create layout.ts**

```typescript
// src/cli/layout.ts
export interface Layout {
  cols: number;
  rows: number;
  leftW: number;
  rightW: number;
}

/** Compute terminal layout from given dimensions (defaults to process.stdout). */
export function computeLayout(rawCols?: number, rawRows?: number): Layout {
  const cols  = Math.max(rawCols  ?? process.stdout.columns ?? 100, 80);
  const rows  = Math.max(rawRows  ?? process.stdout.rows    ?? 30,  20);
  const leftW = Math.max(32, Math.floor((cols - 4) * 0.38));
  const rightW = cols - 4 - leftW;
  return { cols, rows, leftW, rightW };
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/layout.test.ts
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/layout.ts __tests__/cli/layout.test.ts
git commit -m "feat(cli): add layout geometry helper"
```

---

## Task 3: TopBar component

**Files:**
- Create: `src/cli/components/top-bar.ts`
- Create: `__tests__/cli/top-bar.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/top-bar.test.ts
import { describe, it, expect } from "vitest";
import { renderTopBar, type TopBarProps } from "../../src/cli/components/top-bar.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const base: TopBarProps = { owlEmoji: "🦉", owlName: "Atlas", model: "sonnet-3-5", turn: 0, tokens: 0, cost: 0 };

describe("TopBar", () => {
  it("includes owl name", () => {
    expect(stripAnsi(renderTopBar(base, 100))).toContain("Atlas");
  });
  it("shows turn when turn > 0", () => {
    expect(stripAnsi(renderTopBar({ ...base, turn: 3 }, 100))).toContain("turn 3");
  });
  it("omits turn when turn is 0", () => {
    expect(stripAnsi(renderTopBar(base, 100))).not.toContain("turn");
  });
  it("shows cost when cost > 0", () => {
    expect(stripAnsi(renderTopBar({ ...base, cost: 0.005 }, 100))).toContain("$0.005");
  });
  it("omits cost when cost is 0", () => {
    expect(stripAnsi(renderTopBar(base, 100))).not.toContain("$");
  });
  it("strips claude- prefix from model", () => {
    expect(stripAnsi(renderTopBar({ ...base, model: "claude-sonnet-3-5" }, 100))).toContain("sonnet-3-5");
    expect(stripAnsi(renderTopBar({ ...base, model: "claude-sonnet-3-5" }, 100))).not.toContain("claude-");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/top-bar.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create components/top-bar.ts**

```typescript
// src/cli/components/top-bar.ts
import chalk from "chalk";
import { AMBER, BLUE, GREEN, PURPLE, MUT, LBL, PANEL_BG } from "../shared/palette.js";
import { padR, visLen } from "../shared/text.js";

export interface TopBarProps {
  owlEmoji: string;
  owlName:  string;
  model:    string;
  turn:     number;
  tokens:   number;
  cost:     number;
}

const DIV = "━";

export function renderTopBar(props: TopBarProps, cols: number): string {
  const { owlEmoji, owlName, model, turn, tokens, cost } = props;
  const inner = cols - 2;

  const badge    = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(` ${owlEmoji} ${owlName} `);
  const modelStr = model ? ` ${MUT("[")}${BLUE(model.replace("claude-", "").slice(0, 18))}${MUT("]")}` : "";
  const turnStr  = turn   > 0 ? ` ${MUT("·")} ${PURPLE("turn " + turn)}` : "";
  const toksStr  = tokens > 0 ? ` ${MUT("·")} ${LBL((tokens / 1000).toFixed(1) + "k")}` : "";
  const costStr  = cost   > 0 ? ` ${MUT("·")} ${GREEN("$" + cost.toFixed(3))}` : "";

  const content = badge + modelStr + turnStr + toksStr + costStr;
  const row2 = PANEL_BG("  " + padR(content, inner - 2) + "  ");
  const row3 = PANEL_BG(AMBER(DIV.repeat(cols)));
  return row2 + row3;
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/top-bar.test.ts
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/components/top-bar.ts __tests__/cli/top-bar.test.ts
git commit -m "feat(cli): add TopBar component"
```

---

## Task 4: InputBox component

**Files:**
- Create: `src/cli/components/input-box.ts`
- Create: `__tests__/cli/input-box.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/input-box.test.ts
import { describe, it, expect } from "vitest";
import { renderInputBox, type InputBoxProps } from "../../src/cli/components/input-box.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const base: InputBoxProps = { buf: "", cursor: 0, locked: false, masked: false, spinIdx: 0 };

describe("InputBox", () => {
  it("shows prompt arrow when unlocked", () => {
    expect(stripAnsi(renderInputBox(base, 60))).toContain("›");
  });
  it("shows thinking message when locked", () => {
    expect(stripAnsi(renderInputBox({ ...base, locked: true }, 60))).toContain("thinking");
  });
  it("shows buffer content", () => {
    expect(stripAnsi(renderInputBox({ ...base, buf: "hello", cursor: 5 }, 60))).toContain("hello");
  });
  it("masks buffer when masked=true", () => {
    const out = stripAnsi(renderInputBox({ ...base, buf: "secret", cursor: 6, masked: true }, 60));
    expect(out).not.toContain("secret");
    expect(out).toContain("*");
  });
  it("returns three lines (top border, content, bottom border)", () => {
    const lines = stripAnsi(renderInputBox(base, 60)).split("\n");
    expect(lines.length).toBe(3);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/input-box.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create components/input-box.ts**

```typescript
// src/cli/components/input-box.ts
import chalk from "chalk";
import { AMBER, BLUE, LBL, W, PANEL_BG } from "../shared/palette.js";
import { visLen } from "../shared/text.js";

export interface InputBoxProps {
  buf:     string;
  cursor:  number;
  locked:  boolean;
  masked:  boolean;
  spinIdx: number;
}

const SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];

export function renderInputBox(props: InputBoxProps, width: number): string {
  const content  = buildContentLine(props);
  const topBorder = PANEL_BG(AMBER("▔".repeat(width + 2)));
  const body      = PANEL_BG(" " + content + " ".repeat(Math.max(0, width - visLen(content))) + " ");
  const botBorder = PANEL_BG(AMBER("▁".repeat(width + 2)));
  return topBorder + "\n" + body + "\n" + botBorder;
}

function buildContentLine(props: InputBoxProps): string {
  const { buf, cursor, locked, masked, spinIdx } = props;
  if (locked) {
    return "  " + BLUE(SPINNER[spinIdx % SPINNER.length]) + LBL("  thinking — press ESC to stop");
  }
  const prefix = "  " + AMBER("› ");
  let before: string, atCur: string, after: string;
  if (masked) {
    before = "*".repeat(cursor);
    atCur  = buf[cursor] ? "*" : " ";
    after  = "*".repeat(Math.max(0, buf.length - cursor - 1));
  } else {
    before = buf.slice(0, cursor);
    atCur  = buf[cursor] ?? " ";
    after  = buf.slice(cursor + 1);
  }
  return prefix + W(before) + chalk.bgYellow.black(atCur) + W(after);
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/input-box.test.ts
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/components/input-box.ts __tests__/cli/input-box.test.ts
git commit -m "feat(cli): add InputBox component"
```

---

## Task 5: ShortcutsBar + CmdPopup components

**Files:**
- Create: `src/cli/components/shortcuts-bar.ts`
- Create: `src/cli/components/cmd-popup.ts`
- Create: `__tests__/cli/cmd-popup.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/cmd-popup.test.ts
import { describe, it, expect } from "vitest";
import { renderCmdPopup, type CmdPopupProps } from "../../src/cli/components/cmd-popup.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

describe("CmdPopup", () => {
  it("returns empty array when no matches", () => {
    expect(renderCmdPopup({ matches: [], selectedIdx: 0 }, 40)).toEqual([]);
  });
  it("renders one line per match plus border", () => {
    const lines = renderCmdPopup({ matches: ["help", "status"], selectedIdx: 0 }, 40);
    expect(lines.length).toBe(3); // 2 items + border
  });
  it("caps at 8 visible items", () => {
    const matches = ["a","b","c","d","e","f","g","h","i","j"];
    const lines = renderCmdPopup({ matches, selectedIdx: 0 }, 40);
    expect(lines.length).toBe(9); // 8 items + border
  });
  it("includes match text in output", () => {
    const lines = renderCmdPopup({ matches: ["help"], selectedIdx: 0 }, 40);
    expect(stripAnsi(lines[0])).toContain("help");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/cmd-popup.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create components/shortcuts-bar.ts**

```typescript
// src/cli/components/shortcuts-bar.ts
import chalk from "chalk";
import { LBL, PANEL_BG } from "../shared/palette.js";
import { padR } from "../shared/text.js";

export interface ShortcutEntry { key: string; label: string; }

export function renderShortcutsBar(shortcuts: ShortcutEntry[], cols: number): string {
  const key  = (k: string) => chalk.bgRgb(26, 26, 44).rgb(205, 214, 244).bold(` ${k} `);
  const line = shortcuts.map(s => key(s.key) + LBL("  " + s.label)).join("     ");
  return PANEL_BG(padR(line, cols - 4));
}
```

- [ ] **Step 4: Create components/cmd-popup.ts**

```typescript
// src/cli/components/cmd-popup.ts
import chalk from "chalk";
import { AMBER, W } from "../shared/palette.js";
import { visLen } from "../shared/text.js";

export interface CmdPopupProps {
  matches:     string[];
  selectedIdx: number;
}

const POPUP_BG = chalk.bgRgb(28, 28, 52);

export function renderCmdPopup(props: CmdPopupProps, width: number): string[] {
  const { matches, selectedIdx } = props;
  if (matches.length === 0) return [];

  const visible = matches.slice(0, 8);
  const itemW   = width - 3;
  const lines: string[] = [];

  for (let i = 0; i < visible.length; i++) {
    const cmd = visible[i];
    const pad = " ".repeat(Math.max(0, itemW - visLen(" " + cmd + " ")));
    if (i === selectedIdx) {
      lines.push(AMBER("▌") + chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" " + cmd + " " + pad));
    } else {
      lines.push(AMBER("▌") + POPUP_BG(W(" " + cmd + " " + pad)));
    }
  }
  lines.push(POPUP_BG(AMBER("▁".repeat(width - 1))));
  return lines;
}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/cmd-popup.test.ts
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/cli/components/shortcuts-bar.ts src/cli/components/cmd-popup.ts __tests__/cli/cmd-popup.test.ts
git commit -m "feat(cli): add ShortcutsBar and CmdPopup components"
```

---

## Task 6: LeftPanel component

**Files:**
- Create: `src/cli/components/left-panel.ts`
- Create: `__tests__/cli/left-panel.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/left-panel.test.ts
import { describe, it, expect } from "vitest";
import { renderLeftPanel, type LeftPanelProps } from "../../src/cli/components/left-panel.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const homeBase: LeftPanelProps = {
  mode: "home", owlState: "idle", spinIdx: 0,
  dna: { challenge: 5, verbosity: 5, mood: 7 }, toolCalls: [],
  instincts: 0, memFacts: 0, skillsHit: 0,
  owlEmoji: "🦉", owlName: "Atlas", generation: 3, challenge: 7,
  provider: "anthropic", model: "claude-sonnet-3-5", skills: 4,
};
const sessionBase: LeftPanelProps = { ...homeBase, mode: "session" };

describe("LeftPanel home mode", () => {
  it("shows owl name", () => {
    const lines = renderLeftPanel(homeBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("Atlas"))).toBe(true);
  });
  it("shows provider", () => {
    const lines = renderLeftPanel(homeBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("anthropic"))).toBe(true);
  });
  it("returns exactly `rows` lines", () => {
    expect(renderLeftPanel(homeBase, 40, 20).length).toBe(20);
  });
});

describe("LeftPanel session mode", () => {
  it("shows OWL MIND section", () => {
    const lines = renderLeftPanel(sessionBase, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("OWL MIND"))).toBe(true);
  });
  it("shows thinking indicator when state is thinking", () => {
    const lines = renderLeftPanel({ ...sessionBase, owlState: "thinking" }, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("thinking"))).toBe(true);
  });
  it("shows tool call names", () => {
    const props = { ...sessionBase, toolCalls: [{ name: "web_fetch", args: "", status: "done" as const, ms: 120 }] };
    const lines = renderLeftPanel(props, 40, 20);
    expect(lines.some(l => stripAnsi(l).includes("web_fetch"))).toBe(true);
  });
  it("shows FIREWALL footer", () => {
    const lines = renderLeftPanel(sessionBase, 40, 20);
    expect(stripAnsi(lines[lines.length - 1])).toContain("FIREWALL");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/left-panel.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create components/left-panel.ts**

```typescript
// src/cli/components/left-panel.ts
import chalk from "chalk";
import { AMBER, BLUE, GREEN, PURPLE, W, LBL, MUT, R } from "../shared/palette.js";
import { SPINNER } from "../shared/palette.js";
import { trunc } from "../shared/text.js";

export type OwlState = "idle" | "thinking" | "done" | "error";

export interface ToolEntry {
  name:    string;
  args:    string;
  status:  "running" | "done" | "error";
  summary?: string;
  ms?:     number;
}

export interface LeftPanelProps {
  mode:      "home" | "session";
  owlState:  OwlState;
  spinIdx:   number;
  dna:       { challenge: number; verbosity: number; mood: number };
  toolCalls: ToolEntry[];
  instincts: number;
  memFacts:  number;
  skillsHit: number;
  owlEmoji:  string;
  owlName:   string;
  generation: number;
  challenge:  number;
  provider:   string;
  model:      string;
  skills:     number;
}

const OWL_FACES = {
  idle:     " ( o  o ) ",
  thinking: [" ( -_- ) ", " ( o_- ) ", " ( -_o ) ", " ( o_o ) "],
  done:     " ( ^‿^ ) ",
  error:    " ( >_< ) ",
};

export function renderLeftPanel(props: LeftPanelProps, width: number, rows: number): string[] {
  const lines: string[] = [];
  const add   = (t: string) => lines.push(t);
  const blank = () => lines.push("");
  const secHdr = (label: string) =>
    "  " + AMBER.bold(label) + " " + MUT("─".repeat(Math.max(0, width - label.length - 5)));

  if (props.mode === "home") {
    blank();
    add("  " + chalk.bgRgb(250,179,135).rgb(8,8,16).bold(` ${props.owlEmoji} ${props.owlName} `));
    blank();
    add(secHdr("IDENTITY"));
    add("  " + LBL("Generation") + "  " + W(String(props.generation)));
    add("  " + LBL("Challenge ") + "  " + AMBER("⚡" + String(props.challenge)));
    blank();
    add(secHdr("BACKEND"));
    add("  " + LBL("Provider") + "   " + BLUE(props.provider));
    add("  " + LBL("Model   ") + "   " + W(props.model.replace("claude-","").slice(0,14)));
    add("  " + LBL("Skills  ") + "   " + GREEN(String(props.skills) + " loaded"));
  } else {
    blank();
    add(secHdr("OWL MIND"));
    blank();
    add("  " + AMBER(currentFace(props.owlState, props.spinIdx)));
    if (props.owlState === "thinking") {
      add("  " + BLUE(SPINNER[props.spinIdx % SPINNER.length] + " thinking..."));
    }
    blank();
    add("  " + PURPLE("◆") + " " + LBL("Instincts") + "   " + (props.instincts > 0 ? AMBER.bold(props.instincts + " triggered") : MUT("—")));
    add("  " + PURPLE("◆") + " " + LBL("Memory   ") + "   " + (props.memFacts  > 0 ? AMBER.bold(props.memFacts  + " facts")     : MUT("—")));
    add("  " + PURPLE("◆") + " " + LBL("Skills   ") + "   " + (props.skillsHit > 0 ? GREEN.bold(props.skillsHit + " invoked")   : MUT("—")));
    blank();

    if (props.toolCalls.length > 0) {
      add(secHdr("REASONING"));
      const visible = props.toolCalls.slice(-8);
      visible.forEach((tc, i) => {
        const isLast = i === visible.length - 1;
        const branch = isLast ? MUT("  └ ") : MUT("  ├ ");
        const icon   = tc.status === "running" ? BLUE(SPINNER[props.spinIdx % SPINNER.length])
                     : tc.status === "done"    ? GREEN("✓")
                     : R("✕");
        const name   = tc.status === "running" ? BLUE(trunc(tc.name, width - 18)) : W(trunc(tc.name, width - 18));
        const ms     = tc.ms ? MUT(" " + tc.ms + "ms") : "";
        add(branch + icon + " " + name + ms);
        if (tc.summary) {
          add((isLast ? "        " : "  │     ") + LBL(trunc(tc.summary, width - 12)));
        }
      });
      blank();
    }

    const remaining = rows - lines.length - 7;
    if (remaining > 4) {
      blank();
      add(secHdr("DNA"));
      blank();
      add("  " + dnaBar("challenge", props.dna.challenge, "challenge"));
      add("  " + dnaBar("verbosity", props.dna.verbosity, "verbosity"));
      add("  " + dnaBar("mood     ", props.dna.mood,      "mood"));
    }
  }

  while (lines.length < rows - 1) lines.push("");
  lines.push("  " + MUT("─".repeat(Math.max(0, width - 4))) + " " + MUT("FIREWALL"));
  return lines.slice(0, rows);
}

function currentFace(state: OwlState, spinIdx: number): string {
  if (state === "thinking") return (OWL_FACES.thinking as string[])[Math.floor(spinIdx / 6) % 4];
  if (state === "done")     return OWL_FACES.done;
  if (state === "error")    return OWL_FACES.error;
  return OWL_FACES.idle;
}

function dnaBar(label: string, val: number, trait: "challenge" | "verbosity" | "mood"): string {
  const v     = Math.max(0, Math.min(10, Math.round(val)));
  const color = trait === "challenge" ? AMBER : trait === "verbosity" ? BLUE : GREEN;
  return LBL(label) + " " + color("█").repeat(v) + MUT("█").repeat(10 - v) + " " + MUT(String(val));
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/left-panel.test.ts
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/components/left-panel.ts __tests__/cli/left-panel.test.ts
git commit -m "feat(cli): add LeftPanel component (home + session modes)"
```

---

## Task 7: RightPanel component

**Files:**
- Create: `src/cli/components/right-panel.ts`
- Create: `__tests__/cli/right-panel.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/right-panel.test.ts
import { describe, it, expect } from "vitest";
import { renderRightPanel, type RightPanelProps } from "../../src/cli/components/right-panel.js";
import { stripAnsi } from "../../src/cli/shared/text.js";

const homeBase: RightPanelProps = { mode: "home", lines: [], scrollOff: 0, recentSessions: [] };
const sessionBase: RightPanelProps = { mode: "session", lines: [], scrollOff: 0, recentSessions: [] };

describe("RightPanel home mode", () => {
  it("shows prompt label", () => {
    const lines = renderRightPanel(homeBase, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("What do you want to work on?"))).toBe(true);
  });
  it("shows recent session titles", () => {
    const props = { ...homeBase, recentSessions: [{ title: "My session", turns: 5, ago: "2h" }] };
    const lines = renderRightPanel(props, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("My session"))).toBe(true);
  });
  it("returns exactly `rows` lines", () => {
    expect(renderRightPanel(homeBase, 60, 20).length).toBe(20);
  });
});

describe("RightPanel session mode", () => {
  it("shows empty prompt when no lines", () => {
    const lines = renderRightPanel(sessionBase, 60, 20);
    expect(lines.some(l => stripAnsi(l).includes("What do you want to work on?"))).toBe(true);
  });
  it("shows conversation lines", () => {
    const props = { ...sessionBase, lines: ["  Hello there"] };
    const lines = renderRightPanel(props, 60, 20);
    expect(lines.some(l => l.includes("Hello there"))).toBe(true);
  });
  it("ends with divider line", () => {
    const lines = renderRightPanel(sessionBase, 60, 20);
    expect(stripAnsi(lines[lines.length - 1])).toMatch(/━+/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/right-panel.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create components/right-panel.ts**

```typescript
// src/cli/components/right-panel.ts
import { LBL, MUT, W } from "../shared/palette.js";
import { trunc, visLen } from "../shared/text.js";

export interface RecentSession { title: string; turns: number; ago: string; }

export interface RightPanelProps {
  mode:            "home" | "session";
  lines:           string[];
  scrollOff:       number;
  recentSessions:  RecentSession[];
}

const DIV = "━";

export function renderRightPanel(props: RightPanelProps, width: number, rows: number): string[] {
  const result:   string[] = [];
  const convRows = rows - 1;

  if (props.mode === "home") {
    const centerRow = Math.floor(rows / 2) - 1;
    for (let i = 0; i < centerRow; i++) result.push("");
    const label    = "What do you want to work on?";
    const labelPad = Math.max(0, Math.floor((width - label.length) / 2));
    result.push(" ".repeat(labelPad) + LBL(label));
    result.push(""); // input box occupies this row

    const sessions = props.recentSessions.slice(0, 3);
    if (sessions.length > 0) {
      result.push("");
      result.push("  " + MUT("─".repeat(Math.max(0, width - 4))));
      result.push("  " + LBL("recent sessions"));
      result.push("");
      for (const s of sessions) {
        const title   = trunc(s.title, width - 24);
        const turns   = MUT(String(s.turns) + "t");
        const ago     = MUT(s.ago);
        const spacer  = " ".repeat(Math.max(1, width - 2 - visLen(title) - visLen(String(s.turns) + "t") - visLen(s.ago) - 4));
        result.push("  " + W(title) + spacer + turns + "  " + ago);
      }
    }
  } else {
    if (props.lines.length === 0) {
      result.push("  " + LBL("What do you want to work on?"));
    } else {
      const total = props.lines.length;
      const end   = Math.max(0, total - props.scrollOff);
      const start = Math.max(0, end - convRows);
      const vis   = props.lines.slice(start, end);
      for (let i = 0; i < convRows; i++) result.push(vis[i] ?? "");
    }
  }

  while (result.length < convRows) result.push("");
  result.push("  " + LBL(DIV.repeat(Math.max(0, width - 4))));
  return result.slice(0, rows);
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/right-panel.test.ts
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/components/right-panel.ts __tests__/cli/right-panel.test.ts
git commit -m "feat(cli): add RightPanel component (home + session modes)"
```

---

## Task 8: InputHandler

**Files:**
- Create: `src/cli/input-handler.ts`
- Create: `__tests__/cli/input-handler.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/cli/input-handler.test.ts
import { describe, it, expect, vi } from "vitest";
import { InputHandler } from "../../src/cli/input-handler.js";

describe("InputHandler", () => {
  it("emits line on Enter", () => {
    const h = new InputHandler();
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("h"); h.feed("i"); h.feed("\r");
    expect(onLine).toHaveBeenCalledWith("hi");
  });

  it("does not emit empty line by default", () => {
    const h = new InputHandler();
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("\r");
    expect(onLine).not.toHaveBeenCalled();
  });

  it("emits empty line when allowEmpty=true", () => {
    const h = new InputHandler();
    h.setAllowEmpty(true);
    const onLine = vi.fn();
    h.on("line", onLine);
    h.feed("\r");
    expect(onLine).toHaveBeenCalledWith("");
  });

  it("handles backspace correctly", () => {
    const h = new InputHandler();
    h.feed("h"); h.feed("i"); h.feed("\x7f");
    expect(h.buf).toBe("h");
    expect(h.cursor).toBe(1);
  });

  it("emits quit on Ctrl+C", () => {
    const h = new InputHandler();
    const onQuit = vi.fn();
    h.on("quit", onQuit);
    h.feed("\x03");
    expect(onQuit).toHaveBeenCalled();
  });

  it("activates cmd popup on /", () => {
    const h = new InputHandler();
    h.setCommandList(["help", "status"]);
    h.feed("/");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("filters popup matches as user types", () => {
    const h = new InputHandler();
    h.setCommandList(["help", "status", "skills"]);
    h.feed("/"); h.feed("s");
    expect(h.cmdMatches).toEqual(["status", "skills"]);
  });

  it("dismisses popup on ESC", () => {
    const h = new InputHandler();
    h.setCommandList(["help"]);
    h.feed("/");
    h.feed("\x1B");
    expect(h.cmdPopupActive).toBe(false);
  });

  it("emits change on each keystroke", () => {
    const h = new InputHandler();
    const onChange = vi.fn();
    h.on("change", onChange);
    h.feed("a");
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("does not accept input when locked", () => {
    const h = new InputHandler();
    h.setLocked(true);
    h.feed("a");
    expect(h.buf).toBe("");
  });

  it("clears buf and emits line after Enter, restoring unmasked state", () => {
    const h = new InputHandler();
    h.setMasked(true);
    h.feed("s"); h.feed("e"); h.feed("\r");
    expect(h.masked).toBe(false);
    expect(h.buf).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/input-handler.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create input-handler.ts**

```typescript
// src/cli/input-handler.ts
import { EventEmitter } from "node:events";

const ESC = "\x1B";

export class InputHandler extends EventEmitter {
  private _buf    = "";
  private _cursor = 0;
  private _history: string[]  = [];
  private _histIdx  = -1;
  private _histTemp = "";
  private _cmdPopupActive = false;
  private _cmdNames:   string[] = [];
  private _cmdMatches: string[] = [];
  private _cmdIdx  = 0;
  private _masked  = false;
  private _allowEmpty = false;
  private _locked  = false;

  // ─── State (read by renderer each frame) ─────────────────────

  get buf()            { return this._buf; }
  get cursor()         { return this._cursor; }
  get locked()         { return this._locked; }
  get masked()         { return this._masked; }
  get cmdPopupActive() { return this._cmdPopupActive; }
  get cmdMatches()     { return [...this._cmdMatches]; }
  get cmdIdx()         { return this._cmdIdx; }

  // ─── Configuration ────────────────────────────────────────────

  setCommandList(names: string[])  { this._cmdNames = names; }
  setMasked(on: boolean)           { this._masked = on; }
  setAllowEmpty(on: boolean)       { this._allowEmpty = on; }
  setLocked(on: boolean)           { this._locked = on; }
  setInitialInput(buf: string)     { this._buf = buf; this._cursor = buf.length; }

  // ─── Key feed ─────────────────────────────────────────────────

  /** Feed a raw stdin data chunk. Emits: "line", "quit", "change", "scroll", "clear". */
  feed(data: string): void {
    if (data === "\x03" || data === "\x04") { this.emit("quit"); return; }
    if (this._cmdPopupActive) { this._handlePopupKey(data); return; }
    this._handleNormalKey(data);
  }

  // ─── y/n prompt (used by askInstall) ─────────────────────────

  /** Pause normal input, wait for y/n keypress, then return. */
  async promptYesNo(): Promise<boolean> {
    return new Promise(resolve => {
      const onKey = (chunk: unknown) => {
        const k = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
        if (k.toLowerCase() === "y") { process.stdin.off("data", onKey); resolve(true); }
        else if (k.toLowerCase() === "n" || k === "\x03") { process.stdin.off("data", onKey); resolve(false); }
      };
      process.stdin.on("data", onKey);
    });
  }

  // ─── Private ──────────────────────────────────────────────────

  private _handleNormalKey(data: string): void {
    if (this._locked) return;

    if (data === "\r" || data === "\n") {
      const line = this._buf.trim();
      this._buf = ""; this._cursor = 0; this._histIdx = -1;
      if (line) {
        this._history.unshift(line);
        if (this._history.length > 100) this._history.pop();
        this._masked = false;
        this.emit("line", line);
      } else if (this._allowEmpty) {
        this.emit("line", "");
      }
      this.emit("change");
      return;
    }
    if (data === "\x7f") {
      if (this._cursor > 0) {
        this._buf = this._buf.slice(0, this._cursor - 1) + this._buf.slice(this._cursor);
        this._cursor--;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[A") {
      if (this._histIdx === -1) this._histTemp = this._buf;
      if (this._histIdx < this._history.length - 1) {
        this._histIdx++;
        this._buf    = this._history[this._histIdx];
        this._cursor = this._buf.length;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[B") {
      if (this._histIdx > -1) {
        this._histIdx--;
        this._buf    = this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
        this._cursor = this._buf.length;
        this.emit("change");
      }
      return;
    }
    if (data === ESC + "[D" && this._cursor > 0)                    { this._cursor--; this.emit("change"); return; }
    if (data === ESC + "[C" && this._cursor < this._buf.length)     { this._cursor++; this.emit("change"); return; }
    if (data === ESC + "[5~") { this.emit("scroll",  5); return; }
    if (data === ESC + "[6~") { this.emit("scroll", -5); return; }
    if (data === "\x0C")      { this.emit("clear");       return; }

    if (data === "/") {
      this._buf = "/"; this._cursor = 1;
      this._cmdPopupActive = true;
      this._updateMatches();
      this._cmdIdx = 0;
      this.emit("change");
      return;
    }
    if (data.length >= 1 && data >= " ") {
      this._buf    = this._buf.slice(0, this._cursor) + data + this._buf.slice(this._cursor);
      this._cursor += data.length;
      this.emit("change");
    }
  }

  private _handlePopupKey(data: string): void {
    if (data === ESC + "[A") { this._cmdIdx = Math.max(0, this._cmdIdx - 1); this.emit("change"); return; }
    if (data === ESC + "[B") { this._cmdIdx = Math.min(this._cmdMatches.length - 1, this._cmdIdx + 1); this.emit("change"); return; }
    if (data === "\r" || data === "\n") {
      const selected = this._cmdMatches[this._cmdIdx];
      if (selected) { this._buf = "/" + selected; this._cursor = this._buf.length; }
      this._cmdPopupActive = false;
      this.emit("change");
      return;
    }
    if (data === ESC) {
      this._buf = ""; this._cursor = 0;
      this._cmdPopupActive = false;
      this.emit("change");
      return;
    }
    if (data === "\x7f") {
      if (this._buf.length <= 1) { this._buf = ""; this._cursor = 0; this._cmdPopupActive = false; }
      else { this._buf = this._buf.slice(0, -1); this._cursor--; this._updateMatches(); }
      this.emit("change");
      return;
    }
    if (data.length >= 1 && data >= " ") {
      this._buf    = this._buf.slice(0, this._cursor) + data + this._buf.slice(this._cursor);
      this._cursor += data.length;
      this._updateMatches();
      this.emit("change");
    }
  }

  private _updateMatches(): void {
    const filter    = this._buf.slice(1).toLowerCase();
    this._cmdMatches = filter
      ? this._cmdNames.filter(n => n.startsWith(filter))
      : [...this._cmdNames];
    this._cmdIdx = 0;
    if (this._cmdMatches.length === 0) this._cmdPopupActive = false;
  }
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
npx vitest run __tests__/cli/input-handler.test.ts
```
Expected: all 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/input-handler.ts __tests__/cli/input-handler.test.ts
git commit -m "feat(cli): add InputHandler — keystroke capture, buffer, history, popup"
```

---

## Task 9: TerminalRenderer

**Files:**
- Create: `src/cli/renderer.ts`

- [ ] **Step 1: Create renderer.ts**

```typescript
// src/cli/renderer.ts
import { EventEmitter } from "node:events";
import chalk from "chalk";
import { ansi } from "./shared/ansi.js";
import { AMBER, CONTENT_BG, PANEL_BG, PANEL_V, W, LBL, R } from "./shared/palette.js";
import { visLen, wrapText } from "./shared/text.js";
import { computeLayout } from "./layout.js";
import { renderTopBar, type TopBarProps } from "./components/top-bar.js";
import { renderLeftPanel, type LeftPanelProps, type OwlState, type ToolEntry } from "./components/left-panel.js";
import { renderRightPanel, type RightPanelProps, type RecentSession } from "./components/right-panel.js";
import { renderInputBox } from "./components/input-box.js";
import { renderCmdPopup } from "./components/cmd-popup.js";
import { renderShortcutsBar, type ShortcutEntry } from "./components/shortcuts-bar.js";
import { InputHandler } from "./input-handler.js";
import type { GatewayResponse } from "../gateway/types.js";
import type { StreamEvent } from "../providers/base.js";

// ─── State types ──────────────────────────────────────────────────

interface RendererState {
  mode: "home" | "session";
  // TopBar
  owlEmoji:  string;
  owlName:   string;
  model:     string;
  turn:      number;
  tokens:    number;
  cost:      number;
  // LeftPanel
  owlState:   OwlState;
  spinIdx:    number;
  dna:        { challenge: number; verbosity: number; mood: number };
  toolCalls:  ToolEntry[];
  instincts:  number;
  memFacts:   number;
  skillsHit:  number;
  generation: number;
  challenge:  number;
  provider:   string;
  skills:     number;
  // RightPanel
  lines:           string[];
  scrollOff:       number;
  recentSessions:  RecentSession[];
  // streaming
  streaming:       boolean;
  streamHeaderIdx: number;
  streamBuf:       string;
}

const DEFAULT_SHORTCUTS: ShortcutEntry[] = [
  { key: "ESC", label: "Stop" },
  { key: "^P",  label: "Parliament" },
  { key: "^L",  label: "Clear" },
  { key: "^C",  label: "Quit" },
];

// ─── TerminalRenderer ─────────────────────────────────────────────

export class TerminalRenderer extends EventEmitter {
  readonly input: InputHandler;

  private _state: RendererState = {
    mode: "home",
    owlEmoji: "🦉", owlName: "Owl", model: "", turn: 0, tokens: 0, cost: 0,
    owlState: "idle", spinIdx: 0,
    dna: { challenge: 5, verbosity: 5, mood: 7 },
    toolCalls: [], instincts: 0, memFacts: 0, skillsHit: 0,
    generation: 1, challenge: 5, provider: "", skills: 0,
    lines: [], scrollOff: 0, recentSessions: [],
    streaming: false, streamHeaderIdx: -1, streamBuf: "",
  };

  private _thinkTimer:  ReturnType<typeof setInterval> | null = null;
  private _thinkStart   = 0;
  private _resizeTimer: ReturnType<typeof setTimeout>  | null = null;
  private _rendering    = false;
  private _renderQueued = false;
  private _closed       = false;

  constructor() {
    super();
    this.input = new InputHandler();
    this.input.on("change", () => this.redraw());
    this.input.on("quit",   () => this.emit("quit"));
    this.input.on("clear",  () => { this._state.lines = []; this._state.toolCalls = []; this._state.scrollOff = 0; this.redraw(); });
    this.input.on("scroll", (delta: number) => {
      const max = Math.max(0, this._state.lines.length - this._convRows());
      this._state.scrollOff = Math.max(0, Math.min(this._state.scrollOff + delta, max));
      this.redraw();
    });
  }

  // ─── Lifecycle ────────────────────────────────────────────────

  enter(): void {
    process.stdout.write(ansi.altIn + ansi.hide);
    if (process.stdin.isTTY) process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", this._keyHandler);
    process.stdout.on("resize", this._resizeHandler);
    setTimeout(() => this.redraw(), 40);
  }

  close(): void {
    this._closed = true;
    this._stopThink();
    process.stdin.off("data",   this._keyHandler);
    process.stdout.off("resize", this._resizeHandler);
    process.stdout.write(ansi.show + ansi.altOut);
    if (process.stdin.isTTY) {
      try { process.stdin.setRawMode(false); } catch { /**/ }
    }
  }

  // ─── Configuration ────────────────────────────────────────────

  setMode(mode: "home" | "session"): void {
    this._state.mode = mode;
    this.redraw();
  }

  setOwl(emoji: string, name: string, provider: string, model: string): void {
    Object.assign(this._state, { owlEmoji: emoji, owlName: name, provider, model });
  }

  updateDNA(dna: Partial<RendererState["dna"]>): void {
    Object.assign(this._state.dna, dna);
    this.redraw();
  }

  updateStats(tokens: number, cost: number): void {
    this._state.tokens = tokens;
    this._state.cost   = cost;
  }

  setRecentSessions(sessions: RecentSession[]): void {
    this._state.recentSessions = sessions;
  }

  setCommandList(names: string[]): void {
    this.input.setCommandList(names);
  }

  setInitialInput(buf: string): void {
    this.input.setInitialInput(buf);
  }

  setMasked(on: boolean):     void { this.input.setMasked(on); }
  setAllowEmptyInput(on: boolean): void { this.input.setAllowEmpty(on); }

  // ─── Public output API ────────────────────────────────────────

  showThinking(): void {
    this.input.setLocked(true);
    this._state.owlState = "thinking";
    this._thinkStart = Date.now();
    this._state.spinIdx  = 0;
    this._stopThink();
    this._thinkTimer = setInterval(() => {
      this._state.spinIdx++;
      this.redraw();
    }, 100);
  }

  stopThinking(): void {
    this._stopThink();
    this._state.owlState = "idle";
    this.input.setLocked(false);
    this.redraw();
  }

  showToolCall(name: string): void {
    this._state.owlState = "thinking";
    const [tool, ...rest] = name.split(" ");
    this._state.toolCalls.push({ name: tool, args: rest.join(" "), status: "running" });
    if (this._state.toolCalls.length > 12) this._state.toolCalls.shift();
    this.redraw();
  }

  completeToolCall(): void {
    const last = this._state.toolCalls.findLast?.((t) => t.status === "running")
      ?? this._state.toolCalls.filter(t => t.status === "running").at(-1);
    if (last) { last.status = "done"; last.ms = Date.now() - this._thinkStart; }
    this.redraw();
  }

  showResponse(response: GatewayResponse): void {
    this._stopThink();
    this._state.owlState = "done";
    this._state.turn++;
    this._pushLine(chalk.rgb(205,214,244).bold("  " + response.owlEmoji + " " + response.owlName) + LBL(":"));
    const { rightW } = computeLayout();
    for (const l of wrapText(response.content, rightW - 4)) {
      this._pushLine("  " + W(l));
    }
    this._pushLine("");
    this.input.setLocked(false);
    this.redraw();
  }

  printResponse(emoji: string, name: string, content: string): void {
    this.showResponse({ content, owlName: name, owlEmoji: emoji, toolsUsed: [] });
  }

  printError(msg: string): void {
    this._stopThink();
    this._state.owlState = "error";
    this._pushLine("  " + R("✕ ") + R(msg));
    this._pushLine("");
    this.input.setLocked(false);
    this.redraw();
  }

  printInfo(msg: string): void {
    this._pushLine("  " + LBL(msg));
    this.redraw();
  }

  printLines(lines: string[]): void {
    for (const l of lines) this._pushLine(l === "" ? "" : "  " + l);
    this.redraw();
  }

  // ─── Streaming ────────────────────────────────────────────────

  createStreamHandler(): { handler: (event: StreamEvent) => Promise<void>; didStream: () => boolean } {
    let streamed = false;
    const handler = async (ev: StreamEvent) => {
      switch (ev.type) {
        case "text_delta": {
          const chunk = ev.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;
          this._stopThink();
          this._state.owlState = "done";
          if (!this._state.streaming) {
            this._state.streaming       = true;
            this._state.streamBuf       = "";
            this._state.streamHeaderIdx = this._state.lines.length;
            this._state.turn++;
            this._pushLine(chalk.rgb(205,214,244).bold("  " + this._state.owlEmoji + " " + this._state.owlName) + LBL(":"));
          }
          this._state.streamBuf += chunk;
          this._state.lines.splice(this._state.streamHeaderIdx + 1);
          const { rightW } = computeLayout();
          for (const l of wrapText(this._state.streamBuf, rightW - 4)) {
            this._state.lines.push("  " + W(l));
          }
          this.redraw();
          streamed = true;
          break;
        }
        case "tool_start": this.stopThinking(); this.showToolCall(ev.toolName); break;
        case "tool_end":   this.completeToolCall(); break;
        case "done":
          this._stopThink();
          this._state.owlState    = "idle";
          this._state.streaming   = false;
          this._state.streamBuf   = "";
          this._state.streamHeaderIdx = -1;
          this._pushLine("");
          this.input.setLocked(false);
          this.redraw();
          break;
      }
    };
    return { handler, didStream: () => streamed };
  }

  // ─── Redraw ───────────────────────────────────────────────────

  redraw(): void {
    if (this._closed)       return;
    if (this._renderQueued) return;
    this._renderQueued = true;
    setImmediate(() => {
      if (this._closed)    return;
      this._renderQueued = false;
      if (this._rendering) return;
      this._rendering = true;
      try { process.stdout.write(this._buildFrame()); }
      finally { this._rendering = false; }
    });
  }

  // ─── Frame builder ────────────────────────────────────────────

  private _buildFrame(): string {
    const layout = computeLayout();
    const { cols, rows, leftW, rightW } = layout;
    const FRAME_H = CONTENT_BG(" ");
    const FRAME_V = CONTENT_BG(" ");

    let out = ansi.clear;

    // Pixel-shadow frame
    out += ansi.pos(1)   + FRAME_H.repeat(cols);
    for (let i = 2; i < rows; i++) {
      out += ansi.pos(i, 1)    + FRAME_V;
      out += ansi.pos(i, cols) + FRAME_V;
    }
    out += ansi.pos(rows) + FRAME_H.repeat(cols);

    // Top bar (rows 2–3)
    const topBarStr = renderTopBar({
      owlEmoji: this._state.owlEmoji, owlName: this._state.owlName,
      model: this._state.model, turn: this._state.turn,
      tokens: this._state.tokens, cost: this._state.cost,
    }, cols);
    out += ansi.pos(2) + topBarStr;

    // Body (rows 4 to rows-5)
    const bodyRows = rows - 7;
    const leftLines  = renderLeftPanel(this._leftProps(),  leftW,  bodyRows);
    const rightLines = renderRightPanel(this._rightProps(), rightW, bodyRows);

    for (let i = 0; i < bodyRows; i++) {
      const row  = 4 + i;
      const lLn  = leftLines[i]  ?? "";
      const rLn  = rightLines[i] ?? "";
      const lPad = " ".repeat(Math.max(0, leftW  - visLen(lLn)));
      const rPad = " ".repeat(Math.max(0, rightW - visLen(rLn)));
      out += ansi.pos(row, 2)        + lLn + lPad;
      out += ansi.pos(row, leftW + 2) + PANEL_V;
      out += ansi.pos(row, leftW + 5) + rLn + rPad;
    }

    // Input panel (rows rows-4 to rows-2)
    const inputStr = renderInputBox({
      buf: this.input.buf, cursor: this.input.cursor,
      locked: this.input.locked, masked: this.input.masked,
      spinIdx: this._state.spinIdx,
    }, rightW);
    const inputLines = inputStr.split("\n");
    out += ansi.pos(rows - 4, leftW + 2) + inputLines[0];
    out += ansi.pos(rows - 3, leftW + 2) + inputLines[1];
    out += ansi.pos(rows - 2, leftW + 2) + inputLines[2];

    // Command popup
    if (this.input.cmdPopupActive) {
      const popupLines = renderCmdPopup({ matches: this.input.cmdMatches, selectedIdx: this.input.cmdIdx }, rightW);
      const startRow   = rows - 4 - popupLines.length;
      for (let i = 0; i < popupLines.length; i++) {
        out += ansi.pos(startRow + i, leftW + 3) + popupLines[i];
      }
    }

    // Shortcuts bar (row rows-1)
    out += ansi.pos(rows - 1, 3) + renderShortcutsBar(DEFAULT_SHORTCUTS, cols);

    return out;
  }

  // ─── Props builders ───────────────────────────────────────────

  private _leftProps(): LeftPanelProps {
    const s = this._state;
    return {
      mode: s.mode, owlState: s.owlState, spinIdx: s.spinIdx,
      dna: s.dna, toolCalls: s.toolCalls,
      instincts: s.instincts, memFacts: s.memFacts, skillsHit: s.skillsHit,
      owlEmoji: s.owlEmoji, owlName: s.owlName, generation: s.generation,
      challenge: s.challenge, provider: s.provider, model: s.model, skills: s.skills,
    };
  }

  private _rightProps(): RightPanelProps {
    const s = this._state;
    return { mode: s.mode, lines: s.lines, scrollOff: s.scrollOff, recentSessions: s.recentSessions };
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private _convRows(): number { return computeLayout().rows - 4; }

  private _pushLine(line: string): void {
    this._state.lines.push(line);
    if (this._state.lines.length > 5000) this._state.lines.shift();
    if (this._state.scrollOff > 0) this._state.scrollOff++;
  }

  private _stopThink(): void {
    if (this._thinkTimer) { clearInterval(this._thinkTimer); this._thinkTimer = null; }
  }

  private _keyHandler = (chunk: unknown): void => {
    const key = typeof chunk === "string" ? chunk : (chunk as Buffer).toString("utf8");
    this.input.feed(key);
  };

  private _resizeHandler = (): void => {
    if (this._resizeTimer) clearTimeout(this._resizeTimer);
    this._resizeTimer = setTimeout(() => { this._resizeTimer = null; this.redraw(); }, 100);
  };
}
```

- [ ] **Step 2: Type-check**

```bash
npx tsc --noEmit
```
Expected: no errors in `src/cli/renderer.ts` or any of the new component files

- [ ] **Step 3: Commit**

```bash
git add src/cli/renderer.ts
git commit -m "feat(cli): add TerminalRenderer compositor"
```

---

## Task 10: Slim CLIAdapter + update OnboardingFlow

**Files:**
- Modify: `src/gateway/adapters/cli.ts`
- Modify: `src/cli/onboarding-flow.ts`

- [ ] **Step 1: Rewrite cli.ts**

Replace the entire file with:

```typescript
// src/gateway/adapters/cli.ts
/**
 * StackOwl — CLI Channel Adapter
 *
 * Pure transport layer. All rendering lives in TerminalRenderer.
 * Responsibilities:
 *   - Normalize user input → GatewayMessage
 *   - Pass GatewayResponse → renderer
 *   - Implement ChannelAdapter interface
 */

import { resolve } from "node:path";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import { log } from "../../logger.js";
import { TerminalRenderer } from "../../cli/renderer.js";
import { CommandRegistry } from "../../cli/commands.js";
import { OnboardingFlow } from "../../cli/onboarding-flow.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";

export interface CLIAdapterConfig { userId?: string; }

export class CLIAdapter implements ChannelAdapter {
  readonly id   = "cli";
  readonly name = "CLI";

  private userId:    string;
  private sessionId: string;
  private renderer:  TerminalRenderer;
  private commands:  CommandRegistry;

  private queue:      string[] = [];
  private processing = false;
  private _shuttingDown = false;
  private _onboarding: OnboardingFlow | null = null;

  constructor(private gateway: OwlGateway, config: CLIAdapterConfig = {}) {
    this.userId    = config.userId ?? "local";
    this.sessionId = makeSessionId(this.id, this.userId);
    this.renderer  = new TerminalRenderer();
    this.commands  = new CommandRegistry();
    this.renderer.setCommandList(this.commands.listNames());
  }

  // ─── ChannelAdapter ───────────────────────────────────────────

  async sendToUser(_userId: string, response: GatewayResponse): Promise<void> {
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    this.renderer.printResponse(response.owlEmoji, response.owlName, response.content);
  }

  async deliverFile(_userId: string, filePath: string, caption?: string): Promise<void> {
    this.renderer.printInfo(`File ready: ${filePath}${caption ? " — " + caption : ""}`);
  }

  async start(): Promise<void> {
    const owl    = this.gateway.getOwl();
    const config = this.gateway.getConfig();
    const traits = owl.dna.evolvedTraits;
    const challengeNum = typeof traits.challengeLevel === "number"
      ? traits.challengeLevel
      : parseInt(String(traits.challengeLevel), 10) || 5;

    this.renderer.setOwl(owl.persona.emoji, owl.persona.name, config.defaultProvider, config.defaultModel);
    this.renderer.updateDNA({ challenge: challengeNum, verbosity: (traits as any).verbosity ?? 5, mood: 7 });
    this.renderer.setRecentSessions([]);

    this._wireRenderer();
    await this._showHome(owl, config, challengeNum);

    await new Promise<void>(res => {
      this.renderer.once("quit", res);
      process.once("_stackowlStop", res as () => void);
    });
  }

  stop(): void { this.renderer.close(); }

  // ─── Home → Session transition ────────────────────────────────

  private _showHome(
    owl:          ReturnType<OwlGateway["getOwl"]>,
    config:       ReturnType<OwlGateway["getConfig"]>,
    challengeNum: number,
  ): Promise<void> {
    return new Promise(resolve => {
      // Populate left-panel home state
      (this.renderer as any)._state.generation = owl.dna.generation;
      (this.renderer as any)._state.challenge  = challengeNum;
      (this.renderer as any)._state.skills     =
        this.gateway.getSkillsLoader?.()?.getRegistry().listEnabled().length ?? 0;

      this.renderer.enter();

      // First "line" event transitions to session mode
      const onActivate = (input: string) => {
        this.renderer.setMode("session");
        if (input) {
          this.queue.push(input);
          this._drain();
        }
        resolve();
      };
      this.renderer.input.once("line", onActivate);
    });
  }

  // ─── Wire renderer events ─────────────────────────────────────

  private _wireRenderer(): void {
    this.renderer.input.on("line", (input: string) => {
      if (this.renderer["_state"].mode !== "session") return; // handled by _showHome
      this.queue.push(input);
      this._drain();
    });
    this.renderer.on("quit", async () => { await this._gracefulShutdown(); });
    this.renderer.input.on("quit", async () => { await this._gracefulShutdown(); });
  }

  // ─── Queue ────────────────────────────────────────────────────

  private _drain(): void {
    if (this.processing || this.queue.length === 0) return;
    const input = this.queue.shift()!;
    this.processing = true;
    this._processLine(input).finally(() => { this.processing = false; this._drain(); });
  }

  private async _processLine(input: string): Promise<void> {
    if (this._onboarding) {
      const done = await this._onboarding.handleInput(input, this.renderer);
      if (done) this._onboarding = null;
      return;
    }

    const consumed = await this.commands.handle(input, this.renderer, this.gateway);
    if (consumed) return;

    try {
      log.cli.incoming(this.userId, input);
      this.gateway.getCognitiveLoop()?.notifyUserActivity();
      this.renderer.showThinking();

      const { handler, didStream } = this.renderer.createStreamHandler();

      const response = await this.gateway.handle(
        { id: makeMessageId(), channelId: this.id, userId: this.userId, sessionId: this.sessionId, text: input },
        {
          onProgress: async (msg: string) => { log.engine.debug(`[progress] ${msg}`); },
          askInstall: async (deps: string[]) => {
            this.renderer.stopThinking();
            this.renderer.printInfo(`📦 Install ${deps.join(" ")}? [y/n]`);
            return this.renderer.input.promptYesNo();
          },
          onStreamEvent: handler,
        },
      );

      log.cli.outgoing(this.userId, response.content);
      if (!didStream()) {
        this.renderer.stopThinking();
        this.renderer.showResponse(response);
      }
      if (response.usage) {
        this.renderer.updateStats(
          (response.usage.promptTokens ?? 0) + (response.usage.completionTokens ?? 0), 0,
        );
      }
    } catch (err) {
      this.renderer.stopThinking();
      const msg = err instanceof Error ? err.message : String(err);
      log.cli.error(`Error: ${msg}`);
      this.renderer.printError(msg);
    }
  }

  private async _gracefulShutdown(): Promise<void> {
    if (this._shuttingDown) return;
    this._shuttingDown = true;
    this.renderer.close();
    process.exit(0);
  }
}
```

- [ ] **Step 2: Update onboarding-flow.ts type reference**

In `src/cli/onboarding-flow.ts`, change the import and type of `ui` parameter:

```typescript
// Change line 20:
// FROM:
import type { TerminalUI } from "./ui.js";
// TO:
import type { TerminalRenderer } from "./renderer.js";
```

Then update every method signature that accepts `ui: TerminalUI` to `ui: TerminalRenderer`. Search for all occurrences:

```bash
grep -n "TerminalUI\|ui: Terminal" src/cli/onboarding-flow.ts
```

Replace each `TerminalUI` type reference with `TerminalRenderer`. The method calls (`ui.printLines`, `ui.printError`, `ui.setMasked`, `ui.setAllowEmptyInput`, `ui.emit`) are identical on `TerminalRenderer` — no call-site changes needed.

- [ ] **Step 3: Update commands.ts type reference**

In `src/cli/commands.ts`, change the import and type:

```typescript
// Change line 9 (import):
// FROM:
import type { TerminalUI } from "./ui.js";
// TO:
import type { TerminalRenderer } from "./renderer.js";
```

Replace `TerminalUI` with `TerminalRenderer` in the `CommandFn` type definition:

```typescript
// Change:
type CommandFn = (args: string, ui: TerminalUI, gateway: OwlGateway) => Promise<boolean>;
// To:
type CommandFn = (args: string, ui: TerminalRenderer, gateway: OwlGateway) => Promise<boolean>;
```

Also update the `handle` and `paletteHint` method signatures accordingly.

- [ ] **Step 4: Type-check**

```bash
npx tsc --noEmit
```
Expected: no errors. If `TerminalUI` is referenced elsewhere, search and replace:

```bash
grep -rn "from.*cli/ui" src/ --include="*.ts"
```

Fix any remaining imports to point to `./renderer.js` or `../cli/renderer.js`.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/adapters/cli.ts src/cli/onboarding-flow.ts src/cli/commands.ts
git commit -m "feat(cli): slim CLIAdapter to pure transport, wire TerminalRenderer"
```

---

## Task 11: Delete old files + full test run

**Files:**
- Delete: `src/cli/ui.ts`
- Delete: `src/cli/home.ts`

- [ ] **Step 1: Verify nothing else imports ui.ts or home.ts**

```bash
grep -rn "from.*cli/ui\|from.*cli/home" src/ --include="*.ts"
```
Expected: no output. If any imports remain, fix them before deleting.

- [ ] **Step 2: Delete the old files**

```bash
rm src/cli/ui.ts src/cli/home.ts
```

- [ ] **Step 3: Type-check**

```bash
npx tsc --noEmit
```
Expected: no errors

- [ ] **Step 4: Run all CLI tests**

```bash
npx vitest run __tests__/cli/
```
Expected: all tests PASS

- [ ] **Step 5: Run full test suite**

```bash
npm test
```
Expected: all tests PASS (or same pass/fail count as before this work — no regressions)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(cli): delete legacy ui.ts and home.ts — replaced by component-based renderer"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task that covers it |
|-----------------|-------------------|
| CLIAdapter = pure transport | Task 10 |
| TerminalRenderer owns components | Task 9 |
| InputHandler separate from renderer | Task 8 |
| shared/ansi.ts, palette.ts, text.ts | Task 1 |
| layout.ts geometry | Task 2 |
| TopBar component | Task 3 |
| InputBox component | Task 4 |
| ShortcutsBar component | Task 5 |
| CmdPopup component | Task 5 |
| LeftPanel (home + session) | Task 6 |
| RightPanel (home + session) | Task 7 |
| home → session mode switch | Task 9, 10 |
| onboarding-flow.ts updated | Task 10 |
| ui.ts, home.ts deleted | Task 11 |
| sendToUser/broadcast via renderer | Task 10 |
| askInstall via InputHandler.promptYesNo | Task 8, 10 |
| streaming via createStreamHandler | Task 9 |
