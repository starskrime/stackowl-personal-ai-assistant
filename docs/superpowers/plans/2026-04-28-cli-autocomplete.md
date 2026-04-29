# CLI Autocomplete — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two CLI autocomplete bugs (backspace after no-match drops popup; no subcommand completion) by introducing a `CompletionEngine` class with a `CompletionProvider` interface that `CommandRegistry` implements.

**Architecture:** New pure class `CompletionEngine` owns all matching logic and is injected into `InputHandler`. `InputHandler` drops its dual key-path and derived popup state. `CommandRegistry` declares subcommands per command; `cli.ts` wires `new CompletionEngine(commands)` into the renderer.

**Tech Stack:** TypeScript, Node.js, Vitest

---

## File Map

| File | Action |
|------|--------|
| `src/cli/completion-engine.ts` | **Create** — `CompletionProvider` interface, `CompletionResult`, `CompletionEngine` class |
| `src/cli/commands.ts` | Modify — `CommandDef` gains `subcommands?`; `CommandRegistry` implements `CompletionProvider`; add `skills` entry; rename `listNames` → `topLevelNames` |
| `src/cli/input-handler.ts` | Modify — replace dual key-path + `_cmdNames`/`_cmdMatches`/`_updateMatches` with `CompletionEngine` delegation |
| `src/cli/renderer.ts` | Modify — `setCommandList` → `setCompletionEngine` |
| `src/gateway/adapters/cli.ts` | Modify — wire `new CompletionEngine(this.commands)` |
| `__tests__/cli/completion-engine.test.ts` | **Create** — unit tests for `CompletionEngine` |
| `__tests__/cli/input-handler.test.ts` | Modify — update existing tests + add Bug 1/Bug 2 regression tests |

---

## Task 1: CompletionEngine

**Files:**
- Create: `src/cli/completion-engine.ts`
- Create: `__tests__/cli/completion-engine.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/cli/completion-engine.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeProvider(): CompletionProvider {
  return {
    topLevelNames: () => ["help", "status", "skills", "specialization", "clear"],
    subcommands: (cmd: string) => {
      const map: Record<string, string[]> = {
        skills: ["list", "install"],
        specialization: ["list", "show", "create", "delete", "update"],
      };
      return map[cmd] ?? [];
    },
  };
}

describe("CompletionEngine", () => {
  describe("command mode", () => {
    it("returns all commands when buf is /", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual(["help", "status", "skills", "specialization", "clear"]);
    });

    it("prefix-filters top-level names", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/s");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual(["status", "skills", "specialization"]);
    });

    it("returns empty items when no top-level match", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/xyz");
      expect(result.mode).toBe("command");
      expect(result.items).toEqual([]);
    });

    it("returns empty when buf does not start with /", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("hello");
      expect(result.items).toEqual([]);
      expect(result.mode).toBe("command");
    });

    it("returns empty for empty buf", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("");
      expect(result.items).toEqual([]);
    });

    it("is case-insensitive", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/SK");
      expect(result.items).toEqual(["skills", "specialization"]);
    });
  });

  describe("subcommand mode", () => {
    it("returns all subcommands after command + space", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/skills ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["list", "install"]);
    });

    it("prefix-filters subcommands", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/specialization s");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["show"]);
    });

    it("returns all specialization subcommands", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/specialization ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual(["list", "show", "create", "delete", "update"]);
    });

    it("returns empty for unknown command after space", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/unknown ");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual([]);
    });

    it("returns empty when subcommand partial has no match", () => {
      const engine = new CompletionEngine(makeProvider());
      const result = engine.complete("/skills xyz");
      expect(result.mode).toBe("subcommand");
      expect(result.items).toEqual([]);
    });
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/cli/completion-engine.test.ts
```

Expected: FAIL — `Cannot find module '../../src/cli/completion-engine.js'`

- [ ] **Step 3: Create `src/cli/completion-engine.ts`**

```typescript
export interface CompletionProvider {
  topLevelNames(): string[];
  subcommands(commandName: string): string[];
}

export interface CompletionResult {
  items: string[];
  mode: "command" | "subcommand";
}

export class CompletionEngine {
  constructor(private provider: CompletionProvider) {}

  complete(buf: string): CompletionResult {
    if (!buf.startsWith("/")) return { items: [], mode: "command" };

    const inner = buf.slice(1);
    const spaceIdx = inner.indexOf(" ");

    if (spaceIdx === -1) {
      const filter = inner.toLowerCase();
      const items = filter
        ? this.provider.topLevelNames().filter((n) => n.startsWith(filter))
        : this.provider.topLevelNames();
      return { items, mode: "command" };
    }

    const cmdName = inner.slice(0, spaceIdx).toLowerCase();
    const partial = inner.slice(spaceIdx + 1).toLowerCase();
    const subs = this.provider.subcommands(cmdName);
    const items = partial ? subs.filter((s) => s.startsWith(partial)) : subs;
    return { items, mode: "subcommand" };
  }
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/cli/completion-engine.test.ts
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/completion-engine.ts
git add -f __tests__/cli/completion-engine.test.ts
git commit -m "feat(cli): add CompletionEngine with CompletionProvider interface"
```

---

## Task 2: CommandRegistry implements CompletionProvider

**Files:**
- Modify: `src/cli/commands.ts`

- [ ] **Step 1: Update `CommandDef` and `COMMANDS`**

In `src/cli/commands.ts`, replace the `CommandDef` interface and update `COMMANDS` + `CommandRegistry`:

Change `CommandDef`:
```typescript
interface CommandDef {
  description: string;
  fn: CommandFn;
  subcommands?: string[];
}
```

In the `COMMANDS` object, add `subcommands` to `specialization` and add a `skills` entry (execution falls through via the early-return in `handle()`; the entry exists for completion only):

```typescript
const COMMANDS: Record<string, CommandDef> = {
  help:           { description: "Show command list",          fn: cmdHelp },
  "?":            { description: "Show command list",          fn: cmdHelp },
  status:         { description: "Provider / model / owl info", fn: cmdStatus },
  owls:           { description: "List owl personas",          fn: cmdOwls },
  specialization: {
    description: "Manage specialized owls",
    fn: cmdSpecialization,
    subcommands: ["list", "show", "create", "delete", "update"],
  },
  skills: {
    description: "List or install skills",
    fn: async (_args, _ui, _gateway) => false,   // execution falls through to gateway.handle() via early-return
    subcommands: ["list", "install"],
  },
  clear:          { description: "Clear context",              fn: cmdClear },
  reset:          { description: "Clear context",              fn: cmdClear },
  capabilities:   { description: "List synthesized tools",     fn: cmdCapabilities },
  learning:       { description: "Learning report",            fn: cmdLearning },
  quit:           { description: "Save and exit",              fn: cmdQuit },
  exit:           { description: "Save and exit",              fn: cmdQuit },
  bye:            { description: "Save and exit",              fn: cmdQuit },
  onboarding:     { description: "Re-run setup wizard",        fn: cmdOnboarding },
};
```

Replace `CommandRegistry` class:

```typescript
export class CommandRegistry {
  /** CompletionProvider — top-level command names */
  topLevelNames(): string[] {
    return Object.keys(COMMANDS);
  }

  /** CompletionProvider — subcommands for a given top-level command */
  subcommands(commandName: string): string[] {
    return COMMANDS[commandName]?.subcommands ?? [];
  }

  /** Keep for any callers that haven't migrated yet */
  listNames(): string[] {
    return this.topLevelNames();
  }

  getDescription(name: string): string {
    return COMMANDS[name]?.description ?? "";
  }

  async handle(
    input: string,
    ui: TerminalRenderer,
    gateway: OwlGateway,
  ): Promise<boolean> {
    if (activeWizard) {
      const done = await activeWizard.step(input, ui);
      if (done) {
        activeWizard = null;
        await gateway.reloadSpecializedRegistry();
        ui.setAllowEmptyInput(false);
      }
      return true;
    }

    if (!input.startsWith("/")) return false;

    // Let /skills fall through to gateway.handle() for wizard routing
    if (input.toLowerCase().startsWith("/skills")) return false;

    const space = input.indexOf(" ");
    const name = (
      space === -1 ? input.slice(1) : input.slice(1, space)
    ).toLowerCase();
    const args = space === -1 ? "" : input.slice(space + 1);

    const def = COMMANDS[name];
    if (!def) {
      ui.printLines([
        R(`Unknown command "/${name}".`) + D("  Type /help for the list."),
        "",
      ]);
      return true;
    }

    return def.fn(args, ui, gateway);
  }

  paletteHint(): string {
    return Object.keys(COMMANDS)
      .filter((k) => !["?", "reset", "exit"].includes(k))
      .map((k) => chalk.cyan(`/${k}`))
      .join("  ");
  }
}
```

Also add the import at the top of the file:
```typescript
import type { CompletionProvider } from "./completion-engine.js";
```

And declare the class with the interface:
```typescript
export class CommandRegistry implements CompletionProvider {
```

- [ ] **Step 2: Run type-check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Run existing commands tests**

```bash
npx vitest run __tests__/cli/specialization-commands.test.ts
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat(cli): CommandRegistry implements CompletionProvider, adds skills + subcommands"
```

---

## Task 3: Rewrite InputHandler

**Files:**
- Modify: `src/cli/input-handler.ts`
- Modify: `__tests__/cli/input-handler.test.ts`

- [ ] **Step 1: Add regression tests for Bug 1 and Bug 2 to the test file**

In `__tests__/cli/input-handler.test.ts`, add these new tests inside the `describe("InputHandler")` block. Add them after the existing tests (keep all existing tests):

```typescript
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeEngine(names: string[], subs: Record<string, string[]> = {}): CompletionEngine {
  const provider: CompletionProvider = {
    topLevelNames: () => names,
    subcommands: (cmd) => subs[cmd] ?? [],
  };
  return new CompletionEngine(provider);
}
```

Add at the top of the file (alongside the existing import):
```typescript
import { describe, it, expect, vi } from "vitest";
import { InputHandler } from "../../src/cli/input-handler.js";
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeEngine(names: string[], subs: Record<string, string[]> = {}): CompletionEngine {
  return new CompletionEngine({
    topLevelNames: () => names,
    subcommands: (cmd) => subs[cmd] ?? [],
  });
}
```

Replace the whole file with:

```typescript
import { describe, it, expect, vi } from "vitest";
import { InputHandler } from "../../src/cli/input-handler.js";
import { CompletionEngine } from "../../src/cli/completion-engine.js";
import type { CompletionProvider } from "../../src/cli/completion-engine.js";

function makeEngine(names: string[], subs: Record<string, string[]> = {}): CompletionEngine {
  return new CompletionEngine({
    topLevelNames: () => names,
    subcommands: (cmd) => subs[cmd] ?? [],
  });
}

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
    h.setCompletionEngine(makeEngine(["help", "status"]));
    h.feed("/");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("filters popup matches as user types", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help", "status", "skills"]));
    h.feed("/"); h.feed("s");
    expect(h.cmdMatches).toEqual(["status", "skills"]);
  });

  it("dismisses popup on ESC", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help"]));
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

  // ─── Bug 1 regression: backspace after no-match reopens popup ───

  it("Bug 1: popup reappears after backspace following a no-match filter", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help", "status"]));
    h.feed("/"); h.feed("x"); // /x — no match
    expect(h.cmdPopupActive).toBe(false);
    h.feed("\x7f");           // backspace → buf = "/"
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["help", "status"]);
  });

  it("Bug 1: popup stays closed when buf no longer starts with /", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["help"]));
    h.feed("/"); h.feed("\x7f"); // delete the /
    expect(h.cmdPopupActive).toBe(false);
    expect(h.buf).toBe("");
  });

  // ─── Bug 2 regression: subcommand completion after space ────────

  it("Bug 2: shows subcommands after /skills <space>", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["skills"], { skills: ["list", "install"] }));
    for (const c of "/skills ") h.feed(c);
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["list", "install"]);
  });

  it("Bug 2: filters subcommands by partial", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(
      ["specialization"],
      { specialization: ["list", "show", "create", "delete", "update"] },
    ));
    for (const c of "/specialization s") h.feed(c);
    expect(h.cmdMatches).toEqual(["show"]);
  });

  // ─── Enter applies selected completion ──────────────────────────

  it("Enter applies highlighted command and appends space", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["status", "skills"]));
    h.feed("/"); h.feed("s");                // matches: status, skills; idx=0
    h.feed("\r");                            // select "status"
    expect(h.buf).toBe("/status ");
    expect(h.cmdPopupActive).toBe(false);    // no subcommands for status
  });

  it("Enter on command with subcommands reopens popup with subcommands", () => {
    const h = new InputHandler();
    h.setCompletionEngine(makeEngine(["skills"], { skills: ["list", "install"] }));
    h.feed("/"); h.feed("s");               // matches: skills
    h.feed("\r");                           // select "skills" → buf = "/skills "
    expect(h.buf).toBe("/skills ");
    expect(h.cmdPopupActive).toBe(true);
    expect(h.cmdMatches).toEqual(["list", "install"]);
  });
});
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
npx vitest run __tests__/cli/input-handler.test.ts
```

Expected: several FAILs — `setCompletionEngine is not a function` and the Bug 1/2 regression tests fail.

- [ ] **Step 3: Rewrite `src/cli/input-handler.ts`**

Replace the entire file:

```typescript
import { EventEmitter } from "node:events";
import type { CompletionEngine, CompletionResult } from "./completion-engine.js";

const ESC = "\x1B";

export class InputHandler extends EventEmitter {
  private _buf      = "";
  private _cursor   = 0;
  private _history: string[] = [];
  private _histIdx  = -1;
  private _histTemp = "";
  private _cmdIdx   = 0;
  private _completion: CompletionResult = { items: [], mode: "command" };
  private _engine: CompletionEngine | null = null;
  private _masked     = false;
  private _allowEmpty = false;
  private _locked     = false;

  // ─── State (read by renderer each frame) ─────────────────────

  get buf()            { return this._buf; }
  get cursor()         { return this._cursor; }
  get locked()         { return this._locked; }
  get masked()         { return this._masked; }
  get cmdPopupActive() { return this._completion.items.length > 0 && this._buf.startsWith("/"); }
  get cmdMatches()     { return [...this._completion.items]; }
  get cmdIdx()         { return this._cmdIdx; }

  // ─── Configuration ────────────────────────────────────────────

  setCompletionEngine(engine: CompletionEngine): void {
    this._engine = engine;
    this._refreshCompletion();
  }

  setMasked(on: boolean)       { this._masked = on; }
  setAllowEmpty(on: boolean)   { this._allowEmpty = on; }
  setLocked(on: boolean)       { this._locked = on; }
  setInitialInput(buf: string) { this._buf = buf; this._cursor = buf.length; this._refreshCompletion(); }

  // ─── Key feed ─────────────────────────────────────────────────

  /** Feed a raw stdin data chunk. Emits: "line", "quit", "change", "scroll", "clear". */
  feed(data: string): void {
    if (data === "\x03" || data === "\x04") { this.emit("quit"); return; }

    data = data.replace(/\x1B\[200~/g, "").replace(/\x1B\[201~/g, "");
    if (!data) return;

    this._handleKey(data);
  }

  // ─── y/n prompt ───────────────────────────────────────────────

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

  private _handleKey(data: string): void {
    if (this._locked) return;

    // ─── Submit / select ──────────────────────────────────────
    if (data === "\r" || data === "\n") {
      if (this.cmdPopupActive) {
        const selected = this._completion.items[this._cmdIdx];
        if (selected !== undefined) {
          if (this._completion.mode === "command") {
            this._buf = "/" + selected + " ";
          } else {
            const spaceIdx = this._buf.indexOf(" ");
            this._buf = this._buf.slice(0, spaceIdx + 1) + selected + " ";
          }
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
          return;
        }
      }
      const line = this._buf.trim();
      this._buf = ""; this._cursor = 0; this._histIdx = -1;
      this._refreshCompletion();
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

    // ─── Backspace ────────────────────────────────────────────
    if (data === "\x7f") {
      if (this._cursor > 0) {
        this._buf = this._buf.slice(0, this._cursor - 1) + this._buf.slice(this._cursor);
        this._cursor--;
        this._refreshCompletion();
        this.emit("change");
      }
      return;
    }

    // ─── Arrow Up ─────────────────────────────────────────────
    if (data === ESC + "[A") {
      if (this.cmdPopupActive) {
        this._cmdIdx = Math.max(0, this._cmdIdx - 1);
        this.emit("change");
      } else {
        if (this._histIdx === -1) this._histTemp = this._buf;
        if (this._histIdx < this._history.length - 1) {
          this._histIdx++;
          this._buf    = this._history[this._histIdx];
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
        }
      }
      return;
    }

    // ─── Arrow Down ───────────────────────────────────────────
    if (data === ESC + "[B") {
      if (this.cmdPopupActive) {
        this._cmdIdx = Math.min(this._completion.items.length - 1, this._cmdIdx + 1);
        this.emit("change");
      } else {
        if (this._histIdx > -1) {
          this._histIdx--;
          this._buf    = this._histIdx === -1 ? this._histTemp : this._history[this._histIdx];
          this._cursor = this._buf.length;
          this._refreshCompletion();
          this.emit("change");
        }
      }
      return;
    }

    // ─── Other navigation ─────────────────────────────────────
    if (data === ESC + "[D" && this._cursor > 0)                { this._cursor--; this.emit("change"); return; }
    if (data === ESC + "[C" && this._cursor < this._buf.length) { this._cursor++; this.emit("change"); return; }
    if (data === ESC + "[5~") { this.emit("scroll",  5); return; }
    if (data === ESC + "[6~") { this.emit("scroll", -5); return; }
    if (data === "\x0C")      { this.emit("clear");       return; }

    // ─── ESC ──────────────────────────────────────────────────
    if (data === ESC) {
      if (this.cmdPopupActive) {
        this._buf = ""; this._cursor = 0;
        this._refreshCompletion();
        this.emit("change");
      }
      return;
    }

    // ─── Printable characters ─────────────────────────────────
    if (data.length >= 1) {
      const printable = data.length === 1
        ? (data >= " " ? data : "")
        : data.replace(/[\x00-\x1F\x7F]/g, "");
      if (!printable) return;
      this._buf    = this._buf.slice(0, this._cursor) + printable + this._buf.slice(this._cursor);
      this._cursor += printable.length;
      this._refreshCompletion();
      this.emit("change");
    }
  }

  private _refreshCompletion(): void {
    if (this._engine) {
      this._completion = this._engine.complete(this._buf);
    } else {
      this._completion = { items: [], mode: "command" };
    }
    this._cmdIdx = 0;
  }
}
```

- [ ] **Step 4: Run all input-handler tests**

```bash
npx vitest run __tests__/cli/input-handler.test.ts
```

Expected: all 17 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/input-handler.ts
git add -f __tests__/cli/input-handler.test.ts
git commit -m "fix(cli): rewrite InputHandler with CompletionEngine — fixes backspace and subcommand bugs"
```

---

## Task 4: Wire up — Renderer + CLIAdapter

**Files:**
- Modify: `src/cli/renderer.ts`
- Modify: `src/gateway/adapters/cli.ts`

- [ ] **Step 1: Update `src/cli/renderer.ts`**

Add import at the top alongside the existing imports:
```typescript
import type { CompletionEngine } from "./completion-engine.js";
```

Replace the `setCommandList` method (line 188-190) with:
```typescript
setCompletionEngine(engine: CompletionEngine): void {
  this.input.setCompletionEngine(engine);
}
```

- [ ] **Step 2: Update `src/gateway/adapters/cli.ts`**

Add import (alongside existing CLI imports):
```typescript
import { CompletionEngine } from "../../cli/completion-engine.js";
```

Replace line 54:
```typescript
// Before:
this.renderer.setCommandList(this.commands.listNames());

// After:
this.renderer.setCompletionEngine(new CompletionEngine(this.commands));
```

- [ ] **Step 3: Run TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run __tests__/cli/
```

Expected: all CLI tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/renderer.ts src/gateway/adapters/cli.ts
git commit -m "feat(cli): wire CompletionEngine into renderer and CLIAdapter"
```

---

## Verification checklist after all tasks

Manual smoke-test in the terminal:

1. Type `/spez` — popup should show and then hide (no matches). Press backspace — popup should reappear showing commands starting with `/spe` (`specialization`).
2. Type `/skills` then space — popup should show `list` and `install`.
3. Type `/specialization` then space — popup should show `list show create delete update`.
4. Type `/specialization s` — popup should filter to `show`.
5. Press `↓` to select an item, press `Enter` — buf should contain the selection with trailing space.
6. Select `skills` with Enter — popup should reopen showing skill subcommands.
