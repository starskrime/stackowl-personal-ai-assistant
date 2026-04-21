# Terminal UI Neon Accent Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat monochromatic Dark Glass palette with the Neon Accent design — amber primary, blue secondary, per-trait DNA colors, visible amber input border, and a populated home screen right panel.

**Architecture:** Pure visual swap — no logic, key-handling, scroll, or streaming code changes. All changes are in color constants and string-building methods. Row layout math is preserved; the frame is made transparent (same color as content BG) rather than removed, avoiding row offset recalculation.

**Tech Stack:** TypeScript, chalk (already imported), Node.js terminal ANSI rendering.

**Spec:** `docs/superpowers/specs/2026-04-21-terminal-ui-redesign.md`

---

## File Map

| File | What changes |
|------|-------------|
| `src/cli/ui.ts` | Color constants block, `_buildFrame`, `_buildTopBar`, `_buildTopBarContent`, `_buildLeft`, `_dnaBar` (add trait param + callers), `_buildInputPanel`, `_buildInputLine`, `_buildShortcuts`, `PANEL_V` constant |
| `src/cli/home.ts` | Color constants block, `_buildTopBar`, `_buildLeft`, `_buildRight` (add recent sessions), `_buildShortcuts`, `PANEL_V` constant |

No other files change. No new files created.

> **Note on testing:** `TerminalUI` and `HomeScreen` have no unit tests — they write raw ANSI to stdout and have no return values to assert. Each task ends with a build check (`npm run build`) instead of a test run. Full visual verification is done once at the end in Task 11.

---

## Task 1: Replace color constants in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts:30-55`

- [ ] **Step 1: Replace the color shortcuts block and background constants**

  Open `src/cli/ui.ts`. Replace lines 30–55 (the `// ─── Color shortcuts` block through `const DIV = "━";`) with:

  ```typescript
  // ─── Color palette — Neon Accent ─────────────────────────────────

  const AMBER  = chalk.rgb(250, 179, 135);   // primary accent
  const BLUE   = chalk.rgb(137, 180, 250);   // secondary accent
  const GREEN  = chalk.rgb(166, 227, 161);   // success / high mood
  const PURPLE = chalk.rgb(203, 166, 247);   // metadata (turns, triggered)
  const W      = chalk.rgb(205, 214, 244);   // primary text
  const LBL    = chalk.rgb(69, 71, 90);      // labels / dim text
  const MUT    = chalk.rgb(46, 46, 69);      // muted (borders, timings)
  const R      = chalk.rgb(243, 139, 168);   // error

  // Backgrounds
  const PANEL_BG   = chalk.bgRgb(12, 12, 24);   // top bar / input zone bg
  const CONTENT_BG = chalk.bgRgb(8, 8, 16);     // body panels bg

  // ─── Frame + panel constants ──────────────────────────────────────

  const FRAME_V = CONTENT_BG(" ");          // transparent frame cell (invisible)
  const FRAME_H = CONTENT_BG(" ");          // transparent frame cell (invisible)
  const PANEL_V = MUT(" │ ");               // panel separator — explicit muted color

  const DIV = "━"; // heavy horizontal divider (U+2501)
  ```

- [ ] **Step 2: Verify build is clean**

  ```bash
  cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants && npm run build 2>&1 | tail -20
  ```

  Expected: no errors. If you see `Cannot find name 'Y'`, `'D'`, `'C'`, `'G'`, `'Wb'`, `'Wbr'`, `'TOP_BG'`, `'SHORT_BG'`, or `'FRAME_BG'` — those old names are still used in the file. Do NOT fix them yet; they will be resolved in subsequent tasks.

- [ ] **Step 3: Commit**

  ```bash
  cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants
  git add src/cli/ui.ts
  git commit -m "refactor(ui): replace color constants with Neon Accent palette"
  ```

---

## Task 2: Update top bar in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts` — `_buildTopBar` and `_buildTopBarContent` methods (~lines 680–703)

- [ ] **Step 1: Replace `_buildTopBar`**

  Find and replace the entire `_buildTopBar` method:

  ```typescript
  private _buildTopBar(): string {
    const c = this.cols;
    const bar = this._buildTopBarContent();
    return (
      ansi.pos(2) +
      PANEL_BG("  " + bar + "  ") +
      ansi.pos(3) +
      PANEL_BG(AMBER(DIV.repeat(c)))
    );
  }
  ```

- [ ] **Step 2: Replace `_buildTopBarContent`**

  Find and replace the entire `_buildTopBarContent` method:

  ```typescript
  private _buildTopBarContent(): string {
    const badge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(
      " " + this.owlEmoji + " " + this.owlName + " "
    );
    const model = this.owlModel
      ? " " + MUT("[") + BLUE(this.owlModel.replace("claude-", "").slice(0, 18)) + MUT("]")
      : "";
    const turn  = this._turn > 0
      ? " " + MUT("·") + " " + PURPLE("turn " + this._turn)
      : "";
    const toks  = this._tokens > 0
      ? " " + MUT("·") + " " + LBL((this._tokens / 1000).toFixed(1) + "k")
      : "";
    const cost  = this._cost > 0
      ? " " + MUT("·") + " " + GREEN("$" + this._cost.toFixed(3))
      : "";
    return badge + model + turn + toks + cost;
  }
  ```

- [ ] **Step 3: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: no new errors introduced by this task.

- [ ] **Step 4: Commit**

  ```bash
  git add src/cli/ui.ts
  git commit -m "refactor(ui): neon accent top bar — amber badge, blue model chip, green cost"
  ```

---

## Task 3: Update left panel — section headers and stat rows in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts` — `_buildLeft` method (~lines 811–887)

- [ ] **Step 1: Replace the `_buildLeft` method**

  Find and replace the entire `_buildLeft` method with:

  ```typescript
  private _buildLeft(w: number, rows: number): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    // ── Section header helper ──────────────────────────────────────
    const secHdr = (label: string) => {
      const line = MUT("─".repeat(Math.max(0, w - label.length - 5)));
      return "  " + AMBER.bold(label) + " " + line;
    };

    blank();
    add(secHdr("OWL MIND"));
    blank();
    add("  " + AMBER(this._currentFace()));
    if (this._owlState === "thinking") {
      add("  " + BLUE(SPINNER[this._spinIdx % SPINNER.length] + " thinking..."));
    }
    blank();
    add(
      "  " + PURPLE("◆") + " " + LBL("Instincts") + "   " +
      (this._instincts > 0 ? AMBER.bold(this._instincts + " triggered") : MUT("—")),
    );
    add(
      "  " + PURPLE("◆") + " " + LBL("Memory   ") + "   " +
      (this._memFacts > 0 ? AMBER.bold(this._memFacts + " facts") : MUT("—")),
    );
    add(
      "  " + PURPLE("◆") + " " + LBL("Skills   ") + "   " +
      (this._skillsHit > 0 ? GREEN.bold(this._skillsHit + " invoked") : MUT("—")),
    );
    blank();

    if (this._toolCalls.length > 0) {
      add(secHdr("REASONING"));
      const visible = this._toolCalls.slice(-8);
      visible.forEach((tc, i) => {
        const isLast = i === visible.length - 1;
        const branch = isLast ? MUT("  └ ") : MUT("  ├ ");
        const spinner = SPINNER[this._spinIdx % SPINNER.length];
        const icon =
          tc.status === "running"
            ? BLUE(spinner)
            : tc.status === "done"
              ? GREEN("✓")
              : R("✕");
        const name = tc.status === "running"
          ? BLUE(trunc(tc.name, w - 18))
          : W(trunc(tc.name, w - 18));
        const ms = tc.ms ? MUT(" " + tc.ms + "ms") : "";
        add(branch + icon + " " + name + ms);
        if (tc.summary) {
          const indent = isLast ? "        " : "  │     ";
          add(indent + LBL(trunc(tc.summary, w - 12)));
        }
      });
      blank();
    }

    const dnaStartIdx = lines.length;
    const dnaRows = rows - dnaStartIdx - 2;

    if (dnaRows > 4) {
      blank();
      add(secHdr("DNA"));
      blank();
      add("  " + this._dnaBar("challenge", this._dna.challenge, "challenge"));
      add("  " + this._dnaBar("verbosity", this._dna.verbosity, "verbosity"));
      add("  " + this._dnaBar("mood     ", this._dna.mood, "mood"));
    }

    while (lines.length < rows - 1) blank();
    add("  " + MUT("─".repeat(Math.max(0, w - 4))) + " " + MUT("FIREWALL"));

    return lines.slice(0, rows);
  }
  ```

- [ ] **Step 2: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: errors only for `_dnaBar` signature mismatch (will be fixed next task). No other new errors.

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/ui.ts
  git commit -m "refactor(ui): neon accent left panel — section headers, stat rows, tool tree icons"
  ```

---

## Task 4: Update `_dnaBar` in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts` — `_dnaBar` method (~line 989)

- [ ] **Step 1: Replace `_dnaBar`**

  Find and replace the entire `_dnaBar` method:

  ```typescript
  private _dnaBar(
    label: string,
    val: number,
    trait: "challenge" | "verbosity" | "mood",
  ): string {
    const v = Math.max(0, Math.min(10, Math.round(val)));
    const color =
      trait === "challenge" ? AMBER : trait === "verbosity" ? BLUE : GREEN;
    const filled = color("█").repeat(v);
    const empty  = MUT("█").repeat(10 - v);
    return LBL(label) + " " + filled + empty + " " + MUT(String(val));
  }
  ```

- [ ] **Step 2: Verify build is fully clean**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: **zero errors**. If `_dnaBar` callers in Task 3 already use the three-arg form, this resolves the mismatch.

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/ui.ts
  git commit -m "refactor(ui): per-trait colored DNA blocks (amber/blue/green)"
  ```

---

## Task 5: Update input zone in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts` — `_buildInputPanel` (~line 751) and `_buildInputLine` (~line 926)

- [ ] **Step 1: Replace `_buildInputPanel`**

  Find and replace the entire `_buildInputPanel` method:

  ```typescript
  private _buildInputPanel(): string {
    const rW = this.rightW;
    const topRow   = this.rows - 4;
    const inputRow = this.rows - 3;
    const botRow   = this.rows - 2;
    const line = this._buildInputLine(rW);

    // Amber top-border line, panel-bg content row, amber bottom-border line
    const topBorder = PANEL_BG(AMBER("▔".repeat(rW + 2)));
    const content   = PANEL_BG(
      " " + line.t + " ".repeat(Math.max(0, rW - line.v)) + " ",
    );
    const botBorder = PANEL_BG(AMBER("▁".repeat(rW + 2)));

    return (
      ansi.pos(topRow,   this.leftW + 2) + topBorder +
      ansi.pos(inputRow, this.leftW + 2) + content   +
      ansi.pos(botRow,   this.leftW + 2) + botBorder
    );
  }
  ```

- [ ] **Step 2: Replace `_buildInputLine`**

  Find and replace the entire `_buildInputLine` method:

  ```typescript
  private _buildInputLine(_w?: number): { t: string; v: number } {
    if (this._inputLocked) {
      const spin = BLUE(SPINNER[this._spinIdx % SPINNER.length]);
      return {
        t: "  " + spin + LBL("  thinking — press ESC to stop"),
        v: visLen("  thinking — press ESC to stop") + 3,
      };
    }
    const prefix = "  " + AMBER("› ") + W("");
    let before: string, atCur: string, after: string;
    if (this._inputMasked) {
      before = "*".repeat(this._inputCursor);
      atCur  = this._inputBuf[this._inputCursor] ? "*" : " ";
      after  = "*".repeat(
        Math.max(0, this._inputBuf.length - this._inputCursor - 1),
      );
    } else {
      before = this._inputBuf.slice(0, this._inputCursor);
      atCur  = this._inputBuf[this._inputCursor] ?? " ";
      after  = this._inputBuf.slice(this._inputCursor + 1);
    }
    const display = W(before) + chalk.bgYellow.black(atCur) + W(after);
    const t = prefix + display;
    return { t, v: visLen(t) };
  }
  ```

- [ ] **Step 3: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: zero errors.

- [ ] **Step 4: Commit**

  ```bash
  git add src/cli/ui.ts
  git commit -m "refactor(ui): amber-bordered input panel, blue thinking state"
  ```

---

## Task 6: Update shortcuts bar in `src/cli/ui.ts`

**Files:**
- Modify: `src/cli/ui.ts` — `_buildShortcuts` method (~line 954)

- [ ] **Step 1: Replace `_buildShortcuts`**

  Find and replace the entire `_buildShortcuts` method:

  ```typescript
  private _buildShortcuts(): string {
    const c = this.cols;
    const r = this.rows;
    const inner = c - 4;

    const key = (k: string) =>
      chalk.bgRgb(26, 26, 44).rgb(205, 214, 244).bold(` ${k} `);

    const line =
      key("ESC") + LBL("  Stop     ") +
      key("^P")  + LBL("  Parliament     ") +
      key("^L")  + LBL("  Clear     ") +
      key("^C")  + LBL("  Quit");

    return ansi.pos(r - 1, 3) + PANEL_BG(padR(line, inner));
  }
  ```

- [ ] **Step 2: Verify build is fully clean (all old names gone)**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: **zero errors, zero warnings**. If you see `Cannot find name 'Y'`, `'D'`, `'Wb'`, `'Wbr'`, `'G'`, `'C'`, `'TOP_BG'`, `'SHORT_BG'`, or `'FRAME_BG'` — search the file for those names and replace with their new equivalents:

  | Old | New |
  |-----|-----|
  | `Y(...)` | `AMBER(...)` |
  | `D(...)` | `LBL(...)` |
  | `W(...)` | `W(...)` *(unchanged)* |
  | `Wb(...)` | `W.bold(...)` or `chalk.rgb(205,214,244).bold(...)` |
  | `Wbr(...)` | `LBL(...)` |
  | `G(...)` | `GREEN(...)` |
  | `C(...)` | `BLUE(...)` |
  | `TOP_BG(...)` | `PANEL_BG(...)` |
  | `SHORT_BG(...)` | `PANEL_BG(...)` |
  | `FRAME_BG(...)` | `CONTENT_BG(...)` |

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/ui.ts
  git commit -m "refactor(ui): key-chip shortcuts bar, clean up old color name references"
  ```

---

## Task 7: Replace color constants in `src/cli/home.ts`

**Files:**
- Modify: `src/cli/home.ts:26-48`

- [ ] **Step 1: Replace the color block**

  Open `src/cli/home.ts`. Replace lines 26–48 (the `// ─── Colors` block through `const DIV = "━";`) with:

  ```typescript
  // ─── Color palette — Neon Accent ─────────────────────────────────

  const AMBER  = chalk.rgb(250, 179, 135);
  const BLUE   = chalk.rgb(137, 180, 250);
  const GREEN  = chalk.rgb(166, 227, 161);
  const W      = chalk.rgb(205, 214, 244);
  const LBL    = chalk.rgb(69, 71, 90);
  const MUT    = chalk.rgb(46, 46, 69);

  const PANEL_BG   = chalk.bgRgb(12, 12, 24);
  const CONTENT_BG = chalk.bgRgb(8, 8, 16);

  // ─── Frame + panel constants ──────────────────────────────────────

  const FRAME_V = CONTENT_BG(" ");
  const FRAME_H = CONTENT_BG(" ");
  const PANEL_V = MUT(" │ ");

  const DIV = "━";
  ```

- [ ] **Step 2: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: errors only for old names (`YB`, `D`, `C`, `Wb`, `Wbr`, `TOP_BG`, `SHORT_BG`, `FRAME_BG`) still used in the rest of `home.ts`. These are resolved in subsequent tasks.

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/home.ts
  git commit -m "refactor(home): replace color constants with Neon Accent palette"
  ```

---

## Task 8: Update top bar in `src/cli/home.ts`

**Files:**
- Modify: `src/cli/home.ts` — `_buildTopBar` method (~lines 275–297)

- [ ] **Step 1: Replace `_buildTopBar`**

  Find and replace the entire `_buildTopBar` method:

  ```typescript
  private _buildTopBar(): string {
    const c = this.cols;
    const inner = c - 2;
    const { owlName, generation, challenge, skills } = this.opts;

    const leftBadge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" ◈ STACKOWL ");
    const rightBadge = chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(
      " " + this.opts.owlEmoji + " " + owlName + " "
    );
    const meta =
      " " + MUT("[") + BLUE(this.opts.model.replace("claude-", "").slice(0, 14)) + MUT("]") +
      " " + MUT("·") + " " + LBL("gen" + generation) +
      " " + MUT("·") + " " + AMBER("⚡" + challenge) +
      " " + MUT("·") + " " + GREEN("📦" + skills + " skills");

    const leftLen  = visLen(leftBadge);
    const rightLen = visLen(rightBadge + meta);
    const gap = Math.max(2, inner - leftLen - rightLen);

    const row2 = leftBadge + " ".repeat(gap) + rightBadge + meta;
    let out = "";
    out += H.pos(2, 2) + PANEL_BG(padR(row2, inner));
    out += H.pos(3, 2) + PANEL_BG(AMBER(DIV.repeat(inner)));
    return out;
  }
  ```

- [ ] **Step 2: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/home.ts
  git commit -m "refactor(home): neon accent top bar — dual amber badges, meta chips"
  ```

---

## Task 9: Update left panel in `src/cli/home.ts`

**Files:**
- Modify: `src/cli/home.ts` — `_buildLeft` method (~lines 333–375)

- [ ] **Step 1: Replace `_buildLeft`**

  Find and replace the entire `_buildLeft` method:

  ```typescript
  private _buildLeft(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add   = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    const secHdr = (label: string) => {
      const line = MUT("─".repeat(Math.max(0, w - label.length - 5)));
      return "  " + AMBER.bold(label) + " " + line;
    };

    const { owlEmoji, owlName, generation, challenge, provider, model, skills } =
      this.opts;

    blank();
    add("  " + chalk.bgRgb(250, 179, 135).rgb(8, 8, 16).bold(" " + owlEmoji + " " + owlName + " "));
    blank();
    add(secHdr("IDENTITY"));
    add("  " + LBL("Generation") + "  " + W(String(generation)));
    add("  " + LBL("Challenge ") + "  " + AMBER("⚡" + String(challenge)));
    blank();
    add(secHdr("BACKEND"));
    add("  " + LBL("Provider") + "   " + BLUE(provider));
    add("  " + LBL("Model   ") + "   " + W(model.replace("claude-", "").slice(0, 14)));
    add("  " + LBL("Skills  ") + "   " + GREEN(String(skills) + " loaded"));

    while (lines.length < rows) blank();
    return lines.slice(0, rows);
  }
  ```

- [ ] **Step 2: Verify build**

  ```bash
  npm run build 2>&1 | tail -20
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/cli/home.ts
  git commit -m "refactor(home): neon accent left panel — section headers, identity/backend rows"
  ```

---

## Task 10: Implement right panel (recent sessions) in `src/cli/home.ts`

**Files:**
- Modify: `src/cli/home.ts` — `_buildRight` method (~lines 379–390), `_buildShortcuts` (~line 394), `_renderInputBox` (~line 414)

- [ ] **Step 1: Replace `_buildRight`**

  Find and replace the entire `_buildRight` method:

  ```typescript
  private _buildRight(
    w: number,
    rows: number,
  ): Array<{ t: string; v: number }> {
    const lines: Array<{ t: string; v: number }> = [];
    const add   = (t: string) => lines.push({ t, v: visLen(t) });
    const blank = () => add("");

    // Center row for the input prompt
    const centerRow = Math.floor(rows / 2) - 1;

    for (let i = 0; i < centerRow; i++) blank();

    // Centered prompt label
    const labelText = "What do you want to work on?";
    const labelPad  = Math.max(0, Math.floor((w - labelText.length) / 2));
    add(" ".repeat(labelPad) + LBL(labelText));
    blank(); // input box rendered separately by _renderInputBox

    // Recent sessions — show up to 3
    const sessions = this.opts.recentSessions.slice(0, 3);
    if (sessions.length > 0) {
      blank();
      add("  " + MUT("─".repeat(Math.max(0, w - 4))));
      add("  " + LBL("recent sessions"));
      blank();
      for (const s of sessions) {
        const title    = trunc(s.title, w - 24);
        const turns    = MUT(String(s.turns) + "t");
        const ago      = MUT(s.ago);
        const spacer   = " ".repeat(
          Math.max(1, w - 2 - visLen(title) - visLen(String(s.turns) + "t") - visLen(s.ago) - 4),
        );
        add("  " + W(title) + spacer + turns + "  " + ago);
      }
    }

    while (lines.length < rows) blank();
    return lines.slice(0, rows);
  }
  ```

  Also add `trunc` helper at the top of `home.ts` (after the `padR` function, around line 68):

  ```typescript
  function trunc(s: string, max: number): string {
    const plain = stripAnsi(s);
    return plain.length > max ? plain.slice(0, max - 1) + "…" : s;
  }
  ```

- [ ] **Step 2: Replace `_buildShortcuts` in `home.ts`**

  Find and replace the entire `_buildShortcuts` method:

  ```typescript
  private _buildShortcuts(): string {
    const c = this.cols;
    const r = this.rows;
    const inner = c - 4;

    const key = (k: string) =>
      chalk.bgRgb(26, 26, 44).rgb(205, 214, 244).bold(` ${k} `);

    const line =
      key("ESC") + LBL("  Stop     ") +
      key("^P")  + LBL("  Parliament     ") +
      key("^L")  + LBL("  Clear     ") +
      key("^C")  + LBL("  Quit");

    return H.pos(r - 1, 3) + PANEL_BG(padR(line, inner));
  }
  ```

- [ ] **Step 3: Update `_renderInputBox` to use new colors**

  Find and replace the entire `_renderInputBox` method:

  ```typescript
  private _renderInputBox(): void {
    const r = this.rows;
    const bodyRows    = r - 7;
    const inputCenterRow = Math.floor(bodyRows / 2);

    const lW = this.leftW;
    const rW = this.rightW;

    const cursor = chalk.bgYellow.black(" ");
    let contentLine: string;

    if (this._buf.length > 0) {
      contentLine = AMBER("  › ") + W(this._buf) + cursor;
    } else {
      contentLine = AMBER("  › ") + LBL("Ask anything or type / for commands") + W(" ") + cursor;
    }

    const contentLen = visLen(contentLine);
    const rowPad = " ".repeat(Math.max(0, rW - contentLen));
    const row    = 3 + inputCenterRow;

    // Amber top/bottom border, panel-bg content
    process.stdout.write(
      H.pos(row - 1, lW + 3) + PANEL_BG(AMBER("▔".repeat(rW))) +
      H.pos(row,     lW + 3) + PANEL_BG(contentLine + rowPad)   +
      H.pos(row + 1, lW + 3) + PANEL_BG(AMBER("▁".repeat(rW))),
    );
  }
  ```

- [ ] **Step 4: Verify build is fully clean**

  ```bash
  npm run build 2>&1 | tail -20
  ```

  Expected: **zero errors**. If you see old name references (`YB`, `D`, `C`, `Wb`, `Wbr`, `TOP_BG`, `SHORT_BG`, `FRAME_BG`) still in `home.ts`, replace per this table:

  | Old | New |
  |-----|-----|
  | `YB(...)` | `AMBER.bold(...)` |
  | `D(...)` | `LBL(...)` |
  | `C(...)` | `BLUE(...)` |
  | `Wb(...)` | `chalk.rgb(205,214,244).bold(...)` |
  | `Wbr(...)` | `LBL(...)` |
  | `TOP_BG(...)` | `PANEL_BG(...)` |
  | `SHORT_BG(...)` | `PANEL_BG(...)` |
  | `FRAME_BG(...)` | `CONTENT_BG(...)` |

- [ ] **Step 5: Commit**

  ```bash
  git add src/cli/home.ts
  git commit -m "refactor(home): recent sessions right panel, amber input box, key-chip shortcuts"
  ```

---

## Task 11: Full visual verification + final cleanup

**Files:**
- No code changes unless visual issues are spotted.

- [ ] **Step 1: Run the app and check the home screen**

  ```bash
  npm run build && node dist/cli/index.js
  ```

  Verify:
  - Top bar: amber `◈ STACKOWL` and `🦉 OwlName` badges, blue model chip, amber `━` divider
  - Left panel: `IDENTITY ───` and `BACKEND ───` amber section headers, DNA not yet shown (home screen has no DNA)
  - Right panel: centered prompt + `recent sessions` list (or empty if no sessions exist yet)
  - Input box: amber `▔`/`▁` top/bottom lines, amber `›` symbol, amber cursor
  - Shortcuts: chip-style `ESC`, `^P`, `^L`, `^C` keys on dark background

- [ ] **Step 2: Type something to open the session screen, check all states**

  Check:
  - Active session: `OWL MIND ───`, `DNA ───` section headers in amber
  - DNA bars: amber blocks for challenge, blue for verbosity, green for mood
  - Tool tree: `✓` in green, `✕` in red, spinner in blue
  - Typing `/`: command popup appears above input, selected command in amber
  - Thinking state (after submitting): owl face in blue, `⠹ thinking — press ESC to stop` in LBL color, input border stays amber

- [ ] **Step 3: Run tests to confirm no regressions**

  ```bash
  npm run test 2>&1 | tail -30
  ```

  Expected: same pass/fail results as before. These tests don't cover UI rendering — they cover engine/pellet/owl logic. All should still pass.

- [ ] **Step 4: Final commit**

  ```bash
  git add -p   # review any remaining unstaged changes
  git commit -m "feat: terminal UI Neon Accent redesign complete"
  ```
