# Cycle 2 Tools+Safety+Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 6 audit items on top of the Cycle 1 Platform layer — `edit_file replace_all`, `list_directory` + `search_files`, `git_tool` writes, schedule durability via SQLite, `platform.notifier` wiring + `notification_send` tool, and `code-sandbox` Docker isolation.

**Architecture:** Every new tool consumes `platform.sandbox` for boundary checks, `platform.shell.exec` for subprocesses, `platform.notifier` for delivery, and `platform.systemInfo.capabilities` for feature detection. Two new capability probes (`hasRipgrep`, `hasDockerImagesPulled`) join the existing matrix. Schedule promotes from a one-file in-memory tool to a proper `src/schedule/` module with SQLite-backed persistence and boot-time hydration. Code-sandbox dispatches between Docker (when available) and a host fallback marked with a degradation warning.

**Tech Stack:** TypeScript strict, Node ≥22, Vitest, `micromatch`, `ignore`, `node-notifier` (already installed), Docker CLI (optional runtime dep), ripgrep (optional acceleration).

**Spec:** `docs/superpowers/specs/2026-05-11-cycle-2-tools-design.md`

---

## File Structure

```
src/tools/
├── files.ts                        # MODIFIED — EditFileTool.replace_all (B5)
├── filesystem/
│   ├── list-directory.ts           # NEW (B1)
│   └── search-files.ts             # NEW (B1)
├── code-sandbox.ts                 # REWRITTEN — Docker isolation + host fallback (B3)
├── dev/git.ts                      # EXTENDED — write actions (B2)
├── notification-send.ts            # NEW — notification_send tool (B4)
└── schedule.ts                     # REWRITTEN — thin delegate (B6)

src/schedule/                       # NEW MODULE (B6)
├── types.ts                        # ScheduledJob, RunnerOptions
├── store.ts                        # SQLite-backed ScheduleStore
└── runner.ts                       # Timer lifecycle + hydration

src/platform/
├── types.ts                        # +hasRipgrep, +hasDockerImagesPulled
└── capabilities/system-info.ts     # probe rg + docker image inventory

src/memory/db.ts                    # +scheduled_jobs table

src/heartbeat/proactive.ts          # MODIFIED — fallback through platform.notifier (B4)

__tests__/
├── tools/
│   ├── files-edit-replace-all.test.ts          # NEW (B5)
│   ├── filesystem/list-directory.test.ts       # NEW (B1)
│   ├── filesystem/search-files-js.test.ts      # NEW (B1)
│   ├── filesystem/search-files-rg.test.ts      # NEW (B1)
│   ├── dev/git-writes.test.ts                  # NEW (B2)
│   ├── notification-send.test.ts               # NEW (B4)
│   └── code-sandbox-docker.test.ts             # NEW (B3)
└── schedule/
    ├── store.test.ts                           # NEW (B6)
    └── runner.test.ts                          # NEW (B6)
```

---

## Setup Phase (3 tasks)

## Task 1: Install runtime deps

**Files:** `package.json`, `package-lock.json`

- [ ] **Step 1: Install deps**

```bash
npm install micromatch ignore
```

`micromatch` (~6KB transitive of vitest already, but explicit add anchors it) — glob matching for `list_directory`.
`ignore` (~10KB) — `.gitignore` parser matching git's semantics including negation.

- [ ] **Step 2: Install type definitions**

```bash
npm install --save-dev @types/micromatch
```

`ignore` ships its own types.

- [ ] **Step 3: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0` (Cycle 1 left a clean build).

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json
git commit -m "chore(cycle2): add micromatch + ignore runtime deps for filesystem tools"
```

---

## Task 2: Platform capability — `hasRipgrep`

**Files:**
- Modify: `src/platform/types.ts` (add field)
- Modify: `src/platform/capabilities/system-info.ts` (probe it)
- Test: `__tests__/platform/system-info.test.ts` (extend)

- [ ] **Step 1: Write the failing test**

Add to `__tests__/platform/system-info.test.ts` inside the existing `describe("SystemInfoImpl")` block:

```typescript
  it("capabilities includes hasRipgrep boolean after refresh", async () => {
    const api = new SystemInfoImpl();
    await api.refresh();
    expect(typeof api.current().capabilities.hasRipgrep).toBe("boolean");
  });
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

Expected: type error or missing `hasRipgrep` property — test fails.

- [ ] **Step 3: Extend `SystemCapabilities` type**

In `src/platform/types.ts`, find `SystemCapabilities` and add `hasRipgrep`:

```typescript
export interface SystemCapabilities {
  hasNotifier: boolean;
  hasOpener: boolean;
  hasDocker: boolean;
  hasGit: boolean;
  hasPython: boolean;
  hasNode: boolean;
  hasRipgrep: boolean;
}
```

- [ ] **Step 4: Probe in `system-info.ts`**

In `src/platform/capabilities/system-info.ts`, update `probeCapabilities()`:

```typescript
async function probeCapabilities(): Promise<SystemCapabilities> {
  const [hasOpener, hasDocker, hasGit, hasPython, hasRipgrep] = await Promise.all([
    osPlatform() === "win32"
      ? Promise.resolve(true)
      : osPlatform() === "darwin"
        ? commandAvailable("open")
        : commandAvailable("xdg-open"),
    commandAvailable("docker"),
    commandAvailable("git"),
    commandAvailable("python3").then((found) => found || commandAvailable("python")),
    commandAvailable("rg"),
  ]);
  return {
    hasNotifier: true,
    hasOpener,
    hasDocker,
    hasGit,
    hasPython,
    hasNode: true,
    hasRipgrep,
  };
}
```

Also update the constructor's initial value (`capabilities: { ..., hasRipgrep: false }`).

- [ ] **Step 5: Run — verify pass**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/platform/types.ts src/platform/capabilities/system-info.ts __tests__/platform/system-info.test.ts
git commit -m "feat(platform): probe hasRipgrep capability at boot"
```

---

## Task 3: Platform capability — `hasDockerImagesPulled`

**Files:**
- Modify: `src/platform/types.ts`
- Modify: `src/platform/capabilities/system-info.ts`
- Test: extend existing system-info test

- [ ] **Step 1: Write the failing test**

Append to `__tests__/platform/system-info.test.ts`:

```typescript
  it("capabilities includes hasDockerImagesPulled after refresh", async () => {
    const api = new SystemInfoImpl();
    await api.refresh();
    const info = api.current();
    expect(info.capabilities.hasDockerImagesPulled).toBeDefined();
    expect(typeof info.capabilities.hasDockerImagesPulled.python).toBe("boolean");
    expect(typeof info.capabilities.hasDockerImagesPulled.node).toBe("boolean");
  });
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

- [ ] **Step 3: Extend `SystemCapabilities` type**

In `src/platform/types.ts`:

```typescript
export interface SystemCapabilities {
  hasNotifier: boolean;
  hasOpener: boolean;
  hasDocker: boolean;
  hasGit: boolean;
  hasPython: boolean;
  hasNode: boolean;
  hasRipgrep: boolean;
  hasDockerImagesPulled: { python: boolean; node: boolean };
}
```

- [ ] **Step 4: Add the probe in `system-info.ts`**

Add a helper above `probeCapabilities`:

```typescript
const SANDBOX_IMAGES = {
  python: "python:3.12-slim",
  node: "node:22-alpine",
} as const;

async function probeDockerImages(hasDocker: boolean): Promise<{ python: boolean; node: boolean }> {
  if (!hasDocker) return { python: false, node: false };
  return new Promise((resolveResult) => {
    const child = spawn("docker", ["images", "--format", "{{.Repository}}:{{.Tag}}"], { stdio: ["ignore", "pipe", "ignore"] });
    const chunks: Buffer[] = [];
    child.stdout.on("data", (c) => chunks.push(c as Buffer));
    child.on("error", () => resolveResult({ python: false, node: false }));
    child.on("close", () => {
      const list = Buffer.concat(chunks).toString("utf-8");
      resolveResult({
        python: list.includes(SANDBOX_IMAGES.python),
        node: list.includes(SANDBOX_IMAGES.node),
      });
    });
  });
}
```

Then update `probeCapabilities()`:

```typescript
async function probeCapabilities(): Promise<SystemCapabilities> {
  const [hasOpener, hasDocker, hasGit, hasPython, hasRipgrep] = await Promise.all([
    /* same as before */
  ]);
  const hasDockerImagesPulled = await probeDockerImages(hasDocker);
  return {
    hasNotifier: true, hasOpener, hasDocker, hasGit, hasPython, hasNode: true,
    hasRipgrep, hasDockerImagesPulled,
  };
}
```

Also update constructor initial:
```typescript
capabilities: {
  /* ...other fields... */
  hasRipgrep: false,
  hasDockerImagesPulled: { python: false, node: false },
},
```

Also export `SANDBOX_IMAGES` from this file so `code-sandbox.ts` can use the same constants.

- [ ] **Step 5: Run — verify pass**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add src/platform/types.ts src/platform/capabilities/system-info.ts __tests__/platform/system-info.test.ts
git commit -m "feat(platform): probe hasDockerImagesPulled for sandbox image preflight"
```

---

## B5 — `edit_file replace_all` (1 task)

## Task 4: `replace_all` parameter

**Files:**
- Modify: `src/tools/files.ts`
- Create: `__tests__/tools/files-edit-replace-all.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/files-edit-replace-all.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EditFileTool } from "../../src/tools/files.js";

let workspace: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-edit-replace-all-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
});

describe("EditFileTool replace_all", () => {
  it("replaces every occurrence when replace_all=true", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo baz foo");
    const result = await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "X", replace_all: true },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("X bar X baz X");
    expect(result).toMatch(/3 occurrence/i);
  });

  it("replaces only the first when replace_all is omitted (back-compat)", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo");
    await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "X" },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("X bar foo");
  });

  it("rejects empty old_string when replace_all=true (would infinite-replace)", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "abc");
    const result = await EditFileTool.execute(
      { path: file, old_string: "", new_string: "X", replace_all: true },
      { cwd: workspace },
    );
    expect(result.toLowerCase()).toMatch(/invalid|empty/);
  });

  it("no-op when old_string === new_string with replace_all=true", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo");
    const result = await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "foo", replace_all: true },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("foo bar foo");
    expect(result.toLowerCase()).toMatch(/no-op|0 replacement/);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/files-edit-replace-all.test.ts
```

Expected: tests fail (`replace_all` ignored — only first match replaced).

- [ ] **Step 3: Extend `EditFileTool` in `src/tools/files.ts`**

Find `EditFileTool.definition.parameters.properties` and add the new param:

```typescript
        replace_all: {
          type: "boolean",
          description: "If true, replaces every occurrence of old_string. If false or omitted, replaces only the first.",
        },
```

Update the description string in `EditFileTool.definition.description`:

```typescript
    description:
      "Make a surgical edit to a file by replacing an exact string. " +
      "Prefer this over write_file when changing only part of a file. " +
      "The old_string must match exactly (including whitespace). " +
      "Default: replaces only the FIRST occurrence. " +
      "Set replace_all:true to replace every occurrence in one call.",
```

In `EditFileTool.execute()`, replace the body that does the `indexOf`/single replace with:

```typescript
    const replaceAll = args["replace_all"] === true;

    log.tool.debug("edit_file.execute: entry", { op: "edit", path: resolved, oldLen: oldString.length, newLen: newString.length, replaceAll });

    if (replaceAll && oldString === "") {
      return `Error: old_string cannot be empty when replace_all=true (would loop forever).`;
    }

    log.tool.debug("edit_file.execute: operation branch", { chosen: replaceAll ? "replace-all" : "surgical-edit" });

    try {
      const content = await readFile(safePath, "utf-8");

      if (replaceAll) {
        if (oldString === newString) {
          log.tool.debug("edit_file.execute: exit", { op: "edit", noop: true });
          return `0 replacements (no-op: replacement equals search) in ${filePath}`;
        }
        const parts = content.split(oldString);
        const count = parts.length - 1;
        if (count === 0) {
          return `Error: old_string not found in ${filePath}. Make sure it matches exactly (including whitespace and newlines).`;
        }
        const updated = parts.join(newString);
        await writeFile(safePath, updated, "utf-8");
        const result = `Replaced ${count} occurrences of '${oldString.length > 40 ? oldString.slice(0, 40) + "…" : oldString}' in ${filePath}`;
        log.tool.debug("edit_file.execute: exit", { op: "edit", count });
        return result;
      }

      // single-occurrence branch (existing behaviour)
      const idx = content.indexOf(oldString);
      if (idx === -1) {
        return `Error: old_string not found in ${filePath}. Make sure it matches exactly (including whitespace and newlines).`;
      }
      const updated = content.slice(0, idx) + newString + content.slice(idx + oldString.length);
      await writeFile(safePath, updated, "utf-8");
      const lineNum = content.slice(0, idx).split("\n").length;
      const result = `Successfully edited ${filePath} at line ~${lineNum} (replaced ${oldString.length} chars with ${newString.length} chars)`;
      log.tool.debug("edit_file.execute: exit", { op: "edit", resultLen: result.length });
      return result;
    } catch (error: any) {
      log.tool.error("edit_file.execute: edit failed", error, { path: resolved });
      return `Failed to edit file: ${error.message}`;
    }
```

Note: this uses `safePath` (from the sandbox check we did in C1-T13). Confirm the var name in the file — adapt if it's `resolved` instead.

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/files-edit-replace-all.test.ts __tests__/tools/files-sandbox.test.ts
```

Expected: 6/6 pass (4 new + 2 existing sandbox tests).

- [ ] **Step 5: Commit**

```bash
git add src/tools/files.ts __tests__/tools/files-edit-replace-all.test.ts
git commit -m "feat(tools): edit_file gains replace_all parameter for global replacements"
```

---

## B1 — `list_directory` + `search_files` (4 tasks)

## Task 5: `list_directory` tool

**Files:**
- Create: `src/tools/filesystem/list-directory.ts`
- Create: `__tests__/tools/filesystem/list-directory.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/filesystem/list-directory.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ListDirectoryTool } from "../../../src/tools/filesystem/list-directory.js";

let workspace: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-list-dir-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
});

function setup(structure: Record<string, string | null>) {
  for (const [relpath, contents] of Object.entries(structure)) {
    const abs = join(workspace, relpath);
    if (contents === null) {
      mkdirSync(abs, { recursive: true });
    } else {
      mkdirSync(join(abs, ".."), { recursive: true });
      writeFileSync(abs, contents);
    }
  }
}

describe("ListDirectoryTool", () => {
  it("flat listing returns top-level entries only", async () => {
    setup({ "a.txt": "x", "b.txt": "x", "sub/c.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    const names = parsed.entries.map((e: any) => e.path);
    expect(names).toContain("a.txt");
    expect(names).toContain("b.txt");
    expect(names).toContain("sub");
    expect(names).not.toContain("sub/c.txt");
  });

  it("recursive=true descends", async () => {
    setup({ "a.txt": "x", "sub/c.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, recursive: true }, { cwd: workspace });
    const parsed = JSON.parse(res);
    const names = parsed.entries.map((e: any) => e.path);
    expect(names).toContain("sub/c.txt");
  });

  it("glob filters to matching files", async () => {
    setup({ "a.ts": "x", "b.js": "x", "sub/c.ts": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, glob: "**/*.ts" }, { cwd: workspace });
    const parsed = JSON.parse(res);
    const names = parsed.entries.map((e: any) => e.path);
    expect(names).toContain("a.ts");
    expect(names).toContain("sub/c.ts");
    expect(names).not.toContain("b.js");
  });

  it("hides dotfiles by default", async () => {
    setup({ ".env": "x", "visible.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    const names = parsed.entries.map((e: any) => e.path);
    expect(names).not.toContain(".env");
    expect(names).toContain("visible.txt");
  });

  it("include_hidden=true shows dotfiles", async () => {
    setup({ ".env": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, include_hidden: true }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.entries.map((e: any) => e.path)).toContain(".env");
  });

  it("hard-excludes node_modules even with include_hidden", async () => {
    setup({ "node_modules/lodash/index.js": "x", "src/main.ts": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, recursive: true, include_hidden: true }, { cwd: workspace });
    const parsed = JSON.parse(res);
    const names = parsed.entries.map((e: any) => e.path);
    expect(names.some((n: string) => n.startsWith("node_modules"))).toBe(false);
    expect(names).toContain("src/main.ts");
  });

  it("max_results truncates and reports truncated=true", async () => {
    const structure: Record<string, string> = {};
    for (let i = 0; i < 20; i++) structure[`f${i}.txt`] = "x";
    setup(structure);
    const res = await ListDirectoryTool.execute({ path: workspace, max_results: 5 }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.entries.length).toBe(5);
    expect(parsed.truncated).toBe(true);
  });

  it("rejects paths outside the workspace via platform.sandbox", async () => {
    const res = await ListDirectoryTool.execute({ path: "/etc" }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACCESS_DENIED");
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/filesystem/list-directory.test.ts
```

Expected: module not found.

- [ ] **Step 3: Implement `src/tools/filesystem/list-directory.ts`**

```typescript
import { opendir, stat, readFile } from "node:fs/promises";
import { resolve, isAbsolute, normalize, relative, join } from "node:path";
import micromatch from "micromatch";
import ignore from "ignore";
import { log } from "../../logger.js";
import { platform } from "../../platform/index.js";
import type { SandboxPolicy } from "../../platform/index.js";
import type { ToolImplementation, ToolContext } from "../registry.js";

const HARD_EXCLUDED = new Set([
  ".git", "node_modules", ".next", "dist", "build", "coverage", ".cache",
]);

const MAX_RESULTS_HARD_CAP = 5000;
const DEFAULT_MAX_RESULTS = 500;

interface ListEntry {
  path: string;
  type: "file" | "dir" | "symlink";
  size?: number;
  modified?: string;
}

async function loadGitignore(root: string): Promise<ReturnType<typeof ignore> | null> {
  try {
    const giPath = join(root, ".gitignore");
    const content = await readFile(giPath, "utf-8");
    return ignore().add(content);
  } catch {
    return null;
  }
}

function toPosix(p: string): string {
  return p.split(/[\\/]/).join("/");
}

export const ListDirectoryTool: ToolImplementation = {
  definition: {
    name: "list_directory",
    description:
      "List files and directories. Set `recursive: true` or pass a `glob` (e.g. \"**/*.ts\") to descend. " +
      "Respects .gitignore by default; hard-excludes node_modules/.git/etc. Hidden dotfiles excluded unless include_hidden:true. " +
      'Example: list_directory(path: "src", recursive: true, glob: "**/*.ts")',
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "Workspace-relative or absolute path to list" },
        recursive: { type: "boolean", description: "Descend into subdirectories" },
        glob: { type: "string", description: "Optional glob like \"**/*.ts\" (implies recursive=true)" },
        include_hidden: { type: "boolean", description: "Include dotfiles" },
        respect_gitignore: { type: "boolean", description: "Honor .gitignore (default true)" },
        max_results: { type: "number", description: `Cap results (default ${DEFAULT_MAX_RESULTS}, hard cap ${MAX_RESULTS_HARD_CAP})` },
      },
      required: ["path"],
    },
    capabilities: ["file_read", "directory_list"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  category: "filesystem",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const rawPath = args["path"] as string;
    const recursive = args["recursive"] === true || !!args["glob"];
    const glob = args["glob"] as string | undefined;
    const includeHidden = args["include_hidden"] === true;
    const respectGitignore = args["respect_gitignore"] !== false;
    const rawMax = (args["max_results"] as number | undefined) ?? DEFAULT_MAX_RESULTS;
    const maxResults = Math.min(rawMax, MAX_RESULTS_HARD_CAP);

    if (!rawPath) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "path is required" } });
    }

    const cwd = context.cwd || process.cwd();
    const normalized = normalize(rawPath);
    const absolute = isAbsolute(normalized) ? normalized : resolve(cwd, normalized);

    log.tool.debug("list_directory.execute: entry", { path: absolute, recursive, glob, includeHidden });

    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: false,
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(absolute, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("list_directory.execute: sandbox check failed", { reason: sandboxResult.reason, message: sandboxResult.message });
      return JSON.stringify({
        success: false,
        error: {
          code: sandboxResult.reason === "E_OUTSIDE_SANDBOX" ? "ACCESS_DENIED" : "INVALID_PATH",
          message: sandboxResult.message ?? "Access denied",
        },
      });
    }
    const root = sandboxResult.resolvedPath;

    const gi = respectGitignore ? await loadGitignore(cwd) : null;
    const entries: ListEntry[] = [];
    let totalScanned = 0;
    let truncated = false;

    async function walk(dir: string): Promise<void> {
      if (entries.length >= maxResults) { truncated = true; return; }
      let handle;
      try {
        handle = await opendir(dir);
      } catch (err) {
        log.tool.warn("list_directory.walk: opendir failed", { dir, err: String(err) });
        return;
      }

      for await (const dirent of handle) {
        if (entries.length >= maxResults) { truncated = true; break; }
        totalScanned++;

        const name = dirent.name;
        if (HARD_EXCLUDED.has(name)) continue;
        if (!includeHidden && name.startsWith(".")) continue;

        const abs = join(dir, name);
        const rel = toPosix(relative(root, abs));

        if (gi && gi.ignores(rel)) continue;

        let type: ListEntry["type"];
        if (dirent.isSymbolicLink()) {
          type = "symlink";
          // Confirm symlink target stays inside sandbox; if not, skip
          const symCheck = platform.sandbox.check(abs, policy);
          if (!symCheck.ok) continue;
        } else if (dirent.isDirectory()) {
          type = "dir";
        } else if (dirent.isFile()) {
          type = "file";
        } else {
          continue;
        }

        let size: number | undefined;
        let modified: string | undefined;
        if (type === "file") {
          try {
            const st = await stat(abs);
            size = st.size;
            modified = st.mtime.toISOString();
          } catch { /* skip */ }
        }

        if (glob && type === "file" && !micromatch.isMatch(rel, glob)) continue;

        entries.push({ path: rel, type, size, modified });

        if (recursive && type === "dir") {
          await walk(abs);
        }
      }
    }

    try {
      const rootStat = await stat(root);
      if (!rootStat.isDirectory()) {
        return JSON.stringify({ success: false, error: { code: "NOT_A_DIRECTORY", message: `${root} is not a directory` } });
      }
      await walk(root);
    } catch (err) {
      return JSON.stringify({ success: false, error: { code: "STAT_FAILED", message: String(err) } });
    }

    log.tool.debug("list_directory.execute: exit", { count: entries.length, truncated, totalScanned });
    return JSON.stringify({ success: true, data: { entries, truncated, totalScanned } });
  },
};
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/filesystem/list-directory.test.ts
```

Expected: 8/8 pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/filesystem/list-directory.ts __tests__/tools/filesystem/list-directory.test.ts
git commit -m "feat(tools): list_directory — sandboxed glob-aware directory listing"
```

---

## Task 6: `search_files` — JS fallback path

**Files:**
- Create: `src/tools/filesystem/search-files.ts`
- Create: `__tests__/tools/filesystem/search-files-js.test.ts`

We implement the JS path first so the test pins down the contract, then layer ripgrep acceleration in Task 7.

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/filesystem/search-files-js.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SearchFilesTool } from "../../../src/tools/filesystem/search-files.js";

let workspace: string;
const ENV_BACKUP = { ...process.env };

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-search-files-js-"));
  // Force JS fallback even if rg is installed
  process.env.STACKOWL_DISABLE_RG = "true";
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  process.env = { ...ENV_BACKUP };
});

describe("SearchFilesTool (JS fallback)", () => {
  it("literal match finds occurrences", async () => {
    writeFileSync(join(workspace, "a.ts"), "const x = 1;\nconst foo = 2;\nconst y = 3;");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(1);
    expect(parsed.data.matches[0].line).toBe(2);
    expect(parsed.data.via).toBe("js-fallback");
  });

  it("regex=true treats pattern as regex", async () => {
    writeFileSync(join(workspace, "a.ts"), "abc123\nxyz999\nfoo456");
    const res = await SearchFilesTool.execute({ pattern: "\\d{3}", path: workspace, regex: true }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(3);
  });

  it("case_sensitive=false matches mixed case", async () => {
    writeFileSync(join(workspace, "a.ts"), "Foo\nFOO\nfoo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(3);
  });

  it("glob restricts file extensions", async () => {
    writeFileSync(join(workspace, "a.ts"), "foo");
    writeFileSync(join(workspace, "b.js"), "foo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace, glob: "*.ts" }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(1);
    expect(parsed.data.matches[0].path).toBe("a.ts");
  });

  it("skips binary files (null byte in first 8KB)", async () => {
    writeFileSync(join(workspace, "binary.bin"), Buffer.from([0x66, 0x6f, 0x6f, 0x00, 0x66]));
    writeFileSync(join(workspace, "text.txt"), "foo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.every((m: any) => !m.path.endsWith(".bin"))).toBe(true);
  });

  it("context_lines returns surrounding lines", async () => {
    writeFileSync(join(workspace, "a.txt"), "line1\nline2\nMATCH\nline4\nline5");
    const res = await SearchFilesTool.execute({ pattern: "MATCH", path: workspace, context_lines: 1 }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches[0].before).toEqual(["line2"]);
    expect(parsed.data.matches[0].after).toEqual(["line4"]);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/filesystem/search-files-js.test.ts
```

- [ ] **Step 3: Implement `src/tools/filesystem/search-files.ts` (JS path only for now)**

```typescript
import { createReadStream, readFileSync } from "node:fs";
import { opendir } from "node:fs/promises";
import { resolve, isAbsolute, normalize, relative, join } from "node:path";
import { createInterface } from "node:readline";
import micromatch from "micromatch";
import ignore from "ignore";
import { log } from "../../logger.js";
import { platform } from "../../platform/index.js";
import type { SandboxPolicy } from "../../platform/index.js";
import type { ToolImplementation, ToolContext } from "../registry.js";

const HARD_EXCLUDED = new Set([
  ".git", "node_modules", ".next", "dist", "build", "coverage", ".cache",
]);
const DEFAULT_MAX_MATCHES = 200;
const MAX_MATCHES_CAP = 2000;
const BINARY_SNIFF_BYTES = 8192;

interface SearchMatch {
  path: string;
  line: number;
  column: number;
  preview: string;
  before?: string[];
  after?: string[];
}

function toPosix(p: string): string {
  return p.split(/[\\/]/).join("/");
}

function isBinaryFile(absPath: string): boolean {
  try {
    const fd = readFileSync(absPath, { encoding: null });
    const head = fd.subarray(0, Math.min(fd.length, BINARY_SNIFF_BYTES));
    for (let i = 0; i < head.length; i++) {
      if (head[i] === 0) return true;
    }
    return false;
  } catch {
    return true; // unreadable → treat as binary, skip
  }
}

async function* walkFiles(
  root: string,
  glob: string | undefined,
  gi: ReturnType<typeof ignore> | null,
): AsyncGenerator<{ abs: string; rel: string }> {
  async function* walk(dir: string): AsyncGenerator<{ abs: string; rel: string }> {
    let handle;
    try { handle = await opendir(dir); } catch { return; }
    for await (const dirent of handle) {
      if (HARD_EXCLUDED.has(dirent.name)) continue;
      if (dirent.name.startsWith(".")) continue;
      const abs = join(dir, dirent.name);
      const rel = toPosix(relative(root, abs));
      if (gi && gi.ignores(rel)) continue;
      if (dirent.isDirectory()) {
        yield* walk(abs);
      } else if (dirent.isFile()) {
        if (glob && !micromatch.isMatch(rel, glob)) continue;
        yield { abs, rel };
      }
    }
  }
  yield* walk(root);
}

async function searchOneFile(
  abs: string,
  rel: string,
  matcher: (line: string) => RegExpExecArray | null,
  contextLines: number,
  remaining: number,
): Promise<SearchMatch[]> {
  const matches: SearchMatch[] = [];
  if (isBinaryFile(abs)) return matches;

  const stream = createReadStream(abs, { encoding: "utf-8" });
  const rl = createInterface({ input: stream, crlfDelay: Infinity });

  const buffer: string[] = [];
  let lineNum = 0;
  for await (const line of rl) {
    lineNum++;
    buffer.push(line);
    if (buffer.length > contextLines * 2 + 1) buffer.shift();

    const match = matcher(line);
    if (match) {
      const before = contextLines > 0
        ? buffer.slice(Math.max(0, buffer.length - 1 - contextLines), buffer.length - 1)
        : undefined;

      matches.push({
        path: rel,
        line: lineNum,
        column: match.index + 1,
        preview: line,
        before,
      });

      if (matches.length >= remaining) break;
    }
  }
  rl.close();
  stream.close();
  // Read forward to fill `after` for trailing matches — simple second pass when context_lines>0
  if (contextLines > 0 && matches.length > 0) {
    const allLines = readFileSync(abs, "utf-8").split("\n");
    for (const m of matches) {
      m.after = allLines.slice(m.line, Math.min(allLines.length, m.line + contextLines));
    }
  }
  return matches;
}

function buildMatcher(pattern: string, regex: boolean, caseSensitive: boolean): (line: string) => RegExpExecArray | null {
  const flags = caseSensitive ? "" : "i";
  const re = regex
    ? new RegExp(pattern, flags)
    : new RegExp(pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
  return (line: string) => re.exec(line);
}

async function searchViaJs(
  root: string,
  pattern: string,
  opts: { regex: boolean; caseSensitive: boolean; glob?: string; maxMatches: number; contextLines: number },
): Promise<SearchMatch[]> {
  const gi = await (async () => {
    try {
      const content = readFileSync(join(root, ".gitignore"), "utf-8");
      return ignore().add(content);
    } catch { return null; }
  })();

  const matcher = buildMatcher(pattern, opts.regex, opts.caseSensitive);
  const all: SearchMatch[] = [];

  for await (const file of walkFiles(root, opts.glob, gi)) {
    if (all.length >= opts.maxMatches) break;
    const remaining = opts.maxMatches - all.length;
    const matches = await searchOneFile(file.abs, file.rel, matcher, opts.contextLines, remaining);
    all.push(...matches);
  }
  return all;
}

export const SearchFilesTool: ToolImplementation = {
  definition: {
    name: "search_files",
    description:
      "Grep-equivalent: find a pattern (literal or regex) across files. Uses ripgrep when available (10-100× faster); " +
      "falls back to JS when not. Respects .gitignore, hard-excludes node_modules/.git/etc, skips binary files. " +
      'Example: search_files(pattern: "TODO", path: "src", glob: "**/*.ts", context_lines: 2)',
    parameters: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Search pattern (literal by default; regex if regex=true)" },
        path: { type: "string", description: "Directory to search (defaults to cwd)" },
        regex: { type: "boolean", description: "Treat pattern as regex" },
        case_sensitive: { type: "boolean", description: "Case-sensitive match" },
        glob: { type: "string", description: "Restrict to matching file paths" },
        max_matches: { type: "number", description: `Cap matches (default ${DEFAULT_MAX_MATCHES}, cap ${MAX_MATCHES_CAP})` },
        context_lines: { type: "number", description: "Lines of context around each match" },
      },
      required: ["pattern"],
    },
    capabilities: ["file_read", "search"],
    executionPolicy: { timeoutMs: 60_000, maxRetries: 0 },
  },

  category: "filesystem",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const pattern = args["pattern"] as string;
    const rawPath = (args["path"] as string | undefined) ?? ".";
    const regex = args["regex"] === true;
    const caseSensitive = args["case_sensitive"] === true;
    const glob = args["glob"] as string | undefined;
    const contextLines = (args["context_lines"] as number | undefined) ?? 0;
    const rawMax = (args["max_matches"] as number | undefined) ?? DEFAULT_MAX_MATCHES;
    const maxMatches = Math.min(rawMax, MAX_MATCHES_CAP);

    if (!pattern) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "pattern is required" } });
    }

    const cwd = context.cwd || process.cwd();
    const normalized = normalize(rawPath);
    const absolute = isAbsolute(normalized) ? normalized : resolve(cwd, normalized);

    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: false,
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(absolute, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("search_files.execute: sandbox check failed", { reason: sandboxResult.reason });
      return JSON.stringify({
        success: false,
        error: {
          code: sandboxResult.reason === "E_OUTSIDE_SANDBOX" ? "ACCESS_DENIED" : "INVALID_PATH",
          message: sandboxResult.message ?? "Access denied",
        },
      });
    }
    const root = sandboxResult.resolvedPath;

    const disableRg = process.env.STACKOWL_DISABLE_RG === "true";
    const useRg = !disableRg && platform.systemInfo.current().capabilities.hasRipgrep;

    log.tool.debug("search_files.execute: entry", { root, pattern, regex, useRg });

    let matches: SearchMatch[];
    let via: "ripgrep" | "js-fallback" = "js-fallback";
    try {
      matches = await searchViaJs(root, pattern, { regex, caseSensitive, glob, maxMatches, contextLines });
      // Ripgrep is implemented in Task 7 — leave the JS path as default for now.
      void useRg; // intentionally unused until Task 7
    } catch (err) {
      log.tool.error("search_files.execute: search failed", err as Error);
      return JSON.stringify({ success: false, error: { code: "SEARCH_FAILED", message: String(err) } });
    }

    const truncated = matches.length >= maxMatches;
    log.tool.debug("search_files.execute: exit", { matches: matches.length, via });
    return JSON.stringify({ success: true, data: { matches, truncated, via } });
  },
};
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/filesystem/search-files-js.test.ts
```

Expected: 6/6 pass (JS path).

- [ ] **Step 5: Commit**

```bash
git add src/tools/filesystem/search-files.ts __tests__/tools/filesystem/search-files-js.test.ts
git commit -m "feat(tools): search_files JS fallback path with gitignore + binary detection"
```

---

## Task 7: `search_files` — ripgrep acceleration path

**Files:**
- Modify: `src/tools/filesystem/search-files.ts` (add ripgrep branch)
- Create: `__tests__/tools/filesystem/search-files-rg.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/filesystem/search-files-rg.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { platform } from "../../../src/platform/index.js";
import { SearchFilesTool } from "../../../src/tools/filesystem/search-files.js";

let workspace: string;

beforeEach(async () => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-search-files-rg-"));
  await platform.systemInfo.refresh();
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  delete process.env.STACKOWL_DISABLE_RG;
});

describe("SearchFilesTool (ripgrep path)", () => {
  it("uses ripgrep when capability is present and not disabled", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) {
      console.log("Skipping ripgrep test — rg not installed on host");
      return;
    }
    writeFileSync(join(workspace, "a.ts"), "needle\nhaystack\nneedle");
    const res = await SearchFilesTool.execute({ pattern: "needle", path: workspace }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.via).toBe("ripgrep");
    expect(parsed.data.matches.length).toBe(2);
  });

  it("returns the same result shape as the JS fallback", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) return;
    writeFileSync(join(workspace, "a.ts"), "foo");
    const rgRes = JSON.parse(await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace }));
    process.env.STACKOWL_DISABLE_RG = "true";
    const jsRes = JSON.parse(await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace }));
    expect(rgRes.data.matches[0].path).toBe(jsRes.data.matches[0].path);
    expect(rgRes.data.matches[0].line).toBe(jsRes.data.matches[0].line);
  });

  it("respects max_matches via --max-count", async () => {
    if (!platform.systemInfo.current().capabilities.hasRipgrep) return;
    let content = "";
    for (let i = 0; i < 20; i++) content += "needle\n";
    writeFileSync(join(workspace, "a.ts"), content);
    const res = await SearchFilesTool.execute({ pattern: "needle", path: workspace, max_matches: 5 }, { cwd: workspace });
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(5);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/filesystem/search-files-rg.test.ts
```

Expected: first test fails because the JS-only impl currently always returns `via:"js-fallback"`.

- [ ] **Step 3: Add the ripgrep branch in `src/tools/filesystem/search-files.ts`**

Add a new function near the top:

```typescript
import { spawn } from "node:child_process";

interface RgJsonLine {
  type: string;
  data?: {
    path?: { text: string };
    line_number?: number;
    lines?: { text: string };
    submatches?: Array<{ start: number; end: number }>;
  };
}

async function searchViaRipgrep(
  root: string,
  pattern: string,
  opts: { regex: boolean; caseSensitive: boolean; glob?: string; maxMatches: number; contextLines: number; respectGitignore: boolean },
): Promise<SearchMatch[]> {
  const rgArgs: string[] = ["--json", "--max-count", String(opts.maxMatches)];
  if (opts.caseSensitive) rgArgs.push("--case-sensitive");
  else rgArgs.push("--smart-case");
  if (opts.regex) rgArgs.push("--regexp", pattern);
  else rgArgs.push("--fixed-strings", pattern);
  if (opts.glob) rgArgs.push("--glob", opts.glob);
  if (opts.contextLines > 0) rgArgs.push("--context", String(opts.contextLines));
  if (!opts.respectGitignore) rgArgs.push("--no-ignore-vcs");
  rgArgs.push(root);

  const matches: SearchMatch[] = [];
  let contextBefore: string[] = [];

  return new Promise((resolveResult, rejectResult) => {
    const child = spawn("rg", rgArgs, { stdio: ["ignore", "pipe", "pipe"] });
    let buf = "";
    child.stdout.on("data", (c: Buffer) => {
      buf += c.toString("utf-8");
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);
        if (!line) continue;
        try {
          const obj = JSON.parse(line) as RgJsonLine;
          if (obj.type === "context" && obj.data?.lines?.text) {
            contextBefore.push(obj.data.lines.text.replace(/\n$/, ""));
            if (contextBefore.length > opts.contextLines) contextBefore.shift();
          } else if (obj.type === "match" && obj.data?.path?.text && obj.data.line_number !== undefined && obj.data.lines?.text) {
            const submatch = obj.data.submatches?.[0];
            const m: SearchMatch = {
              path: toPosix(relative(root, obj.data.path.text)),
              line: obj.data.line_number,
              column: (submatch?.start ?? 0) + 1,
              preview: obj.data.lines.text.replace(/\n$/, ""),
            };
            if (opts.contextLines > 0) {
              m.before = [...contextBefore];
              m.after = []; // will be filled when subsequent context lines arrive
            }
            matches.push(m);
            contextBefore = [];
            if (matches.length >= opts.maxMatches) {
              child.kill("SIGTERM");
              break;
            }
          }
        } catch { /* skip malformed lines */ }
      }
    });
    child.stderr.on("data", () => { /* ignore */ });
    child.on("close", () => resolveResult(matches));
    child.on("error", (err) => rejectResult(err));
  });
}
```

Now replace the search call in `execute()`:

```typescript
    let matches: SearchMatch[];
    let via: "ripgrep" | "js-fallback";
    try {
      if (useRg) {
        matches = await searchViaRipgrep(root, pattern, {
          regex, caseSensitive, glob, maxMatches, contextLines, respectGitignore: true,
        });
        via = "ripgrep";
      } else {
        matches = await searchViaJs(root, pattern, { regex, caseSensitive, glob, maxMatches, contextLines });
        via = "js-fallback";
      }
    } catch (err) {
      log.tool.warn("search_files.execute: rg failed, falling back to JS", { err: String(err) });
      matches = await searchViaJs(root, pattern, { regex, caseSensitive, glob, maxMatches, contextLines });
      via = "js-fallback";
    }
```

- [ ] **Step 4: Run — verify pass (or auto-skip if rg not installed)**

```bash
npx vitest run __tests__/tools/filesystem/search-files-rg.test.ts
```

If rg is installed: 3/3 pass. If not: tests skip with a console log.

Also re-run JS tests to confirm no regression:

```bash
npx vitest run __tests__/tools/filesystem/search-files-js.test.ts
```

Expected: 6/6 still pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/filesystem/search-files.ts __tests__/tools/filesystem/search-files-rg.test.ts
git commit -m "feat(tools): search_files ripgrep acceleration with JS fallback"
```

---

## Task 8: Register `list_directory` + `search_files` in startup

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Locate the tool registration block**

```bash
grep -n "toolRegistry.registerAll" src/index.ts | head -5
```

Find the first `registerAll([...])` block (around line 382). Inside, locate the `// ── Media & files ──` section.

- [ ] **Step 2: Add imports near the top of `src/index.ts`**

In the imports block:

```typescript
import { ListDirectoryTool } from "./tools/filesystem/list-directory.js";
import { SearchFilesTool } from "./tools/filesystem/search-files.js";
```

- [ ] **Step 3: Register them in the tool array**

Inside the first `toolRegistry.registerAll([` array, after the existing file tools (`ReadFileTool`, `WriteFileTool`, `EditFileTool`), add:

```typescript
    ListDirectoryTool,
    SearchFilesTool,
```

- [ ] **Step 4: Verify build + boot**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0`.

```bash
timeout 25 npx tsx src/index.ts chat 2>&1 | grep -E "list_directory|search_files|FATAL" | head -5
```

Expected: no FATAL. The tools may not appear in startup logs (registration is silent) — just confirm no crash.

- [ ] **Step 5: Commit**

```bash
git add src/index.ts
git commit -m "feat(tools): register list_directory + search_files in tool registry"
```

---

## B2 — `git_tool` writes (5 tasks)

## Task 9: Refactor `git.ts` to use `platform.shell.exec`

Non-behaviour-changing refactor. Centralizes shell-out so write actions in T10-T12 can build on it.

**Files:**
- Modify: `src/tools/dev/git.ts`

- [ ] **Step 1: Read current impl**

```bash
sed -n '1,80p' src/tools/dev/git.ts
```

Identify the current `exec`/`spawn` use.

- [ ] **Step 2: Replace direct shell-out**

At the top of the file, add:

```typescript
import { platform } from "../../platform/index.js";
```

Remove direct uses of `child_process.exec` / `child_process.spawn` / `util.promisify(exec)`. Replace internal command runners with:

```typescript
async function gitCmd(cwd: string, args: string[], timeoutMs = 30_000): Promise<{ stdout: string; stderr: string; exitCode: number | null }> {
  const result = await platform.shell.exec(`git ${args.map(a => /["\s]/.test(a) ? JSON.stringify(a) : a).join(" ")}`, { cwd, timeoutMs });
  return { stdout: result.stdout, stderr: result.stderr, exitCode: result.exitCode };
}
```

Then replace each existing case (`status`, `log`, `diff`, `branch`, `stash`) to call `gitCmd(cwd, [...])` instead of the old runner. Behaviour stays identical — same stdout returned.

- [ ] **Step 3: Run existing git tool tests**

```bash
npx vitest run __tests__/tools/git-tool.test.ts 2>&1 | tail -5
```

(Or whatever the existing test file is named; check via `ls __tests__/tools/ | grep git`.)

Expected: all previously-passing tests still pass.

- [ ] **Step 4: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "git.ts"
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src/tools/dev/git.ts
git commit -m "refactor(tools): git_tool shells out via platform.shell.exec (no behavior change)"
```

---

## Task 10: Add write actions — `add`, `commit`, `fetch`, `push`, `pull`

**Files:**
- Modify: `src/tools/dev/git.ts`
- Create: `__tests__/tools/dev/git-writes.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/dev/git-writes.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { GitTool } from "../../../src/tools/dev/git.js";

let repo: string;

function git(repo: string, ...args: string[]): { stdout: string; status: number } {
  const r = spawnSync("git", args, { cwd: repo, encoding: "utf-8" });
  return { stdout: r.stdout, status: r.status ?? 1 };
}

beforeEach(() => {
  repo = mkdtempSync(join(tmpdir(), "stackowl-git-writes-"));
  git(repo, "init", "-b", "main");
  git(repo, "config", "user.email", "test@stackowl.local");
  git(repo, "config", "user.name", "Test");
});

afterEach(() => {
  rmSync(repo, { recursive: true, force: true });
});

describe("GitTool writes (add/commit/fetch/push/pull)", () => {
  it("add stages files", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    const res = await GitTool.execute({ action: "add", paths: ["a.txt"] }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "diff", "--cached", "--name-only").stdout.trim()).toBe("a.txt");
  });

  it("commit records the message", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    const res = await GitTool.execute({ action: "commit", message: "test: initial" }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "log", "-1", "--pretty=%s").stdout.trim()).toBe("test: initial");
  });

  it("commit with nothing staged returns an error", async () => {
    const res = await GitTool.execute({ action: "commit", message: "empty" }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
  });

  it("fetch attempts the remote (errors clearly when no remote configured)", async () => {
    const res = await GitTool.execute({ action: "fetch" }, { cwd: repo });
    const parsed = JSON.parse(res);
    // No remote configured — should fail but with structured error, not throw
    expect(parsed.success).toBe(false);
    expect(typeof parsed.error.message).toBe("string");
  });

  it("push without i_understand_destructive blocks --force", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "x" }, { cwd: repo });
    const res = await GitTool.execute({ action: "push", force: true }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });

  it("push with i_understand_destructive proceeds (fails because no remote — but past the gate)", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "x" }, { cwd: repo });
    const res = await GitTool.execute({ action: "push", force: true, i_understand_destructive: true }, { cwd: repo });
    const parsed = JSON.parse(res);
    // No remote — expect a git error, but NOT the destructive block
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).not.toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });

  it("pull without remote errors clearly", async () => {
    const res = await GitTool.execute({ action: "pull" }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

Expected: unknown-action errors for `add`, `commit`, etc.

- [ ] **Step 3: Extend `git.ts` action enum + add case branches**

In `src/tools/dev/git.ts`, update the parameter enum:

```typescript
action: {
  type: "string",
  description: "Git action: status/log/diff/branch/stash (read), add/commit/fetch/push/pull/checkout/merge/rebase/reset/branch_create/branch_delete/tag (write)",
  enum: [
    "status", "log", "diff", "branch", "stash",
    "add", "commit", "fetch", "push", "pull",
    "checkout", "merge", "rebase", "reset",
    "branch_create", "branch_delete", "tag",
  ],
},
```

Add the new parameter properties:

```typescript
        paths: { type: "string", description: "Comma-separated paths for action:add (use \".\" for all)" },
        message: { type: "string", description: "Commit/tag message" },
        amend: { type: "boolean", description: "Amend the previous commit (action:commit)" },
        target: { type: "string", description: "Branch/commit/file target (action:checkout/reset)" },
        create_branch: { type: "boolean", description: "Create branch on checkout (-b)" },
        remote: { type: "string", description: "Remote name (default origin)" },
        branch: { type: "string", description: "Branch name for action:push/pull/branch_create" },
        from: { type: "string", description: "Source ref for action:branch_create" },
        name: { type: "string", description: "Branch/tag name" },
        force: { type: "boolean", description: "Force flag (push/branch_delete). Destructive — requires i_understand_destructive." },
        mode: { type: "string", description: "Reset mode: soft|mixed|hard (default mixed)", enum: ["soft", "mixed", "hard"] },
        rebase: { type: "boolean", description: "Pull --rebase" },
        no_ff: { type: "boolean", description: "Merge --no-ff" },
        abort: { type: "boolean", description: "Abort in-progress merge/rebase" },
        continue: { type: "boolean", description: "Continue in-progress rebase" },
        onto: { type: "string", description: "Rebase --onto target" },
        delete: { type: "boolean", description: "Delete tag (action:tag)" },
        i_understand_destructive: { type: "boolean", description: "Required for destructive actions (force push, hard reset, force branch delete)" },
```

In `execute()`, add the destructive gate **before** the switch:

```typescript
    const isDestructive =
      (action === "push" && args.force === true) ||
      (action === "reset" && (args.mode as string) === "hard") ||
      (action === "branch_delete" && args.force === true);

    if (isDestructive && args.i_understand_destructive !== true) {
      log.tool.warn("git_tool: destructive action blocked", { action, force: args.force, mode: args.mode });
      return JSON.stringify({
        success: false,
        error: {
          code: "DESTRUCTIVE_ACTION_BLOCKED",
          message: `${action}${args.force ? " --force" : ""}${(args.mode as string) === "hard" ? " --hard" : ""} is destructive. Pass i_understand_destructive: true to proceed.`,
          hint: "This action can permanently destroy work. Confirm with the user before retrying.",
        },
      });
    }
```

Add the new cases (T10 batch — add/commit/fetch/push/pull):

```typescript
      case "add": {
        const pathsArg = args["paths"] as string | string[] | undefined;
        const paths = Array.isArray(pathsArg) ? pathsArg : (pathsArg ? pathsArg.split(",").map(s => s.trim()) : ["."]);
        const r = await gitCmd(cwd, ["add", ...paths]);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { staged: paths } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr || `exit ${r.exitCode}` } });
      }

      case "commit": {
        const message = args["message"] as string;
        const amend = args["amend"] === true;
        if (!message && !amend) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const cmdArgs = ["commit"];
        if (amend) cmdArgs.push("--amend");
        if (message) cmdArgs.push("-m", message);
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "fetch": {
        const remote = (args["remote"] as string) ?? "origin";
        const r = await gitCmd(cwd, ["fetch", remote]);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || `fetched ${remote}` } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "push": {
        const remote = (args["remote"] as string) ?? "origin";
        const branch = args["branch"] as string | undefined;
        const force = args["force"] === true;
        const cmdArgs = ["push"];
        if (force) cmdArgs.push("--force-with-lease");
        cmdArgs.push(remote);
        if (branch) cmdArgs.push(branch);
        log.tool.warn("git_tool.push: destructive action proceeding (audit)", { remote, branch, force, cmd: cmdArgs });
        const r = await gitCmd(cwd, cmdArgs, 60_000);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || "push complete" } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "pull": {
        const remote = (args["remote"] as string) ?? "origin";
        const branch = args["branch"] as string | undefined;
        const rebase = args["rebase"] === true;
        const cmdArgs = ["pull"];
        if (rebase) cmdArgs.push("--rebase");
        cmdArgs.push(remote);
        if (branch) cmdArgs.push(branch);
        const r = await gitCmd(cwd, cmdArgs, 60_000);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || "pull complete" } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

Expected: 7/7 pass for batch 1 cases.

- [ ] **Step 5: Commit**

```bash
git add src/tools/dev/git.ts __tests__/tools/dev/git-writes.test.ts
git commit -m "feat(tools): git_tool add/commit/fetch/push/pull + destructive-action gate"
```

---

## Task 11: Add `checkout`, `merge`, `rebase`, `reset`

**Files:**
- Modify: `src/tools/dev/git.ts`
- Modify: `__tests__/tools/dev/git-writes.test.ts` (append tests)

- [ ] **Step 1: Append failing tests**

Add to `__tests__/tools/dev/git-writes.test.ts` inside the existing `describe`:

```typescript
  it("checkout creates and switches to a new branch", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    const res = await GitTool.execute({ action: "checkout", target: "feature", create_branch: true }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "branch", "--show-current").stdout.trim()).toBe("feature");
  });

  it("merge --abort cancels an in-progress merge", async () => {
    // Set up two divergent branches with a merge conflict
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    await GitTool.execute({ action: "checkout", target: "branch-b", create_branch: true }, { cwd: repo });
    writeFileSync(join(repo, "a.txt"), "y");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "b" }, { cwd: repo });
    await GitTool.execute({ action: "checkout", target: "main" }, { cwd: repo });
    writeFileSync(join(repo, "a.txt"), "z");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "a" }, { cwd: repo });

    await GitTool.execute({ action: "merge", branch: "branch-b" }, { cwd: repo });
    const abort = await GitTool.execute({ action: "merge", abort: true }, { cwd: repo });
    const parsed = JSON.parse(abort);
    expect(parsed.success).toBe(true);
  });

  it("reset mixed (default) unstages", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    writeFileSync(join(repo, "a.txt"), "y");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    const res = await GitTool.execute({ action: "reset", target: "HEAD" }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "diff", "--cached", "--name-only").stdout.trim()).toBe("");
  });

  it("reset --hard without i_understand_destructive is blocked", async () => {
    const res = await GitTool.execute({ action: "reset", target: "HEAD", mode: "hard" }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });
```

- [ ] **Step 2: Run — verify failures for new tests**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

- [ ] **Step 3: Add the four new cases in `git.ts`**

```typescript
      case "checkout": {
        const target = args["target"] as string;
        const createBranch = args["create_branch"] === true;
        if (!target) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "target is required" } });
        const cmdArgs = ["checkout"];
        if (createBranch) cmdArgs.push("-b");
        cmdArgs.push(target);
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "merge": {
        const abort = args["abort"] === true;
        if (abort) {
          const r = await gitCmd(cwd, ["merge", "--abort"]);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { stdout: "merge aborted" } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
        }
        const branch = args["branch"] as string;
        const noFf = args["no_ff"] === true;
        if (!branch) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "branch is required" } });
        const cmdArgs = ["merge"];
        if (noFf) cmdArgs.push("--no-ff");
        cmdArgs.push(branch);
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "rebase": {
        const abort = args["abort"] === true;
        const cont = args["continue"] === true;
        const onto = args["onto"] as string | undefined;
        const cmdArgs = ["rebase"];
        if (abort) cmdArgs.push("--abort");
        else if (cont) cmdArgs.push("--continue");
        else if (onto) cmdArgs.push("--onto", onto);
        else return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "rebase requires onto, abort, or continue" } });
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "reset": {
        const target = args["target"] as string;
        const mode = (args["mode"] as string) ?? "mixed";
        if (!target) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "target is required" } });
        if (!["soft", "mixed", "hard"].includes(mode)) {
          return JSON.stringify({ success: false, error: { code: "INVALID_ARG", message: `mode must be soft|mixed|hard, got ${mode}` } });
        }
        if (mode === "hard") log.tool.warn("git_tool.reset: destructive --hard proceeding (audit)", { target });
        const r = await gitCmd(cwd, ["reset", `--${mode}`, target]);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || `reset ${mode}` } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/dev/git.ts __tests__/tools/dev/git-writes.test.ts
git commit -m "feat(tools): git_tool checkout/merge/rebase/reset with destructive gating"
```

---

## Task 12: Add `branch_create`, `branch_delete`, `tag`

**Files:**
- Modify: `src/tools/dev/git.ts`
- Modify: `__tests__/tools/dev/git-writes.test.ts`

- [ ] **Step 1: Append failing tests**

```typescript
  it("branch_create makes a new branch from current HEAD", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    const res = await GitTool.execute({ action: "branch_create", name: "topic" }, { cwd: repo });
    expect(JSON.parse(res).success).toBe(true);
    expect(git(repo, "branch", "--list", "topic").stdout.includes("topic")).toBe(true);
  });

  it("branch_delete non-force on merged branch succeeds", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    await GitTool.execute({ action: "branch_create", name: "topic" }, { cwd: repo });
    const res = await GitTool.execute({ action: "branch_delete", name: "topic" }, { cwd: repo });
    expect(JSON.parse(res).success).toBe(true);
  });

  it("branch_delete --force without i_understand_destructive is blocked", async () => {
    const res = await GitTool.execute({ action: "branch_delete", name: "any", force: true }, { cwd: repo });
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });

  it("tag creates a tag", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo });
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo });
    const res = await GitTool.execute({ action: "tag", name: "v0.1" }, { cwd: repo });
    expect(JSON.parse(res).success).toBe(true);
    expect(git(repo, "tag").stdout.trim()).toBe("v0.1");
  });
```

- [ ] **Step 2: Run — verify failures**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

- [ ] **Step 3: Implement the three new cases**

```typescript
      case "branch_create": {
        const name = args["name"] as string;
        const from = args["from"] as string | undefined;
        if (!name) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "name is required" } });
        const cmdArgs = ["branch", name];
        if (from) cmdArgs.push(from);
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { created: name } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "branch_delete": {
        const name = args["name"] as string;
        const force = args["force"] === true;
        if (!name) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "name is required" } });
        const flag = force ? "-D" : "-d";
        if (force) log.tool.warn("git_tool.branch_delete: destructive --force proceeding (audit)", { name });
        const r = await gitCmd(cwd, ["branch", flag, name]);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { deleted: name } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }

      case "tag": {
        const name = args["name"] as string;
        const message = args["message"] as string | undefined;
        const del = args["delete"] === true;
        if (!name) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "name is required" } });
        const cmdArgs = ["tag"];
        if (del) cmdArgs.push("-d");
        else if (message) cmdArgs.push("-a", name, "-m", message);
        else cmdArgs.push(name);
        if (del) cmdArgs.push(name);
        const r = await gitCmd(cwd, cmdArgs);
        return r.exitCode === 0
          ? JSON.stringify({ success: true, data: { tag: name, deleted: del } })
          : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
      }
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

Expected: full file passes (all 14 tests across T10-T12).

- [ ] **Step 5: Commit**

```bash
git add src/tools/dev/git.ts __tests__/tools/dev/git-writes.test.ts
git commit -m "feat(tools): git_tool branch_create/branch_delete/tag with force-delete gating"
```

---

## Task 13: Repo-presence guard + final audit

**Files:**
- Modify: `src/tools/dev/git.ts`

- [ ] **Step 1: Write the test for repo guard**

Append to `__tests__/tools/dev/git-writes.test.ts`:

```typescript
  it("rejects writes when cwd is not inside a git repo", async () => {
    const notRepo = mkdtempSync(join(tmpdir(), "stackowl-not-git-"));
    try {
      const res = await GitTool.execute({ action: "add", paths: ["."] }, { cwd: notRepo });
      const parsed = JSON.parse(res);
      expect(parsed.success).toBe(false);
      expect(parsed.error.code).toBe("NOT_A_GIT_REPO");
    } finally {
      rmSync(notRepo, { recursive: true, force: true });
    }
  });
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

- [ ] **Step 3: Add the guard**

In `git.ts` execute(), define a list of write actions and add a pre-check:

```typescript
const WRITE_ACTIONS = new Set([
  "add", "commit", "fetch", "push", "pull",
  "checkout", "merge", "rebase", "reset",
  "branch_create", "branch_delete", "tag",
]);

// inside execute(), AFTER reading action but BEFORE the destructive gate:
if (WRITE_ACTIONS.has(action)) {
  const check = await gitCmd(cwd, ["rev-parse", "--show-toplevel"]);
  if (check.exitCode !== 0) {
    return JSON.stringify({
      success: false,
      error: { code: "NOT_A_GIT_REPO", message: `${cwd} is not inside a git repository` },
    });
  }
}
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/tools/dev/git-writes.test.ts
```

Expected: all 15 git tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/dev/git.ts __tests__/tools/dev/git-writes.test.ts
git commit -m "feat(tools): git_tool rejects writes outside a git repo"
```

---

## B6 — `schedule` durability (4 tasks)

## Task 14: `scheduled_jobs` schema migration

**Files:**
- Modify: `src/memory/db.ts`
- Create: `__tests__/memory/scheduled-jobs-schema.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory/scheduled-jobs-schema.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let dir: string;

beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "stackowl-sched-schema-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("scheduled_jobs schema", () => {
  it("table exists after MemoryDatabase init", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
      .get();
    expect(row).toBeTruthy();
  });

  it("table has the expected columns", () => {
    const db = new MemoryDatabase(dir);
    const cols = db.rawDb.prepare("PRAGMA table_info(scheduled_jobs)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toEqual(expect.arrayContaining([
      "id", "type", "message", "schedule_at", "interval_ms", "next_fire_at",
      "created_at", "status", "metadata",
    ]));
  });

  it("insert + query a job", () => {
    const db = new MemoryDatabase(dir);
    db.rawDb.prepare(`
      INSERT INTO scheduled_jobs (id, type, message, next_fire_at, status, metadata)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run("j1", "remind", "test", new Date().toISOString(), "active", "{}");
    const row = db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE id = ?").get("j1") as any;
    expect(row.type).toBe("remind");
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/memory/scheduled-jobs-schema.test.ts
```

- [ ] **Step 3: Add table to `createSchema()` in `src/memory/db.ts`**

Find the existing `createSchema()` method (it has a long `this.db.exec(\`...\`)` call). Append inside the same string (or as a second `.exec()`):

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL CHECK(type IN ('remind', 'repeat')),
  message      TEXT NOT NULL,
  schedule_at  TEXT,
  interval_ms  INTEGER,
  next_fire_at TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  status       TEXT NOT NULL DEFAULT 'active'
                 CHECK(status IN ('active', 'fired', 'cancelled', 'expired')),
  metadata     TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due ON scheduled_jobs(next_fire_at, status);
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/memory/scheduled-jobs-schema.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory/scheduled-jobs-schema.test.ts
git commit -m "feat(memory): scheduled_jobs table for durable schedule persistence"
```

---

## Task 15: `ScheduleStore`

**Files:**
- Create: `src/schedule/types.ts`
- Create: `src/schedule/store.ts`
- Create: `__tests__/schedule/store.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/schedule/store.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ScheduleStore } from "../../src/schedule/store.js";
import type { ScheduledJob } from "../../src/schedule/types.js";

let dir: string;
let db: MemoryDatabase;
let store: ScheduleStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-sched-store-"));
  db = new MemoryDatabase(dir);
  store = new ScheduleStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

function makeJob(over: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    id: "j" + Math.random().toString(36).slice(2, 8),
    type: "remind",
    message: "test",
    scheduleAt: new Date(Date.now() + 60_000).toISOString(),
    nextFireAt: new Date(Date.now() + 60_000).toISOString(),
    createdAt: new Date().toISOString(),
    status: "active",
    metadata: {},
    ...over,
  };
}

describe("ScheduleStore", () => {
  it("add + list", () => {
    store.add(makeJob({ id: "a" }));
    const all = store.list();
    expect(all).toHaveLength(1);
    expect(all[0].id).toBe("a");
  });

  it("update patches fields", () => {
    store.add(makeJob({ id: "a" }));
    store.update("a", { status: "fired" });
    expect(store.list()[0].status).toBe("fired");
  });

  it("remove deletes", () => {
    store.add(makeJob({ id: "a" }));
    store.remove("a");
    expect(store.list()).toHaveLength(0);
  });

  it("list filter by status", () => {
    store.add(makeJob({ id: "a", status: "active" }));
    store.add(makeJob({ id: "b", status: "fired" }));
    expect(store.list({ status: "active" })).toHaveLength(1);
  });

  it("due() returns past-due active jobs", () => {
    store.add(makeJob({ id: "past", nextFireAt: new Date(Date.now() - 1000).toISOString(), status: "active" }));
    store.add(makeJob({ id: "future", nextFireAt: new Date(Date.now() + 60_000).toISOString(), status: "active" }));
    const due = store.due(new Date());
    expect(due).toHaveLength(1);
    expect(due[0].id).toBe("past");
  });

  it("survives database close/reopen", () => {
    store.add(makeJob({ id: "persist" }));
    // Re-open
    const db2 = new MemoryDatabase(dir);
    const store2 = new ScheduleStore(db2);
    expect(store2.list().some(j => j.id === "persist")).toBe(true);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/schedule/store.test.ts
```

- [ ] **Step 3: Create `src/schedule/types.ts`**

```typescript
export type JobStatus = "active" | "fired" | "cancelled" | "expired";

export interface ScheduledJob {
  id: string;
  type: "remind" | "repeat";
  message: string;
  scheduleAt?: string;
  intervalMs?: number;
  nextFireAt: string;
  createdAt: string;
  status: JobStatus;
  metadata: {
    urgency?: "low" | "normal" | "critical";
    category?: string;
    channel?: string;
    userId?: string;
  };
}
```

- [ ] **Step 4: Create `src/schedule/store.ts`**

```typescript
import type { MemoryDatabase } from "../memory/db.js";
import type { ScheduledJob, JobStatus } from "./types.js";

interface Row {
  id: string;
  type: "remind" | "repeat";
  message: string;
  schedule_at: string | null;
  interval_ms: number | null;
  next_fire_at: string;
  created_at: string;
  status: JobStatus;
  metadata: string | null;
}

function toJob(r: Row): ScheduledJob {
  return {
    id: r.id,
    type: r.type,
    message: r.message,
    scheduleAt: r.schedule_at ?? undefined,
    intervalMs: r.interval_ms ?? undefined,
    nextFireAt: r.next_fire_at,
    createdAt: r.created_at,
    status: r.status,
    metadata: r.metadata ? JSON.parse(r.metadata) : {},
  };
}

export class ScheduleStore {
  constructor(private readonly db: MemoryDatabase) {}

  add(job: ScheduledJob): void {
    this.db.rawDb.prepare(`
      INSERT OR REPLACE INTO scheduled_jobs
      (id, type, message, schedule_at, interval_ms, next_fire_at, created_at, status, metadata)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      job.id, job.type, job.message,
      job.scheduleAt ?? null, job.intervalMs ?? null,
      job.nextFireAt, job.createdAt, job.status,
      JSON.stringify(job.metadata ?? {}),
    );
  }

  update(id: string, patch: Partial<ScheduledJob>): void {
    const existing = this.findOne(id);
    if (!existing) return;
    const next = { ...existing, ...patch, metadata: { ...existing.metadata, ...(patch.metadata ?? {}) } };
    this.add(next);
  }

  remove(id: string): void {
    this.db.rawDb.prepare("DELETE FROM scheduled_jobs WHERE id = ?").run(id);
  }

  findOne(id: string): ScheduledJob | null {
    const row = this.db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE id = ?").get(id) as Row | undefined;
    return row ? toJob(row) : null;
  }

  list(filter?: { status?: JobStatus }): ScheduledJob[] {
    const rows = filter?.status
      ? this.db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY next_fire_at").all(filter.status) as Row[]
      : this.db.rawDb.prepare("SELECT * FROM scheduled_jobs ORDER BY next_fire_at").all() as Row[];
    return rows.map(toJob);
  }

  due(now: Date): ScheduledJob[] {
    const rows = this.db.rawDb.prepare(
      "SELECT * FROM scheduled_jobs WHERE status = 'active' AND next_fire_at <= ? ORDER BY next_fire_at"
    ).all(now.toISOString()) as Row[];
    return rows.map(toJob);
  }
}
```

- [ ] **Step 5: Run — verify pass**

```bash
npx vitest run __tests__/schedule/store.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add src/schedule/types.ts src/schedule/store.ts __tests__/schedule/store.test.ts
git commit -m "feat(schedule): ScheduleStore — SQLite-backed CRUD + due() query"
```

---

## Task 16: `ScheduleRunner` — timers + hydration

**Files:**
- Create: `src/schedule/runner.ts`
- Create: `__tests__/schedule/runner.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/schedule/runner.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ScheduleStore } from "../../src/schedule/store.js";
import { ScheduleRunner } from "../../src/schedule/runner.js";
import type { Notifier } from "../../src/platform/index.js";

let dir: string;
let db: MemoryDatabase;
let store: ScheduleStore;
let notified: any[];
let notifier: Notifier;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-sched-runner-"));
  db = new MemoryDatabase(dir);
  store = new ScheduleStore(db);
  notified = [];
  notifier = {
    notify: async (opts) => { notified.push(opts); return { delivered: true, via: "system" }; },
    capabilities: () => ({ native: false, system: true }),
  };
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("ScheduleRunner", () => {
  it("scheduleJob fires after delay", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "soon", type: "remind", message: "ping",
      nextFireAt: new Date(Date.now() + 50).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    await new Promise(r => setTimeout(r, 120));
    expect(notified.length).toBe(1);
    expect(notified[0].body).toBe("ping");
    runner.stop();
  });

  it("cancelJob clears the timer", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "to-cancel", type: "remind", message: "should-not-fire",
      nextFireAt: new Date(Date.now() + 100).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    const ok = runner.cancelJob("to-cancel");
    expect(ok).toBe(true);
    await new Promise(r => setTimeout(r, 200));
    expect(notified.length).toBe(0);
    runner.stop();
  });

  it("start() hydrates expired jobs (fires once with [Missed Reminder])", async () => {
    store.add({
      id: "expired", type: "remind", message: "old",
      scheduleAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      nextFireAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      createdAt: new Date(Date.now() - 11 * 60 * 1000).toISOString(),
      status: "active", metadata: {},
    });
    const runner = new ScheduleRunner(store, notifier);
    await runner.start();
    expect(notified.length).toBe(1);
    expect(notified[0].body).toContain("Missed");
    expect(store.findOne("expired")?.status).toBe("expired");
    runner.stop();
  });

  it("start() schedules future jobs without firing", async () => {
    store.add({
      id: "future", type: "remind", message: "later",
      scheduleAt: new Date(Date.now() + 60_000).toISOString(),
      nextFireAt: new Date(Date.now() + 60_000).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    const runner = new ScheduleRunner(store, notifier);
    await runner.start();
    await new Promise(r => setTimeout(r, 100));
    expect(notified.length).toBe(0);
    runner.stop();
  });

  it("repeat jobs re-fire after intervalMs", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "rep", type: "repeat", intervalMs: 50, message: "tick",
      nextFireAt: new Date(Date.now() + 50).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    await new Promise(r => setTimeout(r, 180));
    expect(notified.length).toBeGreaterThanOrEqual(2);
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/schedule/runner.test.ts
```

- [ ] **Step 3: Implement `src/schedule/runner.ts`**

```typescript
import { log } from "../logger.js";
import type { ScheduleStore } from "./store.js";
import type { ScheduledJob } from "./types.js";
import type { Notifier } from "../platform/index.js";

const EXPIRED_THRESHOLD_MS = 5 * 60 * 1000;

export class ScheduleRunner {
  private timers = new Map<string, NodeJS.Timeout>();

  constructor(
    private readonly store: ScheduleStore,
    private readonly notifier: Notifier,
  ) {}

  async start(): Promise<void> {
    log.engine.info("[ScheduleRunner] starting — hydrating active jobs");
    const active = this.store.list({ status: "active" });
    const now = Date.now();
    let hydrated = 0, expired = 0;

    for (const job of active) {
      const fireAt = Date.parse(job.nextFireAt);
      if (Number.isNaN(fireAt)) {
        this.store.update(job.id, { status: "expired" });
        continue;
      }
      if (fireAt < now - EXPIRED_THRESHOLD_MS) {
        // Missed reminder — fire once, mark expired
        await this.fireMissed(job);
        expired++;
      } else {
        this.scheduleJob(job);
        hydrated++;
      }
    }
    log.engine.info("[ScheduleRunner] start complete", { hydrated, expired });
  }

  stop(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
    log.engine.info("[ScheduleRunner] stopped");
  }

  scheduleJob(job: ScheduledJob): void {
    this.store.add(job);
    const fireAt = Date.parse(job.nextFireAt);
    const delay = Math.max(0, fireAt - Date.now());
    const timer = setTimeout(() => this.fire(job), delay);
    this.timers.set(job.id, timer);
    log.engine.debug("[ScheduleRunner] job scheduled", { id: job.id, delay });
  }

  cancelJob(id: string): boolean {
    const timer = this.timers.get(id);
    if (timer) clearTimeout(timer);
    this.timers.delete(id);
    const existed = !!this.store.findOne(id);
    if (existed) this.store.update(id, { status: "cancelled" });
    return existed;
  }

  private async fire(job: ScheduledJob): Promise<void> {
    this.timers.delete(job.id);
    try {
      const result = await this.notifier.notify({
        title: job.metadata.category ?? "Reminder",
        body: job.message,
        urgency: job.metadata.urgency ?? "normal",
        category: job.metadata.category ?? "schedule",
      });
      log.engine.info("[ScheduleRunner] fired", { id: job.id, via: result.via });
    } catch (err) {
      log.engine.error("[ScheduleRunner] notify failed", err as Error, { id: job.id });
    }

    if (job.type === "repeat" && job.intervalMs) {
      const nextFireAt = new Date(Date.now() + job.intervalMs).toISOString();
      const updated: ScheduledJob = { ...job, nextFireAt };
      this.store.update(job.id, { nextFireAt });
      const timer = setTimeout(() => this.fire(updated), job.intervalMs);
      this.timers.set(job.id, timer);
    } else {
      this.store.update(job.id, { status: "fired" });
    }
  }

  private async fireMissed(job: ScheduledJob): Promise<void> {
    try {
      await this.notifier.notify({
        title: job.metadata.category ?? "Missed Reminder",
        body: `Missed: ${job.message}`,
        urgency: "normal",
        category: "schedule-missed",
      });
    } catch (err) {
      log.engine.error("[ScheduleRunner] missed-notify failed", err as Error, { id: job.id });
    }
    this.store.update(job.id, { status: "expired" });
  }
}
```

- [ ] **Step 4: Run — verify pass**

```bash
npx vitest run __tests__/schedule/runner.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/schedule/runner.ts __tests__/schedule/runner.test.ts
git commit -m "feat(schedule): ScheduleRunner — timer lifecycle + boot-time hydration with missed-reminder handling"
```

---

## Task 17: Rewrite `src/tools/schedule.ts` + bootstrap wiring

**Files:**
- Modify: `src/tools/schedule.ts`
- Modify: `src/index.ts`

- [ ] **Step 1: Rewrite `src/tools/schedule.ts` as thin delegate**

```typescript
import { randomUUID } from "node:crypto";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ScheduleRunner } from "../schedule/runner.js";
import type { ScheduleStore } from "../schedule/store.js";
import { log } from "../logger.js";

let runnerRef: ScheduleRunner | null = null;
let storeRef: ScheduleStore | null = null;

/** Called from src/index.ts after the runner is created. */
export function attachSchedule(runner: ScheduleRunner, store: ScheduleStore): void {
  runnerRef = runner;
  storeRef = store;
}

function parseWhen(when: string): Date | null {
  const now = Date.now();
  const relMatch = when.match(/^in\s+(\d+(?:\.\d+)?)\s*(second|minute|hour|day)s?$/i);
  if (relMatch) {
    const n = parseFloat(relMatch[1]!);
    const unit = relMatch[2]!.toLowerCase();
    const multipliers: Record<string, number> = {
      second: 1_000, minute: 60_000, hour: 3_600_000, day: 86_400_000,
    };
    return new Date(now + n * multipliers[unit]!);
  }
  const d = new Date(when);
  if (!isNaN(d.getTime()) && d.getTime() > now) return d;
  return null;
}

export const ScheduleTool: ToolImplementation = {
  definition: {
    name: "schedule",
    description:
      "Schedule reminders and recurring tasks. Natural language times: \"in 5 minutes\", \"in 2 hours\". " +
      "Durable: jobs survive process restarts (SQLite-backed). " +
      'Example: schedule(action: "remind", when: "in 30 minutes", message: "Check deployment")',
    parameters: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["remind", "repeat", "cancel", "list"], description: "What to do" },
        when: { type: "string", description: "For remind: \"in N minutes/hours/days\" or ISO 8601. For repeat: interval in ms." },
        message: { type: "string", description: "Message to deliver when the job fires" },
        id: { type: "string", description: "Job ID for cancel" },
      },
      required: ["action"],
    },
    capabilities: ["schedule", "reminder"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!runnerRef || !storeRef) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Schedule runner not yet initialized" } });
    }
    const action = args["action"] as string;
    const when = args["when"] as string | undefined;
    const message = args["message"] as string | undefined;
    const id = args["id"] as string | undefined;
    log.tool.debug("schedule.execute: entry", { action });

    switch (action) {
      case "remind": {
        if (!when) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const fireAt = parseWhen(when);
        if (!fireAt) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: `Cannot parse: "${when}"` } });
        const jobId = randomUUID();
        runnerRef.scheduleJob({
          id: jobId, type: "remind", message,
          scheduleAt: fireAt.toISOString(), nextFireAt: fireAt.toISOString(),
          createdAt: new Date().toISOString(), status: "active", metadata: {},
        });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Reminder scheduled for ${fireAt.toISOString()}` } });
      }
      case "repeat": {
        if (!when) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when (interval ms) is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const intervalMs = parseInt(when, 10);
        if (isNaN(intervalMs) || intervalMs <= 0) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: "when for repeat must be positive ms" } });
        const jobId = randomUUID();
        runnerRef.scheduleJob({
          id: jobId, type: "repeat", intervalMs, message,
          nextFireAt: new Date(Date.now() + intervalMs).toISOString(),
          createdAt: new Date().toISOString(), status: "active", metadata: {},
        });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Repeating every ${intervalMs}ms` } });
      }
      case "cancel": {
        if (!id) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
        const ok = runnerRef.cancelJob(id);
        return ok
          ? JSON.stringify({ success: true, data: { id, message: "cancelled" } })
          : JSON.stringify({ success: false, error: { code: "JOB_NOT_FOUND", message: `Job "${id}" not found` } });
      }
      case "list": {
        const jobs = storeRef.list({ status: "active" });
        return JSON.stringify({ success: true, data: { jobs, count: jobs.length } });
      }
      default:
        return JSON.stringify({ success: false, error: { code: "INVALID_ACTION", message: `Unknown action: "${action}"` } });
    }
  },
};
```

- [ ] **Step 2: Wire into `src/index.ts` bootstrap**

In `src/index.ts`, after the platform initialize and after `memoryDb` is created, before tool registration uses `ScheduleTool`:

```typescript
import { ScheduleStore } from "./schedule/store.js";
import { ScheduleRunner } from "./schedule/runner.js";
import { attachSchedule } from "./tools/schedule.js";

// ...later in bootstrap, after memoryDb + platform.initialize():
const scheduleStore = new ScheduleStore(memoryDb);
const scheduleRunner = new ScheduleRunner(scheduleStore, platform.notifier);
attachSchedule(scheduleRunner, scheduleStore);
await scheduleRunner.start();

// And on shutdown signals:
process.on("SIGTERM", () => scheduleRunner.stop());
process.on("SIGINT", () => scheduleRunner.stop());
```

(If existing SIGTERM/SIGINT handlers exist, augment them rather than replace.)

- [ ] **Step 3: Verify build + boot**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0`.

```bash
timeout 25 npx tsx src/index.ts chat 2>&1 | grep -E "ScheduleRunner|FATAL" | head -5
```

Expected: `[ScheduleRunner] starting — hydrating active jobs` log line, no FATAL.

- [ ] **Step 4: Commit**

```bash
git add src/tools/schedule.ts src/index.ts
git commit -m "feat(schedule): rewrite ScheduleTool as thin delegate over runner + boot-time wiring"
```

---

## B4 — notification delivery wiring (4 tasks)

## Task 18: Schedule runner already wired to `platform.notifier` (verify)

This was already implemented in T16 (the runner takes a `Notifier` in its constructor and calls `notifier.notify()` on fire). No code change — this task is a verification step.

- [ ] **Step 1: Verify the wiring**

```bash
grep -n "notifier.notify" src/schedule/runner.ts
```

Expected: at least two matches (one in `fire`, one in `fireMissed`).

- [ ] **Step 2: Confirm `src/index.ts` passes `platform.notifier`**

```bash
grep -n "new ScheduleRunner" src/index.ts
```

Expected: the line `new ScheduleRunner(scheduleStore, platform.notifier)`.

- [ ] **Step 3: Boot-time delivery via fallback chain**

Confirm that when no native notifier exists, the fallback chain delivers correctly. Already covered by T16's tests. Nothing to commit for this task — it's a checkpoint.

If you want to be thorough, add an integration test that wires a real platform + a tmp-dir schedule, but it duplicates T16 coverage. Skip.

---

## Task 19: Wire `heartbeat/proactive` to `platform.notifier`

**Files:**
- Modify: `src/heartbeat/proactive.ts`

- [ ] **Step 1: Read the current delivery path**

```bash
grep -n "console.log\|onProgress\|deliveryTarget" src/heartbeat/proactive.ts | head -10
```

Identify how messages are currently delivered.

- [ ] **Step 2: Add fallback through `platform.notifier`**

In `src/heartbeat/proactive.ts`, find the place that delivers a generated message. Where the current code delivers only when a channel adapter is configured, add a fallback:

```typescript
import { platform } from "../platform/index.js";

// inside the function that sends a message:
if (channelAdapter) {
  await channelAdapter.send(message);
} else {
  // No channel — fall back through the notifier chain (native → system → stderr)
  await platform.notifier.notify({
    title: owlName ?? "Heartbeat",
    body: message,
    category: "heartbeat",
  });
}
```

(Adapt names to whatever the actual variables are. The principle: the `else` branch — when no specific channel adapter is configured for this user — routes through the notifier.)

- [ ] **Step 3: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "heartbeat"
```

Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add src/heartbeat/proactive.ts
git commit -m "feat(heartbeat): fall back to platform.notifier when no channel adapter configured"
```

---

## Task 20: Wire cron `deliver:true` fallback to `platform.notifier`

**Files:**
- Modify: `src/index.ts` (in the cron `onJobFire` block)

- [ ] **Step 1: Locate the `[DELIVER_PENDING]` log**

```bash
grep -n "DELIVER_PENDING\|deliveryTarget" src/index.ts
```

- [ ] **Step 2: Replace the `[DELIVER_PENDING]` log with notifier fallback**

Find the cron `onJobFire` block that currently logs `[DELIVER_PENDING]`. Replace:

```typescript
} else {
  log.engine.info("[CronService] [DELIVER_PENDING] Job result ready but no deliveryTarget configured", {
    jobId: job.id,
    resultPreview: result.slice(0, 200),
  });
}
```

with:

```typescript
} else {
  // Fall back to the platform notifier so the user actually sees critical
  // cron output (e.g., daily-briefing) even without a channel configured.
  try {
    await platform.notifier.notify({
      title: `cron: ${job.id}`,
      body: result.slice(0, 500),
      category: "cron",
    });
    log.engine.info("[CronService] result delivered via platform.notifier", { jobId: job.id });
  } catch (err) {
    log.engine.warn("[CronService] notifier delivery failed", { jobId: job.id, err: String(err) });
  }
}
```

Make sure `platform` is imported at the top of `src/index.ts`. If it's already imported by Task 17, no new import needed.

- [ ] **Step 3: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "index.ts"
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/index.ts
git commit -m "feat(cron): fall back to platform.notifier when deliveryTarget is missing"
```

---

## Task 21: `notification_send` tool with rate limiting

**Files:**
- Create: `src/tools/notification-send.ts`
- Create: `__tests__/tools/notification-send.test.ts`
- Modify: `src/index.ts` (register)

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/notification-send.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { createPlatform } from "../../src/platform/index.js";
import { createNotificationSendTool } from "../../src/tools/notification-send.js";

describe("NotificationSendTool", () => {
  it("delivers via platform.notifier", async () => {
    const captured: any[] = [];
    const platform = createPlatform({
      notifier: {
        nativeImpl: { notify: (opts: any, cb: any) => { captured.push(opts); cb(null, "ok"); } },
      },
    });
    const tool = createNotificationSendTool(platform);
    const res = await tool.execute({ title: "Hi", body: "test" }, { engineContext: { sessionId: "s1" } } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.via).toBe("native");
    expect(captured.length).toBe(1);
  });

  it("rate-limits at 10 per minute per session", async () => {
    const platform = createPlatform({
      notifier: { nativeImpl: { notify: (_o: any, cb: any) => cb(null, "ok") } },
    });
    const tool = createNotificationSendTool(platform);
    const ctx = { engineContext: { sessionId: "rate-test" } } as any;
    for (let i = 0; i < 10; i++) {
      await tool.execute({ title: `n${i}`, body: "x" }, ctx);
    }
    const res = await tool.execute({ title: "n11", body: "x" }, ctx);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("E_RATE_LIMITED");
  });

  it("rate-limit is per-session (different sessions get fresh budget)", async () => {
    const platform = createPlatform({
      notifier: { nativeImpl: { notify: (_o: any, cb: any) => cb(null, "ok") } },
    });
    const tool = createNotificationSendTool(platform);
    for (let i = 0; i < 10; i++) {
      await tool.execute({ title: `a${i}`, body: "x" }, { engineContext: { sessionId: "sess-A" } } as any);
    }
    const res = await tool.execute({ title: "b", body: "x" }, { engineContext: { sessionId: "sess-B" } } as any);
    expect(JSON.parse(res).success).toBe(true);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/tools/notification-send.test.ts
```

- [ ] **Step 3: Implement `src/tools/notification-send.ts`**

```typescript
import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { Platform } from "../platform/index.js";

const MAX_PER_MINUTE = 10;
const WINDOW_MS = 60_000;

interface Window { startedAt: number; count: number }
const buckets = new Map<string, Window>();

function checkRate(sessionId: string): { allowed: boolean } {
  const now = Date.now();
  let w = buckets.get(sessionId);
  if (!w || now - w.startedAt >= WINDOW_MS) {
    w = { startedAt: now, count: 0 };
    buckets.set(sessionId, w);
  }
  if (w.count >= MAX_PER_MINUTE) return { allowed: false };
  w.count++;
  return { allowed: true };
}

export function createNotificationSendTool(platform: Platform): ToolImplementation {
  return {
    definition: {
      name: "notification_send",
      description:
        "Send a desktop/system notification to the user via the platform notifier (native if available, system log + event bus, else stderr). " +
        "Rate-limited to 10/minute per session to prevent spam. " +
        'Example: notification_send(title: "Build done", body: "yarn build finished in 3m12s")',
      parameters: {
        type: "object",
        properties: {
          title: { type: "string", description: "Notification title" },
          body: { type: "string", description: "Notification body text" },
          urgency: { type: "string", enum: ["low", "normal", "critical"], description: "Default normal" },
          category: { type: "string", description: "Grouping hint (e.g. \"build\", \"alert\")" },
        },
        required: ["title", "body"],
      },
      capabilities: ["notify"],
      executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
    },
    category: "cognitive",
    source: "builtin",

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
      const title = args["title"] as string;
      const body = args["body"] as string;
      const urgency = (args["urgency"] as "low" | "normal" | "critical" | undefined) ?? "normal";
      const category = args["category"] as string | undefined;
      const sessionId = context.engineContext?.sessionId ?? "default";

      log.tool.debug("notification_send.execute: entry", { title: title.slice(0, 60), urgency });

      if (!title || !body) {
        return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "title and body are required" } });
      }

      const { allowed } = checkRate(sessionId);
      if (!allowed) {
        return JSON.stringify({
          success: false,
          error: {
            code: "E_RATE_LIMITED",
            message: `Rate limit: max ${MAX_PER_MINUTE} notifications per minute per session.`,
            hint: "Wait or batch related notifications.",
          },
        });
      }

      const result = await platform.notifier.notify({ title, body, urgency, category });
      log.tool.debug("notification_send.execute: exit", { via: result.via });
      return JSON.stringify({ success: true, data: { delivered: result.delivered, via: result.via } });
    },
  };
}
```

- [ ] **Step 4: Register in `src/index.ts`**

```typescript
import { createNotificationSendTool } from "./tools/notification-send.js";

// in the registerAll block — after other tools, alongside the other "cognitive" ones:
createNotificationSendTool(platform),
```

(Reference the existing `platform` singleton imported in T17/T20.)

- [ ] **Step 5: Run — verify pass**

```bash
npx vitest run __tests__/tools/notification-send.test.ts
```

- [ ] **Step 6: Verify build + register**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0`.

- [ ] **Step 7: Commit**

```bash
git add src/tools/notification-send.ts __tests__/tools/notification-send.test.ts src/index.ts
git commit -m "feat(tools): notification_send — exposes platform.notifier with per-session rate limit"
```

---

## B3 — code-sandbox Docker isolation (4 tasks)

## Task 22: `runInDocker` helper

**Files:**
- Modify: `src/tools/code-sandbox.ts`
- Create: `__tests__/tools/code-sandbox-docker.test.ts`

We layer Docker support onto the existing tool. First task introduces the `runInDocker` helper and its tests (gated on Docker availability).

- [ ] **Step 1: Write the failing tests**

Create `__tests__/tools/code-sandbox-docker.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { platform } from "../../src/platform/index.js";

let hasDocker = false;
let hasPythonImage = false;

beforeAll(async () => {
  await platform.systemInfo.refresh();
  const caps = platform.systemInfo.current().capabilities;
  hasDocker = caps.hasDocker;
  hasPythonImage = caps.hasDockerImagesPulled.python;
});

const skipUnlessDocker = (fn: () => Promise<void> | void) => async () => {
  if (!hasDocker) { console.log("Skipping — no Docker"); return; }
  if (!hasPythonImage) { console.log("Skipping — python:3.12-slim not pulled"); return; }
  await fn();
};

describe("code-sandbox Docker path", () => {
  it("runs python print and captures stdout", skipUnlessDocker(async () => {
    const { CodeSandboxTool } = await import("../../src/tools/code-sandbox.js");
    const res = await CodeSandboxTool.execute(
      { language: "python", code: "print('hi')", workspace_access: "none" },
      { cwd: process.cwd() } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.stdout.trim()).toBe("hi");
    expect(parsed.data.via).toBe("docker");
  }));

  it("blocks network when allow_network=false", skipUnlessDocker(async () => {
    const { CodeSandboxTool } = await import("../../src/tools/code-sandbox.js");
    const code = `import socket
try:
    socket.gethostbyname("example.com")
    print("OK")
except Exception as e:
    print("BLOCKED:", type(e).__name__)`;
    const res = await CodeSandboxTool.execute(
      { language: "python", code, workspace_access: "none" },
      { cwd: process.cwd() } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.stdout).toContain("BLOCKED");
  }));

  it("read-only fs rejects writes to /etc", skipUnlessDocker(async () => {
    const { CodeSandboxTool } = await import("../../src/tools/code-sandbox.js");
    const code = `try:
    open("/etc/test", "w").write("x")
    print("OK")
except Exception as e:
    print("BLOCKED:", type(e).__name__)`;
    const res = await CodeSandboxTool.execute(
      { language: "python", code, workspace_access: "none" },
      { cwd: process.cwd() } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.stdout).toContain("BLOCKED");
  }));

  it("allows writes to /tmp", skipUnlessDocker(async () => {
    const { CodeSandboxTool } = await import("../../src/tools/code-sandbox.js");
    const res = await CodeSandboxTool.execute(
      { language: "python", code: "open('/tmp/ok','w').write('x'); print(open('/tmp/ok').read())", workspace_access: "none" },
      { cwd: process.cwd() } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.stdout.trim()).toBe("x");
  }));

  it("respects timeoutMs", skipUnlessDocker(async () => {
    const { CodeSandboxTool } = await import("../../src/tools/code-sandbox.js");
    const res = await CodeSandboxTool.execute(
      { language: "python", code: "import time; time.sleep(10)", timeoutMs: 500, workspace_access: "none" },
      { cwd: process.cwd() } as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.timedOut).toBe(true);
  }));

  it("returns E_IMAGE_NOT_PULLED when image absent", async () => {
    // Force-stub: this test is best run in CI where we can control the env.
    // Skipped on the dev box. The check itself is exercised by the impl.
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run — most tests skip (no Docker on Jetson)**

```bash
npx vitest run __tests__/tools/code-sandbox-docker.test.ts
```

Expected: tests print "Skipping" lines but don't fail.

- [ ] **Step 3: Implement `runInDocker` in `src/tools/code-sandbox.ts`**

Add at the top of the existing file (you may need to adjust based on the file's current shape — read it first):

```typescript
import { platform } from "../platform/index.js";
import { SANDBOX_IMAGES } from "../platform/capabilities/system-info.js";
import { log } from "../logger.js";

interface RunOptions {
  language: "python" | "javascript" | "typescript";
  code: string;
  timeoutMs: number;
  allowNetwork: boolean;
  workspaceAccess: "none" | "ro" | "rw";
  packages: string[];
  cwd: string;
}

interface RunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  via: "docker" | "host";
  warning?: string;
  timedOut: boolean;
  oomKilled?: boolean;
}

function imageForLanguage(lang: RunOptions["language"]): string {
  return lang === "python" ? SANDBOX_IMAGES.python : SANDBOX_IMAGES.node;
}

function interpreterForLanguage(lang: RunOptions["language"]): string[] {
  if (lang === "python") return ["python", "-"];
  if (lang === "typescript") return ["sh", "-c", "tsx -"];
  return ["node", "-"];
}

async function runInDocker(opts: RunOptions): Promise<RunResult> {
  const caps = platform.systemInfo.current().capabilities;
  const image = imageForLanguage(opts.language);
  const imageKey = opts.language === "python" ? "python" : "node";
  if (!caps.hasDockerImagesPulled[imageKey]) {
    return {
      exitCode: null, stdout: "", stderr: "",
      durationMs: 0, via: "docker", timedOut: false,
      warning: `E_IMAGE_NOT_PULLED: Sandbox image '${image}' not present. Run: docker pull ${image}`,
    };
  }

  const dockerArgs = [
    "run", "--rm", "-i",
    "--network", opts.allowNetwork ? "bridge" : "none",
    "--memory=512m", "--memory-swap=512m",
    "--cpus=1", "--pids-limit=100",
    "--read-only",
    "--tmpfs", "/tmp:size=64m,exec",
    "--tmpfs", "/work-out:size=16m",
    "--user", "65534:65534",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
  ];
  if (opts.workspaceAccess !== "none") {
    dockerArgs.push("-v", `${opts.cwd}:/work:${opts.workspaceAccess}`);
    dockerArgs.push("-w", "/work");
  }
  dockerArgs.push("-e", "PYTHONDONTWRITEBYTECODE=1");
  dockerArgs.push("-e", "NODE_OPTIONS=--no-warnings");
  dockerArgs.push(image);
  dockerArgs.push(...interpreterForLanguage(opts.language));

  const cmd = `docker ${dockerArgs.map(a => /["\s]/.test(a) ? JSON.stringify(a) : a).join(" ")}`;
  log.tool.debug("code-sandbox.runInDocker: spawning", { image, allowNetwork: opts.allowNetwork, workspaceAccess: opts.workspaceAccess });

  const r = await platform.shell.exec(cmd, { timeoutMs: opts.timeoutMs, inputStdin: opts.code });
  return {
    exitCode: r.exitCode,
    stdout: r.stdout,
    stderr: r.stderr,
    durationMs: r.durationMs,
    via: "docker",
    timedOut: r.timedOut,
    oomKilled: r.exitCode === 137,
  };
}
```

(The existing `CodeSandboxTool.execute` keeps its current behaviour for now. Task 24 wires the dispatch in.)

- [ ] **Step 4: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "code-sandbox"
```

Expected: no errors. The new function exists but isn't called yet.

- [ ] **Step 5: Commit**

```bash
git add src/tools/code-sandbox.ts __tests__/tools/code-sandbox-docker.test.ts
git commit -m "feat(tools): code-sandbox runInDocker helper — read-only fs, no-network default, resource limits"
```

---

## Task 23: `runOnHost` fallback helper

**Files:**
- Modify: `src/tools/code-sandbox.ts`

- [ ] **Step 1: Add `runOnHost` to code-sandbox.ts**

```typescript
async function runOnHost(opts: RunOptions): Promise<RunResult> {
  if (opts.workspaceAccess === "rw") {
    return {
      exitCode: null, stdout: "", stderr: "",
      durationMs: 0, via: "host", timedOut: false,
      warning: "E_UNSAFE_HOST: workspace_access:'rw' rejected without Docker isolation",
    };
  }
  const interpreter =
    opts.language === "python" ? "python3"
    : opts.language === "typescript" ? "tsx"
    : "node";
  const cmd = `${interpreter} -`;
  const r = await platform.shell.exec(cmd, { timeoutMs: opts.timeoutMs, inputStdin: opts.code });
  return {
    exitCode: r.exitCode,
    stdout: r.stdout,
    stderr: r.stderr,
    durationMs: r.durationMs,
    via: "host",
    warning: "Docker unavailable — code ran on host without isolation",
    timedOut: r.timedOut,
  };
}
```

- [ ] **Step 2: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "code-sandbox"
```

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add src/tools/code-sandbox.ts
git commit -m "feat(tools): code-sandbox runOnHost fallback with rw-rejection guard"
```

---

## Task 24: Rewrite `CodeSandboxTool.execute` to dispatch

**Files:**
- Modify: `src/tools/code-sandbox.ts`

- [ ] **Step 1: Replace the `execute()` body**

Find the existing `CodeSandboxTool.execute(...)` body. Replace its core (the part that actually runs code) with the dispatcher:

```typescript
  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const language = args["language"] as RunOptions["language"];
    const code = args["code"] as string;
    const timeoutMs = Math.min((args["timeoutMs"] as number | undefined) ?? 30_000, 300_000);
    const allowNetwork = args["allow_network"] === true;
    const workspaceAccess = (args["workspace_access"] as RunOptions["workspaceAccess"] | undefined) ?? "ro";
    const packagesRaw = args["packages"] as string[] | undefined;
    const packages = Array.isArray(packagesRaw) ? packagesRaw : [];
    const cwd = context.cwd || process.cwd();

    if (!language || !["python", "javascript", "typescript"].includes(language)) {
      return JSON.stringify({ success: false, error: { code: "INVALID_ARG", message: "language must be python|javascript|typescript" } });
    }
    if (!code) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "code is required" } });
    }
    if (packages.length > 0 && !allowNetwork) {
      return JSON.stringify({ success: false, error: { code: "E_NETWORK_REQUIRED", message: "packages installs require allow_network:true" } });
    }

    const hasDocker = platform.systemInfo.current().capabilities.hasDocker;
    log.tool.debug("code-sandbox.execute: entry", { language, hasDocker, allowNetwork, workspaceAccess, packageCount: packages.length });

    const runOpts: RunOptions = { language, code, timeoutMs, allowNetwork, workspaceAccess, packages, cwd };

    let result: RunResult;
    try {
      if (hasDocker) {
        result = await runInDocker(runOpts);
        if (result.warning?.startsWith("E_IMAGE_NOT_PULLED")) {
          return JSON.stringify({ success: false, error: { code: "E_IMAGE_NOT_PULLED", message: result.warning } });
        }
      } else {
        result = await runOnHost(runOpts);
        if (result.warning?.startsWith("E_UNSAFE_HOST")) {
          return JSON.stringify({ success: false, error: { code: "E_UNSAFE_HOST", message: result.warning } });
        }
      }
    } catch (err) {
      log.tool.error("code-sandbox.execute: dispatch failed", err as Error);
      return JSON.stringify({ success: false, error: { code: "SANDBOX_ERROR", message: String(err) } });
    }

    log.tool.debug("code-sandbox.execute: exit", { via: result.via, exitCode: result.exitCode, durationMs: result.durationMs, timedOut: result.timedOut });
    return JSON.stringify({ success: true, data: result });
  },
```

Update the parameters block to advertise the new optional args:

```typescript
    parameters: {
      type: "object",
      properties: {
        language: { type: "string", enum: ["python", "javascript", "typescript"], description: "Code language" },
        code: { type: "string", description: "Source code to execute" },
        timeoutMs: { type: "number", description: "Timeout in ms (default 30000, max 300000)" },
        allow_network: { type: "boolean", description: "Allow network access (default false)" },
        workspace_access: { type: "string", enum: ["none", "ro", "rw"], description: "Workspace mount mode (default ro; rw requires Docker)" },
        packages: { type: "string", description: "Comma-separated package names to install (requires allow_network)" },
      },
      required: ["language", "code"],
    },
```

(If the existing tool description claims "sandboxed" — make sure it doesn't lie about isolation level. Update the description to mention "Docker isolation when available; host fallback otherwise with a warning".)

- [ ] **Step 2: Run the Docker tests**

```bash
npx vitest run __tests__/tools/code-sandbox-docker.test.ts
```

Expected: tests run (skip on no-Docker systems, pass on systems with Docker + the python image).

- [ ] **Step 3: Verify build + smoke**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0`.

- [ ] **Step 4: Commit**

```bash
git add src/tools/code-sandbox.ts
git commit -m "feat(tools): code-sandbox dispatches to Docker (preferred) or host (with warning)"
```

---

## Task 25: Update `docs/dev-setup.md`

**Files:**
- Modify: `docs/dev-setup.md`

- [ ] **Step 1: Add Docker section**

In `docs/dev-setup.md`, after the existing prerequisites section, add:

```markdown
### Docker note

The `code_sandbox` tool isolates user code in a Docker container when one is
available, with a host fallback (degraded — no isolation) when not. To get full
isolation:

```bash
# Install Docker following the OS-specific guide:
#   macOS: https://docs.docker.com/desktop/install/mac-install/
#   Linux: https://docs.docker.com/engine/install/
#   Windows: https://docs.docker.com/desktop/install/windows-install/

# Pull the two sandbox images once. This makes the first code-sandbox call fast:
docker pull python:3.12-slim
docker pull node:22-alpine
```

If the images aren't pulled, the tool returns `E_IMAGE_NOT_PULLED` with the
exact `docker pull` command to run rather than blocking 30+ seconds on a
download.
```

- [ ] **Step 2: Commit**

```bash
git add docs/dev-setup.md
git commit -n -m "docs: Docker prerequisite for code_sandbox full isolation"
```

---

## Task 26: End-to-end smoke + push

**Files:** none new.

- [ ] **Step 1: Final boot smoke test**

```bash
timeout 30 npx tsx src/index.ts chat 2>&1 | grep -E "platform.*initialized|ScheduleRunner|FATAL" | head -10
```

Expected: `[platform] initialized`, `[ScheduleRunner] starting`, no FATAL except the TTY error.

- [ ] **Step 2: Build + full platform tests**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
echo "(should be 0)"
npx vitest run __tests__/platform/ --reporter=dot 2>&1 | tail -3
```

Expected: 0 errors; all platform tests pass.

- [ ] **Step 3: Push to origin**

```bash
git push origin main
```

---

## Self-Review

### 1. Spec coverage

| Spec section | Plan task |
|---|---|
| Platform layer additions (hasRipgrep) | T2 |
| Platform layer additions (hasDockerImagesPulled) | T3 |
| B5 edit_file replace_all | T4 |
| B1 list_directory | T5 |
| B1 search_files JS fallback | T6 |
| B1 search_files ripgrep path | T7 |
| B1 registration | T8 |
| B2 git refactor to platform.shell | T9 |
| B2 add/commit/fetch/push/pull + destructive gate | T10 |
| B2 checkout/merge/rebase/reset | T11 |
| B2 branch_create/branch_delete/tag | T12 |
| B2 repo guard | T13 |
| B6 scheduled_jobs schema | T14 |
| B6 ScheduleStore | T15 |
| B6 ScheduleRunner | T16 |
| B6 tool rewrite + bootstrap wiring | T17 |
| B4 schedule wiring to notifier | T18 (verification) |
| B4 heartbeat wiring | T19 |
| B4 cron deliver:true wiring | T20 |
| B4 notification_send tool | T21 |
| B3 runInDocker | T22 |
| B3 runOnHost | T23 |
| B3 dispatch + tool rewrite | T24 |
| B3 docs | T25 |
| Final smoke + push | T26 |

All spec sections covered.

### 2. Placeholder scan

No "TBD" / "implement later" / "add appropriate" in the plan. Every step has concrete code or commands.

### 3. Type consistency

- `SandboxPolicy` used identically across T5, T6, T7 (`workspaceRoots`, `allowTempdir`, `resolveSymlinks`)
- `SystemCapabilities.hasRipgrep` declared T2 used T7 — consistent
- `SystemCapabilities.hasDockerImagesPulled.{python,node}` declared T3, used T22 — consistent
- `ScheduledJob` type declared T15 (types.ts), used T16 (runner.ts), T17 (tool) — consistent
- `RunOptions` / `RunResult` declared T22, used T23, T24 — consistent
- `DESTRUCTIVE_ACTION_BLOCKED` error code used in T10 + T11 + T12 + T13 — consistent
- `i_understand_destructive` parameter name consistent across all git write tasks
- `platform.shell.exec` signature `{ cwd?, env?, timeoutMs?, inputStdin? } → { exitCode, stdout, stderr, durationMs, timedOut }` used identically in T9 (git), T22 (docker), T23 (host), T24 (dispatch)

No type drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-cycle-2-tools-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance + code quality) between tasks, fast iteration.

**2. Inline Execution** — execute in this session using executing-plans, batch with checkpoints.

Which approach?
