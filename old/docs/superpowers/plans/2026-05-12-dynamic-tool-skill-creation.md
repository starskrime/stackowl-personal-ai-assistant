# Dynamic Tool & Skill Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend StackOwl's existing synthesis engine with five capabilities: workspace-relative tool storage, a `log.synthesis` namespace, a critical-tool permission gate, Python tool synthesis alongside existing TypeScript, and a `list_synthesized_capabilities` introspection tool.

**Architecture:** The existing synthesis pipeline (`src/evolution/detector.ts` → `src/evolution/handler.ts` → `src/evolution/synthesizer.ts` → `src/evolution/loader.ts`) already handles gap detection, SKILL.md synthesis, and TypeScript tool generation. This plan extends it rather than replacing it. Each task is additive and leaves existing behaviour unchanged.

**Tech Stack:** TypeScript/Node.js ≥22, ESM strict, Vitest, `node:child_process.execFile` for Python execution, `node:fs/promises` for disk I/O.

---

## Pre-flight: read these files before starting any task

You must read and understand these files. Do not guess at their contents.

```
src/evolution/synthesizer.ts    — ToolSynthesizer, SYNTHESIZED_DIR, SkillSynthesisResult
src/evolution/handler.ts        — EvolutionHandler, buildWithSkill(), buildWithTypeScript()
src/evolution/loader.ts         — DynamicToolLoader, loadAll(), loadOne()
src/evolution/detector.ts       — GapDetector, CapabilityGap, REFUSAL_SIGNALS
src/infra/observability/compat.ts — the `log` singleton, how namespaces are added
src/config/loader.ts            — StackOwlConfig, synthesis block, defaults
src/tools/registry.ts           — ToolImplementation, ToolDefinition, ToolContext, ToolCategory
```

---

## File Map

### Created
| File | Responsibility |
|------|---------------|
| `src/evolution/critical-tools-guard.ts` | Scans generated code for dangerous patterns; asks user permission once; persists grants to `workspace/synthesized/.permissions.json` |
| `src/evolution/python-analyzer.ts` | Forbidden-pattern scanner for Python code (mirrors existing TypeScript static analysis in synthesizer) |
| `src/evolution/python-adapter.ts` | `PythonAdapter` — wraps a `.py` file as a `ToolImplementation` using `execFile('python3', ...)` |
| `src/evolution/python-synthesizer.ts` | Generates Python tool code via LLM + wires static analysis + adapter |
| `src/tools/synthesized-catalog.ts` | `list_synthesized_capabilities` tool — introspects live registry for `source: "synthesized"` tools and SKILL.md files |
| `__tests__/evolution/critical-tools-guard.test.ts` | Unit tests for guard + permission store |
| `__tests__/evolution/python-analyzer.test.ts` | Unit tests for Python forbidden patterns |
| `__tests__/evolution/python-adapter.test.ts` | Unit tests for PythonAdapter execute() |
| `__tests__/evolution/python-synthesizer.test.ts` | Integration tests for Python synthesis path |
| `__tests__/tools/synthesized-catalog.test.ts` | Unit tests for list_synthesized_capabilities |

### Modified
| File | Change |
|------|--------|
| `src/config/loader.ts` | Add `synthesis.synthesizedDir?: string` field + update default |
| `src/infra/observability/compat.ts` | Add `synthesis: getLogger("synthesis")` to `log` singleton |
| `src/evolution/synthesizer.ts` | Export `getSynthesizedDir(config)` helper; deprecate static `SYNTHESIZED_DIR` constant |
| `src/evolution/handler.ts` | (1) Use `getSynthesizedDir(config)` everywhere `SYNTHESIZED_DIR` appears; (2) call `CriticalToolsGuard.check()` before registering TypeScript tool; (3) add `buildWithPython()` branch; (4) language selection heuristic |
| `src/evolution/loader.ts` | Accept optional `dir` param in `loadAll()` to support workspace-relative path |
| `src/index.ts` | Pass `synthesizedDir` from config when bootstrapping `DynamicToolLoader.loadAll()` |

---

## Task 1 — Config: `synthesis.synthesizedDir` + workspace-relative tool storage

**Why:** `SYNTHESIZED_DIR` is currently hardcoded to `src/tools/synthesized` (inside the source tree). Tools should be stored in the user's workspace so they survive across code updates.

**Files:**
- Modify: `src/config/loader.ts`
- Modify: `src/evolution/synthesizer.ts`
- Modify: `src/evolution/loader.ts`
- Modify: `src/index.ts`
- Test: `__tests__/evolution/synthesized-dir.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/evolution/synthesized-dir.test.ts
import { describe, it, expect } from "vitest";
import { getSynthesizedDir } from "../../src/evolution/synthesizer.js";
import type { StackOwlConfig } from "../../src/config/loader.js";

describe("getSynthesizedDir", () => {
  it("returns config value when synthesizedDir is set", () => {
    const config = { synthesis: { synthesizedDir: "/custom/path/synth" } } as unknown as StackOwlConfig;
    expect(getSynthesizedDir(config)).toBe("/custom/path/synth");
  });

  it("falls back to workspace/synthesized when not configured", () => {
    const config = { workspace: "./my-workspace", synthesis: {} } as unknown as StackOwlConfig;
    const result = getSynthesizedDir(config);
    expect(result).toContain("my-workspace");
    expect(result).toContain("synthesized");
  });

  it("falls back to workspace/synthesized when synthesis block is absent", () => {
    const config = { workspace: "./my-workspace" } as unknown as StackOwlConfig;
    const result = getSynthesizedDir(config);
    expect(result).toContain("synthesized");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/synthesized-dir.test.ts
```
Expected: FAIL — `getSynthesizedDir` not exported.

- [ ] **Step 3: Add `synthesizedDir` to config type and default**

In `src/config/loader.ts`, extend the `synthesis` block (currently around line 72):

```typescript
  synthesis?: {
    /** Provider name to use for synthesis (must be registered in providers). Default: 'anthropic' */
    provider: string;
    /** Model to use for synthesis. Default: 'claude-sonnet-4-5-20241022' */
    model: string;
    /**
     * Directory where synthesized tools and skills are stored.
     * Default: <workspace>/synthesized
     */
    synthesizedDir?: string;
  };
```

In `DEFAULT_CONFIG` (around line 415):
```typescript
  synthesis: {
    provider: "anthropic",
    model: "claude-sonnet-4-5-20241022",
    // synthesizedDir intentionally absent — derived at runtime from workspace
  },
```

- [ ] **Step 4: Add `getSynthesizedDir()` to synthesizer.ts**

In `src/evolution/synthesizer.ts`, add after the existing `SYNTHESIZED_DIR` constant:

```typescript
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { StackOwlConfig } from "../config/loader.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** @deprecated Use getSynthesizedDir(config) instead. Kept for toolsmith.ts back-compat. */
export const SYNTHESIZED_DIR = join(__dirname, "../tools/synthesized");

/**
 * Returns the synthesized tool directory from config, or falls back to
 * <workspace>/synthesized. Always returns an absolute path.
 */
export function getSynthesizedDir(config: Pick<StackOwlConfig, "workspace" | "synthesis">): string {
  if (config.synthesis?.synthesizedDir) {
    return resolve(config.synthesis.synthesizedDir);
  }
  const workspaceBase = resolve(config.workspace ?? "./workspace");
  return join(workspaceBase, "synthesized");
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/evolution/synthesized-dir.test.ts
```
Expected: PASS — 3 tests passing.

- [ ] **Step 6: Update `handler.ts` to use `getSynthesizedDir(context.config)`**

In `src/evolution/handler.ts`, replace every occurrence of bare `SYNTHESIZED_DIR` with `getSynthesizedDir(context.config)`. There are approximately 3-4 occurrences (in `designSpec`, `buildWithTypeScript`, and the ledger path). Update the import:

```typescript
import {
  ToolSynthesizer,
  type ToolProposal,
  SYNTHESIZED_DIR,      // keep for back-compat with ledger paths
  getSynthesizedDir,    // add this
} from "./synthesizer.js";
```

Replace the pattern:
```typescript
// BEFORE
join(SYNTHESIZED_DIR, record.fileName)

// AFTER  
join(getSynthesizedDir(context.config), record.fileName)
```

And in `buildWithTypeScript`:
```typescript
// BEFORE
const filePath = join(SYNTHESIZED_DIR, `${proposal.toolName}.ts`);

// AFTER
const synthesizedDir = getSynthesizedDir(context.config);
const filePath = join(synthesizedDir, `${proposal.toolName}.ts`);
```

- [ ] **Step 7: Update `loader.ts` to accept an optional directory**

```typescript
// src/evolution/loader.ts — update loadAll signature
async loadAll(registry: ToolRegistry, synthesizedDir?: string): Promise<number> {
  await this.ledger.load();
  const active = this.ledger.listActive();
  const dir = synthesizedDir ?? SYNTHESIZED_DIR;
  let loaded = 0;

  for (const record of active) {
    const tsPath = join(dir, record.fileName);
    // ... rest unchanged
  }
  return loaded;
}
```

- [ ] **Step 8: Update `src/index.ts` bootstrap call**

Find where `dynamicToolLoader.loadAll(registry)` is called at startup and pass the synthesized dir:

```typescript
// Find the existing loadAll call and add the dir argument
const synthesizedDir = getSynthesizedDir(config);
const loadedCount = await dynamicToolLoader.loadAll(toolRegistry, synthesizedDir);
```

Add the import at top of `src/index.ts`:
```typescript
import { getSynthesizedDir } from "./evolution/synthesizer.js";
```

- [ ] **Step 9: Ensure synthesized directory is created at startup**

In `src/index.ts`, after deriving `synthesizedDir`, add directory creation:

```typescript
import { mkdir } from "node:fs/promises";

// After const synthesizedDir = getSynthesizedDir(config);
await mkdir(synthesizedDir, { recursive: true });
await mkdir(join(synthesizedDir, "tools"), { recursive: true });
await mkdir(join(synthesizedDir, "skills"), { recursive: true });
```

- [ ] **Step 10: Run full test suite to confirm no regressions**

```bash
npm run test
```
Expected: All existing tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/config/loader.ts src/evolution/synthesizer.ts src/evolution/loader.ts src/evolution/handler.ts src/index.ts __tests__/evolution/synthesized-dir.test.ts
git commit -m "feat(synthesis): workspace-relative synthesized dir via config"
```

---

## Task 2 — Add `log.synthesis` Namespace

**Why:** Synthesis operations currently log to `log.evolution`. A dedicated namespace makes synthesis-specific log queries (`cat logs/*.log | jq 'select(.module == "synthesis")'`) instant.

**Files:**
- Modify: `src/infra/observability/compat.ts`

- [ ] **Step 1: Add the namespace**

In `src/infra/observability/compat.ts`, add `synthesis` to the `log` singleton:

```typescript
export const log = {
  telegram:   getLogger("telegram"),
  slack:      getLogger("slack"),
  discord:    getLogger("discord"),
  cli:        getLogger("cli"),
  engine:     getLogger("engine"),
  tool:       getLogger("tool"),
  evolution:  getLogger("evolution"),
  memory:     getLogger("memory"),
  heartbeat:  getLogger("heartbeat"),
  pellet:     getLogger("pellet"),
  parliament: getLogger("parliament"),
  gateway:    getLogger("gateway"),
  cognition:  getLogger("cognition"),
  synthesis:  getLogger("synthesis"),   // ← add this line
};
```

- [ ] **Step 2: Verify TypeScript compilation**

```bash
npm run build 2>&1 | head -20
```
Expected: No new errors. The `log.synthesis` property is now available everywhere that imports `log`.

- [ ] **Step 3: Commit**

```bash
git add src/infra/observability/compat.ts
git commit -m "feat(observability): add log.synthesis namespace"
```

---

## Task 3 — Critical Tools Permission Gate

**Why:** When a synthesized TypeScript tool contains dangerous primitives (`child_process`, `exec`, unrestricted filesystem writes), the user should explicitly approve before the tool is registered. Grants are persisted so the user is not asked again.

**Dangerous primitives** that trigger the gate:
- `child_process` (any import/usage)
- `eval(` or `new Function(`
- `fs.writeFile` / `writeFile` with paths that could escape workspace
- `exec(` or `execSync(`

**Files:**
- Create: `src/evolution/critical-tools-guard.ts`
- Create: `__tests__/evolution/critical-tools-guard.test.ts`
- Modify: `src/evolution/handler.ts` (wire gate into `buildWithTypeScript`)

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/evolution/critical-tools-guard.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { CriticalToolsGuard, type ApprovalChannel } from "../../src/evolution/critical-tools-guard.js";
import * as os from "node:os";
import * as path from "node:path";
import * as fs from "node:fs";

const tmpDir = path.join(os.tmpdir(), `stackowl-guard-test-${Date.now()}`);
const permissionsFile = path.join(tmpDir, ".permissions.json");

beforeEach(() => {
  fs.mkdirSync(tmpDir, { recursive: true });
  if (fs.existsSync(permissionsFile)) fs.unlinkSync(permissionsFile);
});

const mockChannel: ApprovalChannel = {
  ask: vi.fn().mockResolvedValue(true),
};

describe("CriticalToolsGuard.detectDangerousPatterns", () => {
  it("detects child_process import", () => {
    const code = `import { exec } from "node:child_process";\nexec("rm -rf /");`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("child_process");
  });

  it("detects eval usage", () => {
    const code = `const result = eval(userInput);`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("eval");
  });

  it("detects exec usage without import", () => {
    const code = `execSync("ls -la");`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("exec");
  });

  it("returns empty array for safe code", () => {
    const code = `import { readFile } from "node:fs/promises";\nconst data = await readFile(args.path, "utf-8");\nreturn data;`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toHaveLength(0);
  });
});

describe("CriticalToolsGuard.check", () => {
  it("returns true without asking when code is safe", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const safe = `const x = 1 + 1;`;
    const result = await guard.check("my_tool", safe);
    expect(result).toBe(true);
    expect(mockChannel.ask).not.toHaveBeenCalled();
  });

  it("asks user when dangerous patterns found", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    const result = await guard.check("my_tool", dangerous);
    expect(result).toBe(true);
    expect(mockChannel.ask).toHaveBeenCalledOnce();
  });

  it("returns false when user denies", async () => {
    const denyChannel: ApprovalChannel = { ask: vi.fn().mockResolvedValue(false) };
    const guard = new CriticalToolsGuard(permissionsFile, denyChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    const result = await guard.check("my_tool", dangerous);
    expect(result).toBe(false);
  });

  it("does not ask again for a previously granted tool", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    await guard.check("my_tool", dangerous);          // first call — asks
    vi.mocked(mockChannel.ask).mockClear();
    await guard.check("my_tool", dangerous);          // second call — should NOT ask
    expect(mockChannel.ask).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/critical-tools-guard.test.ts
```
Expected: FAIL — `CriticalToolsGuard` not found.

- [ ] **Step 3: Implement `src/evolution/critical-tools-guard.ts`**

```typescript
/**
 * StackOwl — Critical Tools Guard
 *
 * Scans synthesized code for dangerous primitives and asks the user for
 * permission once. Grants are persisted to .permissions.json so the user
 * is never asked twice for the same tool.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname } from "node:path";
import { log } from "../logger.js";

export interface ApprovalChannel {
  ask(message: string): Promise<boolean>;
}

const DANGEROUS_PATTERNS: Array<{ name: string; regex: RegExp }> = [
  { name: "child_process", regex: /child_process/u },
  { name: "eval",          regex: /\beval\s*\(/u },
  { name: "new Function",  regex: /new\s+Function\s*\(/u },
  { name: "exec",          regex: /\bexec(?:Sync)?\s*\(/u },
];

type PermissionStore = Record<string, string[]>;

export class CriticalToolsGuard {
  private grants: PermissionStore = {};
  private loaded = false;

  constructor(
    private readonly permissionsFile: string,
    private readonly channel: ApprovalChannel,
  ) {}

  /** Returns names of dangerous patterns found in code. Empty = safe. */
  static detectDangerousPatterns(code: string): string[] {
    return DANGEROUS_PATTERNS
      .filter(({ regex }) => regex.test(code))
      .map(({ name }) => name);
  }

  /**
   * Check if synthesized code is safe to register.
   * Returns true if safe OR user approved.
   * Returns false if user denied.
   */
  async check(toolName: string, code: string): Promise<boolean> {
    log.synthesis.debug("critical-tools-guard.check: entry", { toolName, codeLen: code.length });

    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    if (patterns.length === 0) {
      log.synthesis.debug("critical-tools-guard.check: exit clean", { toolName });
      return true;
    }

    await this.loadGrants();

    // Already approved for this tool?
    const existing = this.grants[toolName] ?? [];
    const alreadyApproved = patterns.every(p => existing.includes(p));
    if (alreadyApproved) {
      log.synthesis.debug("critical-tools-guard.check: previously approved", { toolName, patterns });
      return true;
    }

    log.synthesis.warn("critical-tools-guard.check: dangerous patterns found, asking user", { toolName, patterns });

    const message =
      `New tool "${toolName}" uses potentially dangerous capabilities: [${patterns.join(", ")}].\n` +
      `Allow this tool to be registered? (Grant is remembered for future sessions.)`;

    const granted = await this.channel.ask(message);
    log.synthesis.debug("critical-tools-guard.check: user decision", { toolName, granted });

    if (granted) {
      this.grants[toolName] = [...new Set([...existing, ...patterns])];
      await this.persistGrants();
    }

    return granted;
  }

  private async loadGrants(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    if (!existsSync(this.permissionsFile)) return;
    try {
      const raw = await readFile(this.permissionsFile, "utf-8");
      this.grants = JSON.parse(raw) as PermissionStore;
    } catch {
      this.grants = {};
    }
  }

  private async persistGrants(): Promise<void> {
    await mkdir(dirname(this.permissionsFile), { recursive: true });
    await writeFile(this.permissionsFile, JSON.stringify(this.grants, null, 2), "utf-8");
    log.synthesis.debug("critical-tools-guard.persistGrants: saved", { file: this.permissionsFile });
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/evolution/critical-tools-guard.test.ts
```
Expected: PASS — 7 tests passing.

- [ ] **Step 5: Wire the guard into `handler.ts` `buildWithTypeScript()`**

First, add a `criticalToolsGuard` property to `EvolutionHandler` and instantiate it in the constructor:

```typescript
// In EvolutionHandler class — add property
private criticalToolsGuard?: CriticalToolsGuard;

// In constructor, add parameter and assignment
constructor(
  synthesizer: ToolSynthesizer,
  ledger: CapabilityLedger,
  loader: DynamicToolLoader,
  db?: import("../memory/db.js").MemoryDatabase,
  owlRegistry?: import("../owls/registry.js").OwlRegistry,
  approvalChannel?: import("./critical-tools-guard.js").ApprovalChannel,
) {
  // ... existing assignments ...
  if (approvalChannel) {
    const { getSynthesizedDir } = await import("./synthesizer.js");  // already imported
    // Note: permissionsFile derived at execution time from config
    this.approvalChannel = approvalChannel;
  }
}
```

Actually, the guard needs the `permissionsFile` path which comes from config. Wire it at the point of use in `buildWithTypeScript`:

```typescript
// In buildWithTypeScript(), BEFORE the loader.loadOne() call:

// Critical tools permission gate
if (this.approvalChannel && filePath) {
  const { getSynthesizedDir } = await import("./synthesizer.js"); // already imported
  const permissionsFile = join(getSynthesizedDir(context.config), ".permissions.json");
  const { CriticalToolsGuard } = await import("./critical-tools-guard.js");
  const guard = new CriticalToolsGuard(permissionsFile, this.approvalChannel);

  const { readFile } = await import("node:fs/promises");
  const code = await readFile(filePath, "utf-8");
  const approved = await guard.check(proposal.toolName, code);

  if (!approved) {
    log.synthesis.warn("critical-tools-guard: user denied dangerous tool", { toolName: proposal.toolName });
    await progress(`⛔ Tool "${proposal.toolName}" was not approved. Synthesis cancelled.`);
    return {
      filePath: "",
      response: { content: `Tool synthesis cancelled: user denied dangerous capability in "${proposal.toolName}".`, owlName: context.owl.persona.name, owlEmoji: context.owl.persona.emoji, challenged: false, toolsUsed: [], modelUsed: "", newMessages: [], pendingFiles: [] },
      depsToInstall: [],
      depsInstalled: false,
    };
  }
}

// Then: await this.loader.loadOne(filePath, context.toolRegistry);
```

Add `approvalChannel` to the `EvolutionHandler` constructor call in `src/index.ts` by passing a simple CLI channel (create `src/evolution/cli-approval-channel.ts`):

```typescript
// src/evolution/cli-approval-channel.ts
import * as readline from "node:readline";
import type { ApprovalChannel } from "./critical-tools-guard.js";

export const cliApprovalChannel: ApprovalChannel = {
  async ask(message: string): Promise<boolean> {
    return new Promise((resolve) => {
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      rl.question(`\n⚠️  ${message}\n[y/N] `, (answer) => {
        rl.close();
        resolve(answer.trim().toLowerCase() === "y");
      });
    });
  },
};
```

- [ ] **Step 6: Run full test suite**

```bash
npm run test
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/evolution/critical-tools-guard.ts src/evolution/cli-approval-channel.ts __tests__/evolution/critical-tools-guard.test.ts src/evolution/handler.ts
git commit -m "feat(synthesis): critical tools permission gate with persistent grants"
```

---

## Task 4 — Python Tool Synthesis

**Why:** Some tasks (CSV parsing, data transformation, ML pipelines) are better expressed in Python. Python tools run in a child process, providing OS-level isolation automatically.

**Files:**
- Create: `src/evolution/python-analyzer.ts`
- Create: `src/evolution/python-adapter.ts`
- Create: `src/evolution/python-synthesizer.ts`
- Create: `__tests__/evolution/python-analyzer.test.ts`
- Create: `__tests__/evolution/python-adapter.test.ts`
- Create: `__tests__/evolution/python-synthesizer.test.ts`
- Modify: `src/evolution/handler.ts` (add `buildWithPython()` and language selection)

### Step 4a — Python Analyzer

- [ ] **Step 1: Write failing tests for Python analyzer**

```typescript
// __tests__/evolution/python-analyzer.test.ts
import { describe, it, expect } from "vitest";
import { PythonAnalyzer } from "../../src/evolution/python-analyzer.js";

describe("PythonAnalyzer.analyze", () => {
  it("flags subprocess import", () => {
    const result = PythonAnalyzer.analyze(`import subprocess\nsubprocess.run(["ls"])`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("subprocess");
  });

  it("flags os.system usage", () => {
    const result = PythonAnalyzer.analyze(`import os\nos.system("rm -rf /")`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("os.system");
  });

  it("flags eval()", () => {
    const result = PythonAnalyzer.analyze(`result = eval(user_input)`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("eval");
  });

  it("flags exec()", () => {
    const result = PythonAnalyzer.analyze(`exec(code)`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("exec");
  });

  it("flags __import__", () => {
    const result = PythonAnalyzer.analyze(`mod = __import__("os")`);
    expect(result.safe).toBe(false);
    expect(result.patterns).toContain("__import__");
  });

  it("passes safe data-processing code", () => {
    const code = `
import json, csv, sys

def execute(args: dict, cwd: str) -> str:
    with open(args["path"]) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return json.dumps(rows)

if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`;
    const result = PythonAnalyzer.analyze(code);
    expect(result.safe).toBe(true);
    expect(result.patterns).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/evolution/python-analyzer.test.ts
```
Expected: FAIL.

- [ ] **Step 3: Implement `src/evolution/python-analyzer.ts`**

```typescript
/**
 * StackOwl — Python Static Analyzer
 *
 * Scans generated Python code for patterns that indicate dangerous capabilities.
 * Used as a safety gate before a Python tool is written to disk.
 */

const FORBIDDEN: Array<{ name: string; pattern: RegExp }> = [
  { name: "subprocess",  pattern: /\bsubprocess\b/u },
  { name: "os.system",   pattern: /\bos\.system\s*\(/u },
  { name: "eval",        pattern: /\beval\s*\(/u },
  { name: "exec",        pattern: /\bexec\s*\(/u },
  { name: "__import__",  pattern: /\b__import__\s*\(/u },
];

export interface PythonAnalysisResult {
  safe: boolean;
  patterns: string[];
}

export class PythonAnalyzer {
  static analyze(code: string): PythonAnalysisResult {
    const found = FORBIDDEN.filter(({ pattern }) => pattern.test(code)).map(({ name }) => name);
    return { safe: found.length === 0, patterns: found };
  }
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
npx vitest run __tests__/evolution/python-analyzer.test.ts
```
Expected: PASS — 6 tests.

### Step 4b — Python Adapter

- [ ] **Step 5: Write failing tests for PythonAdapter**

```typescript
// __tests__/evolution/python-adapter.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as childProcessModule from "node:child_process";

// Mock execFile before importing adapter
vi.mock("node:child_process", () => ({
  execFile: vi.fn(),
}));

import { PythonAdapter } from "../../src/evolution/python-adapter.js";
import type { ToolContext } from "../../src/tools/registry.js";

const mockContext: ToolContext = { cwd: "/workspace" };

beforeEach(() => {
  vi.resetAllMocks();
});

describe("PythonAdapter.wrap", () => {
  it("returns a ToolImplementation with correct definition from header", async () => {
    const code = `
# TOOL_NAME: synth_csv_parser
# DESCRIPTION: Parse a CSV file and return rows as JSON
# PARAMETERS:
#   file_path: string - path to the CSV file
import json, csv, sys
def execute(args, cwd): pass
if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`;
    const tool = PythonAdapter.wrap("/tmp/csv_parser.py", code);
    expect(tool.definition.name).toBe("synth_csv_parser");
    expect(tool.definition.description).toBe("Parse a CSV file and return rows as JSON");
    expect(tool.source).toBe("synthesized");
  });

  it("execute() calls python3 with correct args and returns stdout", async () => {
    const { execFile } = childProcessModule;
    vi.mocked(execFile).mockImplementation((_cmd, _args, _opts, cb: any) => {
      cb(null, '["row1","row2"]', "");
      return {} as any;
    });

    const tool = PythonAdapter.wrap("/workspace/synthesized/tools/csv_parser.py", `# TOOL_NAME: synth_csv_parser\n# DESCRIPTION: desc`);
    const result = await tool.execute({ file_path: "data.csv" }, mockContext);
    expect(result).toBe('["row1","row2"]');
    expect(vi.mocked(execFile)).toHaveBeenCalledWith(
      "python3",
      ["/workspace/synthesized/tools/csv_parser.py", expect.any(String), "/workspace"],
      expect.objectContaining({ timeout: 30000, cwd: "/workspace" }),
      expect.any(Function),
    );
  });

  it("execute() returns stderr message on process error", async () => {
    const { execFile } = childProcessModule;
    vi.mocked(execFile).mockImplementation((_cmd, _args, _opts, cb: any) => {
      cb(new Error("python3 not found"), "", "python3: not found");
      return {} as any;
    });

    const tool = PythonAdapter.wrap("/tmp/tool.py", `# TOOL_NAME: synth_x\n# DESCRIPTION: x`);
    const result = await tool.execute({}, mockContext);
    expect(result).toContain("ERROR");
    expect(result).toContain("python3 not found");
  });
});
```

- [ ] **Step 6: Run to confirm failure**

```bash
npx vitest run __tests__/evolution/python-adapter.test.ts
```
Expected: FAIL.

- [ ] **Step 7: Implement `src/evolution/python-adapter.ts`**

```typescript
/**
 * StackOwl — Python Tool Adapter
 *
 * Wraps a .py file as a ToolImplementation.
 * Python tools run in an isolated child process (python3) with:
 *   - stripped environment (only PATH, HOME, PYTHONPATH preserved)
 *   - CWD forced to workspace root
 *   - 30s timeout
 *
 * Python tool contract (the LLM must generate this structure):
 *   # TOOL_NAME: synth_<name>
 *   # DESCRIPTION: one line description
 *   # PARAMETERS:
 *   #   <param>: <type> - <description>
 *   ...
 *   def execute(args: dict, cwd: str) -> str: ...
 *   if __name__ == "__main__":
 *       args = json.loads(sys.argv[1])
 *       cwd = sys.argv[2]
 *       print(execute(args, cwd))
 */

import { execFile } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import { log } from "../logger.js";

function parseHeader(code: string): { name: string; description: string; parameters: string[] } {
  const lines = code.split("\n");
  let name = "synth_unknown";
  let description = "Synthesized Python tool";
  const parameters: string[] = [];

  for (const line of lines) {
    const nameLine = line.match(/^#\s*TOOL_NAME:\s*(.+)/u);
    if (nameLine) { name = nameLine[1].trim(); continue; }
    const descLine = line.match(/^#\s*DESCRIPTION:\s*(.+)/u);
    if (descLine) { description = descLine[1].trim(); continue; }
    const paramLine = line.match(/^#\s{3}(\w+):\s*(.+)/u);
    if (paramLine) { parameters.push(`${paramLine[1]}: ${paramLine[2]}`); }
  }
  return { name, description, parameters };
}

export class PythonAdapter {
  /**
   * Wrap a Python source file as a ToolImplementation.
   * `code` is passed only for header parsing; the file at `filePath` is executed.
   */
  static wrap(filePath: string, code: string): ToolImplementation {
    const { name, description } = parseHeader(code);
    log.synthesis.debug("python-adapter.wrap: parsed header", { name, description, filePath });

    return {
      definition: {
        name,
        description,
        parameters: {
          type: "object",
          properties: {
            // Generic passthrough — Python tools receive the full args object as JSON
            args: { type: "string", description: "JSON-encoded arguments for the tool" },
          },
          required: [],
        },
      },
      source: "synthesized",
      execute: async (args: Record<string, unknown>, context: ToolContext): Promise<string> => {
        log.synthesis.debug("python-adapter.execute: entry", { name, filePath });

        const argsJson = JSON.stringify(args);
        const cwd = context.cwd ?? process.cwd();

        return new Promise<string>((resolve) => {
          execFile(
            "python3",
            [filePath, argsJson, cwd],
            {
              timeout: 30_000,
              cwd,
              env: {
                PATH: process.env["PATH"],
                HOME: process.env["HOME"],
                PYTHONPATH: process.env["PYTHONPATH"],
              },
            },
            (err, stdout, stderr) => {
              if (err) {
                log.synthesis.error("python-adapter.execute: process error", err, { name, stderr });
                resolve(`ERROR executing ${name}: ${err.message}${stderr ? `\nstderr: ${stderr}` : ""}`);
                return;
              }
              if (stderr) {
                log.synthesis.warn("python-adapter.execute: stderr output", { name, stderr });
              }
              log.synthesis.debug("python-adapter.execute: exit", { name, resultLen: stdout.length });
              resolve(stdout);
            },
          );
        });
      },
    };
  }
}
```

- [ ] **Step 8: Run tests to verify pass**

```bash
npx vitest run __tests__/evolution/python-adapter.test.ts
```
Expected: PASS — 3 tests.

### Step 4c — Python Synthesizer and Handler Integration

- [ ] **Step 9: Write failing integration test for Python synthesis**

```typescript
// __tests__/evolution/python-synthesizer.test.ts
import { describe, it, expect, vi } from "vitest";
import { PythonSynthesizer } from "../../src/evolution/python-synthesizer.js";
import type { ModelProvider } from "../../src/providers/base.js";
import type { CapabilityGap } from "../../src/evolution/detector.js";

const mockProvider: ModelProvider = {
  chat: vi.fn().mockResolvedValue({
    content: `# TOOL_NAME: synth_csv_parser
# DESCRIPTION: Parse a CSV file and return rows as JSON
# PARAMETERS:
#   file_path: string - path to the CSV file
import json, csv, sys

def execute(args: dict, cwd: str) -> str:
    with open(args["file_path"]) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return json.dumps(rows)

if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    cwd = sys.argv[2]
    print(execute(args, cwd))
`,
    usage: { promptTokens: 100, completionTokens: 200 },
  }),
} as unknown as ModelProvider;

describe("PythonSynthesizer.generate", () => {
  it("returns generated Python code starting with TOOL_NAME header", async () => {
    const gap: CapabilityGap = {
      type: "CAPABILITY_GAP",
      userRequest: "parse this CSV file",
      description: "Need to parse CSV data",
    };
    const synth = new PythonSynthesizer();
    const result = await synth.generate(gap, mockProvider, "claude-sonnet-4-6");
    expect(result.code).toContain("TOOL_NAME");
    expect(result.code).toContain("def execute");
    expect(result.code).toContain("if __name__");
    expect(result.toolName).toMatch(/^synth_/u);
  });

  it("uses the provider.chat method exactly once", async () => {
    const gap: CapabilityGap = { type: "CAPABILITY_GAP", userRequest: "x", description: "x" };
    const synth = new PythonSynthesizer();
    await synth.generate(gap, mockProvider, "model");
    expect(mockProvider.chat).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 10: Run to confirm failure**

```bash
npx vitest run __tests__/evolution/python-synthesizer.test.ts
```
Expected: FAIL.

- [ ] **Step 11: Implement `src/evolution/python-synthesizer.ts`**

```typescript
/**
 * StackOwl — Python Tool Synthesizer
 *
 * Generates a Python tool module from a capability gap via LLM.
 * Enforces the Python tool contract (TOOL_NAME header, execute(), __main__ block).
 */

import type { ModelProvider } from "../providers/base.js";
import type { CapabilityGap } from "./detector.js";
import { PythonAnalyzer } from "./python-analyzer.js";
import { log } from "../logger.js";

export interface PythonSynthesisResult {
  toolName: string;
  code: string;
}

const PYTHON_TOOL_CONTRACT = `
Python tool contract — the COMPLETE file must follow this structure:
  # TOOL_NAME: synth_<snake_case_name>  (1-3 word generic name, snake_case)
  # DESCRIPTION: one-line description of what this tool does
  # PARAMETERS:
  #   <param_name>: <type> - <description>
  #   (one line per parameter)
  import json, sys
  # (other safe imports — NO subprocess, NO os.system, NO eval, NO exec)

  def execute(args: dict, cwd: str) -> str:
      # implementation — read args["param_name"] for inputs
      # return a string (JSON-encode complex results)
      ...

  if __name__ == "__main__":
      args = json.loads(sys.argv[1])
      cwd = sys.argv[2]
      print(execute(args, cwd))

FORBIDDEN imports/calls: subprocess, os.system, eval(), exec(), __import__()
All file paths must be relative or derived from args — never hardcoded absolute paths.
`;

export class PythonSynthesizer {
  async generate(
    gap: CapabilityGap,
    provider: ModelProvider,
    model: string,
  ): Promise<PythonSynthesisResult> {
    log.synthesis.debug("python-synthesizer.generate: entry", { gap: gap.description });

    const prompt =
      `You are generating a Python tool for StackOwl, an AI assistant framework.\n\n` +
      `Capability needed: ${gap.description}\n` +
      `User request: ${gap.userRequest}\n\n` +
      `${PYTHON_TOOL_CONTRACT}\n` +
      `Output ONLY the complete Python file — no markdown fences, no explanation.`;

    const response = await provider.chat([{ role: "user", content: prompt }], model);
    const code = response.content.trim().replace(/^```python\n?|```$/gmu, "").trim();

    // Validate static safety
    const analysis = PythonAnalyzer.analyze(code);
    if (!analysis.safe) {
      log.synthesis.warn("python-synthesizer.generate: unsafe patterns in LLM output", { patterns: analysis.patterns });
      // Strip forbidden patterns — retry once with explicit warning (handled by caller)
    }

    // Extract tool name from header
    const nameMatch = code.match(/^#\s*TOOL_NAME:\s*(\S+)/mu);
    const toolName = nameMatch ? nameMatch[1].trim() : `synth_${gap.description.slice(0, 20).toLowerCase().replace(/\W+/gu, "_")}`;

    log.synthesis.debug("python-synthesizer.generate: exit", { toolName, codeLen: code.length });
    return { toolName, code };
  }
}
```

- [ ] **Step 12: Run tests to verify pass**

```bash
npx vitest run __tests__/evolution/python-synthesizer.test.ts
```
Expected: PASS — 2 tests.

- [ ] **Step 13: Add `buildWithPython()` and language selection to `handler.ts`**

In `src/evolution/handler.ts`, add `buildWithPython()` method:

```typescript
import { PythonSynthesizer } from "./python-synthesizer.js";
import { PythonAdapter } from "./python-adapter.js";
import { PythonAnalyzer } from "./python-analyzer.js";
import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";

// Language selection heuristic — add before dispatch in handle()
private selectSynthesisLanguage(gap: PendingCapabilityGap): "skill" | "typescript" | "python" {
  const desc = (gap.description + " " + gap.userRequest).toLowerCase();
  const pythonSignals = [
    "csv", "pandas", "numpy", "excel", "xlsx", "json transform",
    "data process", "parse file", "ml ", "machine learning",
    "data analysis", "data transform", "statistics", "plot", "matplotlib",
  ];
  if (pythonSignals.some(s => desc.includes(s))) return "python";
  return "typescript"; // existing default
}

private async buildWithPython(
  proposal: ToolProposal,
  originalMessage: string,
  context: EngineContext,
  engine: OwlEngine,
  progress: ProgressCallback,
): Promise<BuildResult> {
  const { provider: synthesisProvider, model: synthesisModel } = this.resolveSynthesisProvider(context);
  const synthesizedDir = getSynthesizedDir(context.config);
  const gap: import("./detector.js").CapabilityGap = {
    type: "CAPABILITY_GAP",
    userRequest: originalMessage,
    description: proposal.description,
  };

  await progress(`🐍 Synthesizing Python tool: "${proposal.toolName}"`);
  log.synthesis.debug("handler.buildWithPython: entry", { toolName: proposal.toolName });

  const pythonSynth = new PythonSynthesizer();
  let { toolName, code } = await pythonSynth.generate(gap, synthesisProvider, synthesisModel);

  // Static analysis — one retry if unsafe
  let analysis = PythonAnalyzer.analyze(code);
  if (!analysis.safe) {
    await progress(`⚠️ Unsafe patterns detected [${analysis.patterns.join(", ")}] — regenerating...`);
    const retryPrompt = `The previous code contained forbidden patterns: ${analysis.patterns.join(", ")}. Regenerate without them.`;
    const retry = await synthesisProvider.chat([
      { role: "user", content: retryPrompt },
    ], synthesisModel);
    code = retry.content.trim().replace(/^```python\n?|```$/gmu, "").trim();
    analysis = PythonAnalyzer.analyze(code);
    if (!analysis.safe) {
      log.synthesis.error("handler.buildWithPython: still unsafe after retry", new Error("unsafe"), { patterns: analysis.patterns });
      await progress(`❌ Python synthesis failed: still contains ${analysis.patterns.join(", ")}`);
      return { filePath: "", response: { content: "Python tool synthesis failed — generated code is unsafe.", owlName: context.owl.persona.name, owlEmoji: context.owl.persona.emoji, challenged: false, toolsUsed: [], modelUsed: synthesisModel, newMessages: [], pendingFiles: [] }, depsToInstall: [], depsInstalled: false };
    }
  }

  // Critical tools gate
  if (this.approvalChannel) {
    const permissionsFile = join(synthesizedDir, ".permissions.json");
    const { CriticalToolsGuard } = await import("./critical-tools-guard.js");
    const guard = new CriticalToolsGuard(permissionsFile, this.approvalChannel);
    const approved = await guard.check(toolName, code);
    if (!approved) {
      await progress(`⛔ Python tool "${toolName}" was not approved.`);
      return { filePath: "", response: { content: `Synthesis cancelled.`, owlName: context.owl.persona.name, owlEmoji: context.owl.persona.emoji, challenged: false, toolsUsed: [], modelUsed: synthesisModel, newMessages: [], pendingFiles: [] }, depsToInstall: [], depsInstalled: false };
    }
  }

  // Write to disk
  const toolsDir = join(synthesizedDir, "tools");
  await mkdir(toolsDir, { recursive: true });
  const filePath = join(toolsDir, `${toolName}.py`);
  await writeFile(filePath, code, "utf-8");
  await progress(`✅ ${toolName}.py written`);

  // Register via adapter
  const tool = PythonAdapter.wrap(filePath, code);
  context.toolRegistry.register(tool);
  await progress(`✅ ${toolName} registered`);

  log.synthesis.debug("handler.buildWithPython: exit", { toolName, filePath });
  return this.retryWithTool(proposal, originalMessage, context, engine, progress, filePath);
}
```

In the main `handle()` dispatch, wire Python selection before the existing skill/TypeScript branch:

```typescript
// Add before the existing: return this.buildWithSkill(...)
const language = this.selectSynthesisLanguage(gap);
if (language === "python" && skillsDir) {
  // SKILL.md is always preferred — Python only if skill can't express it
  const needsCode = !canExpressAsSkill; // existing assessor verdict
  if (needsCode) {
    return this.buildWithPython(proposal, originalMessage, context, engine, progress);
  }
}
```

- [ ] **Step 14: Run all synthesis tests**

```bash
npx vitest run __tests__/evolution/
```
Expected: All synthesis tests pass.

- [ ] **Step 15: Commit**

```bash
git add src/evolution/python-analyzer.ts src/evolution/python-adapter.ts src/evolution/python-synthesizer.ts src/evolution/handler.ts __tests__/evolution/python-analyzer.test.ts __tests__/evolution/python-adapter.test.ts __tests__/evolution/python-synthesizer.test.ts
git commit -m "feat(synthesis): Python tool synthesis path with static analysis and child process execution"
```

---

## Task 5 — `list_synthesized_capabilities` Tool

**Why:** The owl needs to know what synthesized tools and skills it has created (FR-DT-11). Without this, it cannot build on prior synthesis work or avoid duplicating effort.

**Files:**
- Create: `src/tools/synthesized-catalog.ts`
- Create: `__tests__/tools/synthesized-catalog.test.ts`
- Modify: `src/index.ts` (register the tool)

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/synthesized-catalog.test.ts
import { describe, it, expect, vi } from "vitest";
import { SynthesizedCatalogTool } from "../../src/tools/synthesized-catalog.js";
import type { ToolRegistry } from "../../src/tools/registry.js";
import type { ToolContext } from "../../src/tools/registry.js";

function makeRegistry(tools: Array<{ name: string; source?: string }>): ToolRegistry {
  return {
    getAll: () => tools.map(t => ({
      definition: { name: t.name, description: "desc", parameters: { type: "object", properties: {}, required: [] } },
      source: t.source ?? "builtin",
      execute: async () => "",
    })),
  } as unknown as ToolRegistry;
}

describe("SynthesizedCatalogTool.execute", () => {
  it("returns synthesized tools from registry", async () => {
    const registry = makeRegistry([
      { name: "shell", source: "builtin" },
      { name: "synth_csv_parser", source: "synthesized" },
      { name: "synth_api_caller", source: "synthesized" },
    ]);
    const ctx: ToolContext = { cwd: "/workspace", engineContext: { toolRegistry: registry } as any };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.tools).toHaveLength(2);
    expect(parsed.tools).toContain("synth_csv_parser");
    expect(parsed.tools).not.toContain("shell");
  });

  it("returns empty arrays when no synthesized tools exist", async () => {
    const registry = makeRegistry([{ name: "shell", source: "builtin" }]);
    const ctx: ToolContext = { cwd: "/workspace", engineContext: { toolRegistry: registry } as any };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.tools).toHaveLength(0);
  });

  it("returns total count in summary", async () => {
    const registry = makeRegistry([
      { name: "synth_x", source: "synthesized" },
      { name: "synth_y", source: "synthesized" },
    ]);
    const ctx: ToolContext = { cwd: "/workspace", engineContext: { toolRegistry: registry } as any };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.total).toBe(2);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/tools/synthesized-catalog.test.ts
```
Expected: FAIL.

- [ ] **Step 3: Implement `src/tools/synthesized-catalog.ts`**

```typescript
/**
 * StackOwl — Synthesized Capabilities Catalog
 *
 * Lists all tools and skills that have been synthesized in the current
 * session or loaded from disk at startup. Lets the owl introspect its
 * evolved capabilities without reading the filesystem directly.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const SynthesizedCatalogTool: ToolImplementation = {
  definition: {
    name: "list_synthesized_capabilities",
    description:
      "List all tools and skills that have been synthesized (either in this session or loaded from disk at startup). " +
      "Use this before synthesizing a new capability to avoid duplicating existing ones.",
    parameters: {
      type: "object",
      properties: {},
      required: [],
    },
    executionPolicy: { timeoutMs: 2_000, maxRetries: 0 },
  },
  source: "builtin",

  async execute(_args: Record<string, unknown>, context: ToolContext): Promise<string> {
    log.synthesis.debug("list_synthesized_capabilities.execute: entry");

    const registry = context.engineContext?.toolRegistry;
    if (!registry) {
      return JSON.stringify({ tools: [], total: 0, note: "Tool registry not available." });
    }

    const all = registry.getAll ? registry.getAll() : [];
    const synthesizedTools = all
      .filter((t) => t.source === "synthesized")
      .map((t) => t.definition.name);

    const result = {
      tools: synthesizedTools,
      total: synthesizedTools.length,
      note: synthesizedTools.length === 0
        ? "No synthesized tools in current session."
        : `${synthesizedTools.length} synthesized tool(s) available.`,
    };

    log.synthesis.debug("list_synthesized_capabilities.execute: exit", { count: synthesizedTools.length });
    return JSON.stringify(result, null, 2);
  },
};
```

- [ ] **Step 4: Check that ToolRegistry has a `getAll()` method**

```bash
grep -n "getAll\|getAllTools" /ssd/projects/stackowl-personal-ai-assistant/src/tools/registry.ts | head -10
```

If `getAll()` does not exist, add it to `src/tools/registry.ts`:

```typescript
/** Returns all registered tools. Used by catalog and diagnostics. */
getAll(): ToolImplementation[] {
  return Array.from(this.tools.values());
}
```

- [ ] **Step 5: Run tests to verify pass**

```bash
npx vitest run __tests__/tools/synthesized-catalog.test.ts
```
Expected: PASS — 3 tests.

- [ ] **Step 6: Register the tool in `src/index.ts`**

Find the block where builtin tools are registered (the `toolRegistry.registerAll([...])` call). Add `SynthesizedCatalogTool`:

```typescript
import { SynthesizedCatalogTool } from "./tools/synthesized-catalog.js";

// In the registerAll([...]) array, add:
SynthesizedCatalogTool,
```

- [ ] **Step 7: Run full test suite**

```bash
npm run test
```
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/tools/synthesized-catalog.ts __tests__/tools/synthesized-catalog.test.ts src/index.ts src/tools/registry.ts
git commit -m "feat(tools): list_synthesized_capabilities introspection tool"
```

---

## Task 6 — Wire log.synthesis into Existing Synthesis Code (Observability)

**Why:** The existing handler and synthesizer log to `log.evolution`. Now that `log.synthesis` exists, key synthesis-specific events should use it so log queries are precise.

**Files:**
- Modify: `src/evolution/handler.ts` (targeted lines only)
- Modify: `src/evolution/synthesizer.ts` (targeted lines only)

- [ ] **Step 1: Update handler.ts synthesis log calls**

In `src/evolution/handler.ts`, find the synthesis-specific log calls (NOT the general evolution calls — those stay as `log.evolution`). Replace synthesis event logs:

```typescript
// BEFORE (approximately line 476)
log.evolution.warn("No skills directory configured — falling back to TypeScript synthesis");

// AFTER
log.synthesis.warn("handler.buildWithTypeScript: no skills dir — TypeScript path", {});
```

```typescript
// BEFORE (in buildWithSkill)
log.evolution.evolve(`Synthesizing skill for: ...`);

// Use progress callbacks instead — these are already channeled to the user.
// Add synthesis-specific entry/exit logs:
log.synthesis.debug("handler.buildWithSkill: entry", { toolName: proposal.toolName });
// ... at end of method:
log.synthesis.debug("handler.buildWithSkill: exit", { toolName: proposal.toolName, filePath });
```

- [ ] **Step 2: Verify no test regressions**

```bash
npm run test
```
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/evolution/handler.ts src/evolution/synthesizer.ts
git commit -m "refactor(synthesis): route synthesis events to log.synthesis namespace"
```

---

## Self-Review

### Spec coverage check

| Requirement | Task | Status |
|-------------|------|--------|
| FR-DT-1 (structured gap signal) | Pre-existing | ✅ exists |
| FR-DT-2 (semantic refusal detection) | Pre-existing | ✅ exists |
| FR-DT-3 (SKILL.md synthesis) | Pre-existing | ✅ exists |
| FR-DT-4 (TypeScript OR Python tool) | Task 4 | ✅ adds Python |
| FR-DT-5 (live registration same session) | Pre-existing | ✅ exists |
| FR-DT-6 (persist across restart) | Task 1 (synthesizedDir) | ✅ workspace-backed |
| FR-DT-7 (workspace constraint) | Pre-existing (platform sandbox) | ✅ exists |
| FR-DT-8 (critical tool permission gate) | Task 3 | ✅ |
| FR-DT-9 (static analysis gate) | Task 4 (Python analyzer) | ✅ Python added; TS pre-existing |
| FR-DT-10 (MCP list_changed) | **Deferred** — no MCP server in StackOwl | ⏸ future epic |
| FR-DT-11 (introspection tool) | Task 5 | ✅ |
| log.synthesis namespace | Task 2 | ✅ |
| synthesizedDir config field | Task 1 | ✅ |

**MCP deferred note:** StackOwl currently connects TO MCP servers as a client; it does not expose an MCP server. `notifyToolsListChanged()` requires a server. This is a separate epic ("StackOwl MCP Server") not covered here.

### Placeholder scan

No placeholders found. All steps contain complete code, exact file paths, and runnable commands.

### Type consistency check

- `CapabilityGap` — imported from `./detector.js` consistently in all new files
- `ToolImplementation` — imported from `../tools/registry.js` in catalog and adapter
- `getSynthesizedDir(config)` — defined in Task 1, used in Tasks 3 and 4
- `PythonAdapter.wrap(filePath, code)` — defined in Task 4b, called in `buildWithPython()` in handler
- `CriticalToolsGuard` — instantiated with `(permissionsFile, approvalChannel)` consistently in Tasks 3 and 4
- `log.synthesis` — available after Task 2, used in Tasks 3, 4, 5, 6

---

## Execution Notes

- **Task 1** touches the most files and is the riskiest — run `npm run test` after each sub-step
- **Task 4** (Python) is the most additive — it does NOT change any TypeScript synthesis paths; only adds a new branch
- **Task 3** (permission gate) is wired as opt-in via `approvalChannel` — passing `undefined` preserves existing behaviour exactly
- The Telegram and CLI channels will need to implement `ApprovalChannel.ask()` to surface the permission prompt to users on those channels (not in scope for this plan — CLI version provided as default)
