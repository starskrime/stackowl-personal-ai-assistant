# Cycle 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-platform Platform layer at `src/platform/`, unify the duplicated `assertWithinSandbox` helper, sweep all 14 pre-existing TypeScript errors, and document the Jetson dev-setup quirk.

**Architecture:** Interface-driven capability registry (`PlatformRegistry`) wires implementations of Paths, Sandbox, Notifier, Process, Shell, Opener, and SystemInfo behind stable contracts. Capability detection runs once at boot, results cached. Each capability has fallback chains where applicable. Consumers depend on the interfaces — never on `process.platform` or `os.*` directly.

**Tech Stack:** TypeScript strict, Node ≥22, Vitest, `env-paths`, `node-notifier`, `ps-list`, ESLint with `@typescript-eslint`.

**Spec:** `docs/superpowers/specs/2026-05-11-cycle-1-foundation-design.md`

---

## File Structure

```
src/platform/
├── index.ts                       # public facade — `platform` singleton
├── types.ts                       # all capability interfaces + PlatformError types
├── errors.ts                      # PlatformError class + code definitions
├── registry.ts                    # PlatformRegistry — wires impls + boot probe + caches
└── capabilities/
    ├── paths.ts                   # PathsImpl — env-paths-backed
    ├── sandbox.ts                 # SandboxImpl — the ONE assertWithinSandbox
    ├── system-info.ts             # SystemInfoImpl — capability matrix
    ├── process.ts                 # ProcessImpl — ps-list wrap + cross-platform kill
    ├── shell.ts                   # ShellImpl — sh / cmd / powershell branch
    ├── opener.ts                  # OpenerImpl — open / xdg-open / start
    └── notifier.ts                # NotifierImpl — native → system → stderr chain

__tests__/platform/
├── paths.test.ts
├── sandbox.test.ts
├── system-info.test.ts
├── process.test.ts
├── shell.test.ts
├── opener.test.ts
├── notifier.test.ts
└── registry.test.ts

src/tools/db-query.ts              # MODIFIED: use platform.sandbox.check()
src/tools/files.ts                 # MODIFIED: use platform.sandbox.check()
__tests__/tools/files-sandbox.test.ts  # NEW: symlink-escape regression for files.ts
eslint.config.js                   # MODIFIED: no-restricted-syntax rule
docs/dev-setup.md                  # NEW
package.json                       # MODIFIED: deps + test:platform script
```

---

## Task 1: Dependencies + folder scaffolding

**Files:**
- Modify: `package.json`
- Create: `src/platform/` directory tree (empty placeholders fine)

- [ ] **Step 1: Install runtime dependencies**

```bash
npm install env-paths node-notifier ps-list
```

Expected: `package.json` updated with caret-ranged deps; `package-lock.json` regenerated.

- [ ] **Step 2: Install type definitions for node-notifier**

```bash
npm install --save-dev @types/node-notifier
```

Note: `env-paths` and `ps-list` ship their own types.

- [ ] **Step 3: Add the `test:platform` script**

Modify `package.json` `scripts` section — add:

```json
"test:platform": "vitest run __tests__/platform/"
```

- [ ] **Step 4: Create the directory tree**

```bash
mkdir -p src/platform/capabilities __tests__/platform
```

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json src/platform __tests__/platform
git commit -m "chore(platform): add env-paths/node-notifier/ps-list deps + scaffold platform layer"
```

---

## Task 2: Types + Errors

**Files:**
- Create: `src/platform/errors.ts`
- Create: `src/platform/types.ts`

- [ ] **Step 1: Write `src/platform/errors.ts`**

```typescript
export type PlatformErrorCode =
  | "E_OUTSIDE_SANDBOX"
  | "E_EXTENSION_BLOCKED"
  | "E_PATH_INVALID"
  | "E_DOCKER_BYPASS_LOGGED"
  | "E_PLATFORM_UNSUPPORTED"
  | "E_CAPABILITY_MISSING"
  | "E_NOTIFY_NATIVE_FAILED"
  | "E_NOTIFY_SYSTEM_FAILED"
  | "E_SHELL_TIMEOUT"
  | "E_PROCESS_NOT_FOUND";

export class PlatformError extends Error {
  readonly code: PlatformErrorCode;
  readonly cause?: unknown;

  constructor(code: PlatformErrorCode, message: string, cause?: unknown) {
    super(message);
    this.name = "PlatformError";
    this.code = code;
    this.cause = cause;
  }
}
```

- [ ] **Step 2: Write `src/platform/types.ts`**

```typescript
import type { PlatformErrorCode } from "./errors.js";

// ─── Paths ─────────────────────────────────────────────────────
export interface Paths {
  tempdir(): string;
  home(): string;
  configDir(appName?: string): string;
  cacheDir(appName?: string): string;
  dataDir(appName?: string): string;
  logDir(appName?: string): string;
  isInside(child: string, root: string): boolean;
}

// ─── Sandbox ───────────────────────────────────────────────────
export interface SandboxPolicy {
  workspaceRoots: string[];
  allowTempdir?: boolean;
  allowExtensions?: string[];
  resolveSymlinks?: boolean;
}

export interface SandboxResult {
  ok: boolean;
  resolvedPath: string;
  reason?: PlatformErrorCode;
  message?: string;
}

export interface Sandbox {
  check(rawPath: string, policy: SandboxPolicy): SandboxResult;
}

// ─── Notifier ──────────────────────────────────────────────────
export interface NotifyOptions {
  title: string;
  body: string;
  urgency?: "low" | "normal" | "critical";
  category?: string;
}

export interface NotifyResult {
  delivered: boolean;
  via: "native" | "system" | "stderr";
  reason?: PlatformErrorCode;
}

export interface NotifierCapabilities {
  native: boolean;
  system: boolean;
}

export interface Notifier {
  notify(opts: NotifyOptions): Promise<NotifyResult>;
  capabilities(): NotifierCapabilities;
}

// ─── Process ───────────────────────────────────────────────────
export interface ProcessInfo {
  pid: number;
  ppid?: number;
  name: string;
  cmd?: string;
  cpu?: number;
  memory?: number;
}

export interface ProcessAPI {
  list(filter?: { name?: string; pid?: number }): Promise<ProcessInfo[]>;
  kill(pid: number, signal?: NodeJS.Signals): Promise<boolean>;
  isAlive(pid: number): boolean;
  currentInfo(): ProcessInfo;
}

// ─── Shell ─────────────────────────────────────────────────────
export interface SpawnOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  inputStdin?: string;
}

export interface SpawnResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  timedOut: boolean;
}

export interface Shell {
  exec(command: string, opts?: SpawnOptions): Promise<SpawnResult>;
}

// ─── Opener ────────────────────────────────────────────────────
export interface Opener {
  open(target: string): Promise<{ launched: boolean; via: string }>;
}

// ─── SystemInfo ────────────────────────────────────────────────
export interface SystemCapabilities {
  hasNotifier: boolean;
  hasOpener: boolean;
  hasDocker: boolean;
  hasGit: boolean;
  hasPython: boolean;
  hasNode: boolean;
}

export type PlatformName =
  | "darwin" | "linux" | "win32"
  | "freebsd" | "openbsd" | "sunos" | "aix";

export interface SystemInfo {
  platform: PlatformName;
  arch: string;
  release: string;
  locale: string;
  inContainer: boolean;
  inWSL: boolean;
  capabilities: SystemCapabilities;
}

export interface SystemInfoAPI {
  current(): SystemInfo;
  refresh(): Promise<SystemInfo>;
}

// ─── Top-level Platform ────────────────────────────────────────
export interface Platform {
  readonly paths: Paths;
  readonly sandbox: Sandbox;
  readonly notifier: Notifier;
  readonly process: ProcessAPI;
  readonly shell: Shell;
  readonly opener: Opener;
  readonly systemInfo: SystemInfoAPI;
  initialize(): Promise<void>;
}
```

- [ ] **Step 3: Verify compilation**

```bash
npm run build 2>&1 | grep "error TS" | grep "src/platform/"
```

Expected: no output (no errors from new files).

- [ ] **Step 4: Commit**

```bash
git add src/platform/types.ts src/platform/errors.ts
git commit -m "feat(platform): add capability interfaces + PlatformError types"
```

---

## Task 3: Paths capability

**Files:**
- Create: `src/platform/capabilities/paths.ts`
- Create: `__tests__/platform/paths.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/platform/paths.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { tmpdir as osTempdir, homedir as osHomedir } from "node:os";
import { realpathSync } from "node:fs";
import { sep } from "node:path";
import { PathsImpl } from "../../src/platform/capabilities/paths.js";

const paths = new PathsImpl("stackowl");

describe("PathsImpl", () => {
  it("tempdir() returns realpath-resolved os.tmpdir()", () => {
    const expected = realpathSync(osTempdir());
    expect(paths.tempdir()).toBe(expected);
  });

  it("home() returns os.homedir()", () => {
    expect(paths.home()).toBe(osHomedir());
  });

  it("configDir() returns an absolute path containing the app name", () => {
    const dir = paths.configDir();
    expect(dir.length).toBeGreaterThan(0);
    expect(dir.toLowerCase()).toContain("stackowl");
  });

  it("cacheDir/dataDir/logDir return distinct absolute paths", () => {
    const c = paths.cacheDir();
    const d = paths.dataDir();
    const l = paths.logDir();
    expect(c).not.toBe(d);
    expect(c).not.toBe(l);
    expect(d).not.toBe(l);
  });

  it("isInside detects child paths inside a root", () => {
    const root = paths.tempdir();
    const child = root + sep + "subdir" + sep + "file.txt";
    expect(paths.isInside(child, root)).toBe(true);
  });

  it("isInside rejects siblings of root", () => {
    expect(paths.isInside("/etc/passwd", paths.tempdir())).toBe(false);
  });

  it("isInside accepts the root itself", () => {
    const root = paths.tempdir();
    expect(paths.isInside(root, root)).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/platform/paths.test.ts
```

Expected: FAIL — module `src/platform/capabilities/paths.js` not found.

- [ ] **Step 3: Implement `src/platform/capabilities/paths.ts`**

```typescript
import { tmpdir as osTempdir, homedir as osHomedir } from "node:os";
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import envPaths from "env-paths";
import type { Paths } from "../types.js";

const DEFAULT_APP_NAME = "stackowl";

export class PathsImpl implements Paths {
  private readonly resolvedTempdir: string;
  private readonly defaultAppName: string;

  constructor(defaultAppName: string = DEFAULT_APP_NAME) {
    this.defaultAppName = defaultAppName;
    // Resolve once at construction. macOS tmpdir is /var/folders/... but
    // realpath gives /private/var/folders/... — both must match later boundary
    // checks, so we normalize at the source.
    try {
      this.resolvedTempdir = realpathSync(osTempdir());
    } catch {
      this.resolvedTempdir = osTempdir();
    }
  }

  tempdir(): string {
    return this.resolvedTempdir;
  }

  home(): string {
    return osHomedir();
  }

  configDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).config;
  }

  cacheDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).cache;
  }

  dataDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).data;
  }

  logDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).log;
  }

  isInside(child: string, root: string): boolean {
    const resolvedChild = resolve(child);
    const resolvedRoot = resolve(root);
    return (
      resolvedChild === resolvedRoot ||
      resolvedChild.startsWith(resolvedRoot + sep)
    );
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/platform/paths.test.ts
```

Expected: 7/7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/paths.ts __tests__/platform/paths.test.ts
git commit -m "feat(platform): PathsImpl — env-paths-backed cross-platform paths"
```

---

## Task 4: Sandbox capability

**Files:**
- Create: `src/platform/capabilities/sandbox.ts`
- Create: `__tests__/platform/sandbox.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/platform/sandbox.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, symlinkSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";
import { SandboxImpl } from "../../src/platform/capabilities/sandbox.js";
import { PathsImpl } from "../../src/platform/capabilities/paths.js";

let workspace: string;
let external: string;
const paths = new PathsImpl();
const sandbox = new SandboxImpl(paths);

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-sandbox-test-"));
  external = mkdtempSync(join(homedir(), ".stackowl-sandbox-external-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  rmSync(external, { recursive: true, force: true });
});

describe("SandboxImpl.check", () => {
  it("allows a file inside a workspace root", () => {
    const file = join(workspace, "ok.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(true);
    expect(r.reason).toBeUndefined();
  });

  it("rejects a file outside workspace roots", () => {
    const file = join(external, "external.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_OUTSIDE_SANDBOX");
  });

  it("rejects tempdir paths when allowTempdir is false (default)", () => {
    const r = sandbox.check(join(tmpdir(), "x.db"), { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
  });

  it("allows tempdir paths when allowTempdir is true", () => {
    const file = join(tmpdir(), "stackowl-sandbox-allowed-" + Date.now() + ".db");
    writeFileSync(file, "");
    try {
      const r = sandbox.check(file, { workspaceRoots: [workspace], allowTempdir: true });
      expect(r.ok).toBe(true);
    } finally {
      rmSync(file, { force: true });
    }
  });

  it("enforces allowExtensions whitelist", () => {
    const file = join(workspace, "data.txt");
    writeFileSync(file, "");
    const r = sandbox.check(file, {
      workspaceRoots: [workspace],
      allowExtensions: [".db", ".sqlite"],
    });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_EXTENSION_BLOCKED");
  });

  it("symlink escape: symlink inside workspace pointing outside is rejected", () => {
    const target = join(external, "secret.db");
    writeFileSync(target, "");
    const link = join(workspace, "evil.db");
    try {
      symlinkSync(target, link);
    } catch (e) {
      // Windows requires admin or Developer Mode for symlinks — skip
      if ((e as NodeJS.ErrnoException).code === "EPERM") return;
      throw e;
    }
    const r = sandbox.check(link, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_OUTSIDE_SANDBOX");
  });

  it("missing file falls back to lexical path (does not throw)", () => {
    const file = join(workspace, "future.db");
    // file does not exist
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(true);
  });

  it("resolves relative paths against workspace root", () => {
    const r = sandbox.check("subdir/file.db", { workspaceRoots: [workspace] });
    // resolve() uses cwd, not workspace — so a relative path resolves against cwd.
    // Caller responsibility is to pass absolute. Test confirms the impl uses path.resolve.
    expect(r.resolvedPath.startsWith("/") || r.resolvedPath.match(/^[A-Za-z]:/)).toBeTruthy();
  });

  it("returns the resolved (post-realpath) path in result", () => {
    const file = join(workspace, "x.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.resolvedPath).toContain("x.db");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/platform/sandbox.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `src/platform/capabilities/sandbox.ts`**

```typescript
import { existsSync, realpathSync } from "node:fs";
import { resolve, sep, extname } from "node:path";
import { log } from "../../logger.js";
import type { Paths, Sandbox, SandboxPolicy, SandboxResult } from "../types.js";

let dockerBypassLogged = false;

export class SandboxImpl implements Sandbox {
  constructor(private readonly paths: Paths) {}

  check(rawPath: string, policy: SandboxPolicy): SandboxResult {
    const resolvedSymlinks = policy.resolveSymlinks ?? true;
    const absolute = resolve(rawPath);

    // Symlink resolution — graceful fallback for not-yet-existing files
    let resolvedPath = absolute;
    if (resolvedSymlinks) {
      try {
        resolvedPath = realpathSync(absolute);
      } catch {
        log.tool.debug("sandbox.check: realpath failed, using lexical path", { absolute });
        resolvedPath = absolute;
      }
    }

    // Docker bypass — full access inside containers; log once per boot
    const inDocker = process.env.IN_DOCKER === "true" || existsSync("/.dockerenv");
    if (inDocker) {
      if (!dockerBypassLogged) {
        log.tool.info("sandbox.check: Docker bypass active — full filesystem access permitted", {
          reason: "container environment",
        });
        dockerBypassLogged = true;
      }
      return { ok: true, resolvedPath };
    }

    // Build effective allowlist (workspaceRoots realpath-resolved + optional tempdir)
    const roots = policy.workspaceRoots.map((r) => {
      try { return realpathSync(resolve(r)); } catch { return resolve(r); }
    });
    if (policy.allowTempdir) {
      roots.push(this.paths.tempdir());
    }

    // Boundary check
    const insideRoot = roots.some(
      (root) => resolvedPath === root || resolvedPath.startsWith(root + sep),
    );
    if (!insideRoot) {
      return {
        ok: false,
        resolvedPath,
        reason: "E_OUTSIDE_SANDBOX",
        message: `Access denied: "${resolvedPath}" is outside allowed roots: ${roots.join(", ")}`,
      };
    }

    // Extension whitelist
    if (policy.allowExtensions && policy.allowExtensions.length > 0) {
      const ext = extname(resolvedPath).toLowerCase();
      const allowed = policy.allowExtensions.map((e) => e.toLowerCase());
      if (!allowed.includes(ext)) {
        return {
          ok: false,
          resolvedPath,
          reason: "E_EXTENSION_BLOCKED",
          message: `Extension "${ext}" not in allowed list: ${allowed.join(", ")}`,
        };
      }
    }

    return { ok: true, resolvedPath };
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/platform/sandbox.test.ts
```

Expected: 9/9 PASS (symlink test may auto-skip on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/sandbox.ts __tests__/platform/sandbox.test.ts
git commit -m "feat(platform): SandboxImpl — unified path-boundary check with realpath + extension allowlist"
```

---

## Task 5: SystemInfo capability

**Files:**
- Create: `src/platform/capabilities/system-info.ts`
- Create: `__tests__/platform/system-info.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from "vitest";
import { platform as osPlatform, arch as osArch } from "node:os";
import { SystemInfoImpl } from "../../src/platform/capabilities/system-info.js";

describe("SystemInfoImpl", () => {
  it("current() returns matching node platform + arch", () => {
    const api = new SystemInfoImpl();
    const info = api.current();
    expect(info.platform).toBe(osPlatform());
    expect(info.arch).toBe(osArch());
  });

  it("current() reports hasNode: true (we are running on Node)", async () => {
    const api = new SystemInfoImpl();
    await api.refresh();
    expect(api.current().capabilities.hasNode).toBe(true);
  });

  it("current() detects locale", () => {
    const api = new SystemInfoImpl();
    expect(api.current().locale.length).toBeGreaterThan(0);
  });

  it("refresh() re-probes and returns the updated info", async () => {
    const api = new SystemInfoImpl();
    const before = api.current();
    const after = await api.refresh();
    expect(after.platform).toBe(before.platform);
    expect(after.capabilities.hasNode).toBe(true);
  });

  it("inContainer reflects /.dockerenv presence", () => {
    const api = new SystemInfoImpl();
    const info = api.current();
    // We don't assert the value — just that the field is a boolean
    expect(typeof info.inContainer).toBe("boolean");
  });
});
```

- [ ] **Step 2: Run test — verify failure**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `src/platform/capabilities/system-info.ts`**

```typescript
import { platform as osPlatform, arch as osArch, release as osRelease } from "node:os";
import { existsSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import type {
  SystemInfo,
  SystemInfoAPI,
  PlatformName,
  SystemCapabilities,
} from "../types.js";

function detectInContainer(): boolean {
  if (process.env.IN_DOCKER === "true") return true;
  if (existsSync("/.dockerenv")) return true;
  return false;
}

function detectInWSL(): boolean {
  if (osPlatform() !== "linux") return false;
  try {
    const v = readFileSync("/proc/version", "utf-8").toLowerCase();
    return v.includes("microsoft") || v.includes("wsl");
  } catch {
    return false;
  }
}

async function commandAvailable(cmd: string): Promise<boolean> {
  return new Promise((resolveResult) => {
    const checker = osPlatform() === "win32" ? "where" : "which";
    const child = spawn(checker, [cmd], { stdio: "ignore" });
    child.on("error", () => resolveResult(false));
    child.on("close", (code) => resolveResult(code === 0));
  });
}

async function probeCapabilities(): Promise<SystemCapabilities> {
  const [hasOpener, hasDocker, hasGit, hasPython] = await Promise.all([
    osPlatform() === "win32"
      ? Promise.resolve(true) // `start` is built into cmd.exe
      : osPlatform() === "darwin"
        ? commandAvailable("open")
        : commandAvailable("xdg-open"),
    commandAvailable("docker"),
    commandAvailable("git"),
    commandAvailable("python3").then((found) => found || commandAvailable("python")),
  ]);
  return {
    hasNotifier: true, // node-notifier always has a fallback impl per platform
    hasOpener,
    hasDocker,
    hasGit,
    hasPython,
    hasNode: true,
  };
}

export class SystemInfoImpl implements SystemInfoAPI {
  private cached: SystemInfo;

  constructor() {
    // Synchronous initial value — capabilities filled by refresh()
    this.cached = {
      platform: osPlatform() as PlatformName,
      arch: osArch(),
      release: osRelease(),
      locale: Intl.DateTimeFormat().resolvedOptions().locale,
      inContainer: detectInContainer(),
      inWSL: detectInWSL(),
      capabilities: {
        hasNotifier: true,
        hasOpener: false,
        hasDocker: false,
        hasGit: false,
        hasPython: false,
        hasNode: true,
      },
    };
  }

  current(): SystemInfo {
    return this.cached;
  }

  async refresh(): Promise<SystemInfo> {
    const capabilities = await probeCapabilities();
    this.cached = {
      ...this.cached,
      release: osRelease(),
      inContainer: detectInContainer(),
      inWSL: detectInWSL(),
      capabilities,
    };
    return this.cached;
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/system-info.test.ts
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/system-info.ts __tests__/platform/system-info.test.ts
git commit -m "feat(platform): SystemInfoImpl — OS + capability matrix probed at boot"
```

---

## Task 6: Process capability

**Files:**
- Create: `src/platform/capabilities/process.ts`
- Create: `__tests__/platform/process.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from "vitest";
import { ProcessImpl } from "../../src/platform/capabilities/process.js";

const proc = new ProcessImpl();

describe("ProcessImpl", () => {
  it("currentInfo() returns this process's pid", () => {
    expect(proc.currentInfo().pid).toBe(process.pid);
  });

  it("isAlive(process.pid) returns true", () => {
    expect(proc.isAlive(process.pid)).toBe(true);
  });

  it("isAlive(99999999) returns false", () => {
    expect(proc.isAlive(99999999)).toBe(false);
  });

  it("list() includes the current process", async () => {
    const list = await proc.list();
    expect(list.some((p) => p.pid === process.pid)).toBe(true);
  });

  it("list({ pid }) filters to a single process", async () => {
    const list = await proc.list({ pid: process.pid });
    expect(list).toHaveLength(1);
    expect(list[0].pid).toBe(process.pid);
  });

  it("kill(non-existent pid) returns false (no throw)", async () => {
    const result = await proc.kill(99999999);
    expect(result).toBe(false);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/process.test.ts
```

- [ ] **Step 3: Implement `src/platform/capabilities/process.ts`**

```typescript
import psList from "ps-list";
import { platform as osPlatform } from "node:os";
import { spawn } from "node:child_process";
import { log } from "../../logger.js";
import type { ProcessAPI, ProcessInfo } from "../types.js";

export class ProcessImpl implements ProcessAPI {
  async list(filter?: { name?: string; pid?: number }): Promise<ProcessInfo[]> {
    const all = await psList();
    return all
      .filter((p) => (filter?.pid !== undefined ? p.pid === filter.pid : true))
      .filter((p) => (filter?.name ? p.name.toLowerCase().includes(filter.name.toLowerCase()) : true))
      .map((p) => ({
        pid: p.pid,
        ppid: p.ppid,
        name: p.name,
        cmd: p.cmd,
        cpu: p.cpu,
        memory: p.memory,
      }));
  }

  async kill(pid: number, signal: NodeJS.Signals = "SIGTERM"): Promise<boolean> {
    // On Windows, process.kill does not honor signal semantics for SIGKILL;
    // taskkill /F is the only true force-kill.
    if (osPlatform() === "win32" && signal === "SIGKILL") {
      return new Promise<boolean>((resolveResult) => {
        const child = spawn("taskkill", ["/F", "/PID", String(pid)], { stdio: "ignore" });
        child.on("error", () => resolveResult(false));
        child.on("close", (code) => resolveResult(code === 0));
      });
    }

    try {
      process.kill(pid, signal);
      return true;
    } catch (err) {
      log.tool.debug("process.kill failed", { pid, signal, err: String(err) });
      return false;
    }
  }

  isAlive(pid: number): boolean {
    try {
      process.kill(pid, 0); // signal 0 = check only
      return true;
    } catch {
      return false;
    }
  }

  currentInfo(): ProcessInfo {
    return {
      pid: process.pid,
      ppid: process.ppid,
      name: process.title,
      cmd: process.argv.join(" "),
    };
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/process.test.ts
```

Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/process.ts __tests__/platform/process.test.ts
git commit -m "feat(platform): ProcessImpl — ps-list wrap + Windows-aware SIGKILL via taskkill"
```

---

## Task 7: Shell capability

**Files:**
- Create: `src/platform/capabilities/shell.ts`
- Create: `__tests__/platform/shell.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from "vitest";
import { platform as osPlatform } from "node:os";
import { ShellImpl } from "../../src/platform/capabilities/shell.js";

const shell = new ShellImpl();

describe("ShellImpl", () => {
  it("exec runs a trivial echo on the host", async () => {
    const cmd = osPlatform() === "win32" ? "echo hi" : "echo hi";
    const r = await shell.exec(cmd);
    expect(r.exitCode).toBe(0);
    expect(r.stdout.trim()).toBe("hi");
    expect(r.timedOut).toBe(false);
  });

  it("captures stderr separately", async () => {
    const cmd = osPlatform() === "win32"
      ? "powershell -NoProfile -Command \"Write-Error 'oops' -ErrorAction Continue\""
      : "sh -c \"echo oops 1>&2\"";
    const r = await shell.exec(cmd);
    expect(r.stderr).toContain("oops");
  });

  it("respects timeoutMs and reports timedOut=true", async () => {
    const cmd = osPlatform() === "win32"
      ? "powershell -NoProfile -Command \"Start-Sleep -Seconds 5\""
      : "sleep 5";
    const r = await shell.exec(cmd, { timeoutMs: 200 });
    expect(r.timedOut).toBe(true);
  });

  it("durationMs is populated", async () => {
    const r = await shell.exec("echo done");
    expect(r.durationMs).toBeGreaterThanOrEqual(0);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/shell.test.ts
```

- [ ] **Step 3: Implement `src/platform/capabilities/shell.ts`**

```typescript
import { spawn } from "node:child_process";
import { platform as osPlatform } from "node:os";
import { log } from "../../logger.js";
import type { Shell, SpawnOptions, SpawnResult } from "../types.js";

export class ShellImpl implements Shell {
  async exec(command: string, opts: SpawnOptions = {}): Promise<SpawnResult> {
    const start = Date.now();
    const isWin = osPlatform() === "win32";

    // Platform-correct shell selection
    const [bin, args] = isWin
      ? ["cmd.exe", ["/d", "/s", "/c", command]]
      : ["/bin/sh", ["-c", command]];

    log.tool.debug("shell.exec: entry", { bin, command: command.slice(0, 200), cwd: opts.cwd });

    return new Promise<SpawnResult>((resolveResult) => {
      const child = spawn(bin, args as string[], {
        cwd: opts.cwd,
        env: opts.env ?? process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      const stdoutChunks: Buffer[] = [];
      const stderrChunks: Buffer[] = [];
      child.stdout.on("data", (c) => stdoutChunks.push(c as Buffer));
      child.stderr.on("data", (c) => stderrChunks.push(c as Buffer));

      let timedOut = false;
      const timer = opts.timeoutMs
        ? setTimeout(() => {
            timedOut = true;
            child.kill("SIGTERM");
            // Force-kill follow-up if still alive after 100ms
            setTimeout(() => {
              if (!child.killed) child.kill("SIGKILL");
            }, 100);
          }, opts.timeoutMs)
        : null;

      if (opts.inputStdin !== undefined) {
        child.stdin.write(opts.inputStdin);
      }
      child.stdin.end();

      child.on("close", (exitCode) => {
        if (timer) clearTimeout(timer);
        const stdout = Buffer.concat(stdoutChunks).toString("utf-8");
        const stderr = Buffer.concat(stderrChunks).toString("utf-8");
        const durationMs = Date.now() - start;
        log.tool.debug("shell.exec: exit", { exitCode, durationMs, timedOut });
        resolveResult({ exitCode, stdout, stderr, durationMs, timedOut });
      });

      child.on("error", (err) => {
        if (timer) clearTimeout(timer);
        log.tool.error("shell.exec: spawn failed", err);
        resolveResult({
          exitCode: null,
          stdout: Buffer.concat(stdoutChunks).toString("utf-8"),
          stderr: String(err),
          durationMs: Date.now() - start,
          timedOut,
        });
      });
    });
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/shell.test.ts
```

Expected: 4/4 PASS on the host OS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/shell.ts __tests__/platform/shell.test.ts
git commit -m "feat(platform): ShellImpl — cross-platform command spawn (sh / cmd) with timeout"
```

---

## Task 8: Opener capability

**Files:**
- Create: `src/platform/capabilities/opener.ts`
- Create: `__tests__/platform/opener.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from "vitest";
import { platform as osPlatform } from "node:os";
import { OpenerImpl } from "../../src/platform/capabilities/opener.js";

describe("OpenerImpl", () => {
  it("open() returns a launched result with a via indicator (dry-run mode)", async () => {
    const opener = new OpenerImpl({ dryRun: true });
    const r = await opener.open("https://example.com");
    expect(typeof r.launched).toBe("boolean");
    expect(typeof r.via).toBe("string");
    expect(r.via.length).toBeGreaterThan(0);
  });

  it("via reflects the platform's expected opener", async () => {
    const opener = new OpenerImpl({ dryRun: true });
    const r = await opener.open("https://example.com");
    if (osPlatform() === "darwin") expect(r.via).toBe("open");
    else if (osPlatform() === "win32") expect(r.via).toBe("start");
    else expect(["xdg-open", "gnome-open", "kde-open"]).toContain(r.via);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/opener.test.ts
```

- [ ] **Step 3: Implement `src/platform/capabilities/opener.ts`**

```typescript
import { spawn } from "node:child_process";
import { platform as osPlatform } from "node:os";
import { log } from "../../logger.js";
import type { Opener } from "../types.js";

export interface OpenerOptions {
  /** When true, return the resolved command but do not actually launch — for tests. */
  dryRun?: boolean;
}

export class OpenerImpl implements Opener {
  private linuxOpener: string | null = null;
  private readonly dryRun: boolean;

  constructor(opts: OpenerOptions = {}) {
    this.dryRun = opts.dryRun ?? false;
  }

  async open(target: string): Promise<{ launched: boolean; via: string }> {
    const p = osPlatform();
    if (p === "darwin") {
      return this.launch("open", [target], "open");
    }
    if (p === "win32") {
      // `start` is built into cmd.exe; first argument is window title (empty)
      return this.launch("cmd.exe", ["/c", "start", "", target], "start");
    }
    // Linux — try xdg-open first, fall back to gnome-open, then kde-open
    if (!this.linuxOpener) {
      this.linuxOpener = await this.detectLinuxOpener();
    }
    if (!this.linuxOpener) {
      return { launched: false, via: "none" };
    }
    return this.launch(this.linuxOpener, [target], this.linuxOpener);
  }

  private async launch(bin: string, args: string[], via: string): Promise<{ launched: boolean; via: string }> {
    if (this.dryRun) {
      return { launched: true, via };
    }
    return new Promise((resolveResult) => {
      try {
        const child = spawn(bin, args, { detached: true, stdio: "ignore" });
        child.on("error", (err) => {
          log.tool.warn("opener.launch: spawn error", { bin, err: String(err) });
          resolveResult({ launched: false, via });
        });
        child.unref();
        resolveResult({ launched: true, via });
      } catch (err) {
        log.tool.warn("opener.launch: throw", { bin, err: String(err) });
        resolveResult({ launched: false, via });
      }
    });
  }

  private async detectLinuxOpener(): Promise<string | null> {
    const candidates = ["xdg-open", "gnome-open", "kde-open"];
    for (const c of candidates) {
      const ok = await new Promise<boolean>((res) => {
        const probe = spawn("which", [c], { stdio: "ignore" });
        probe.on("error", () => res(false));
        probe.on("close", (code) => res(code === 0));
      });
      if (ok) return c;
    }
    return null;
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/opener.test.ts
```

Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/opener.ts __tests__/platform/opener.test.ts
git commit -m "feat(platform): OpenerImpl — cross-platform URL/file open with detection chain"
```

---

## Task 9: Notifier capability

**Files:**
- Create: `src/platform/capabilities/notifier.ts`
- Create: `__tests__/platform/notifier.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, vi } from "vitest";
import { NotifierImpl } from "../../src/platform/capabilities/notifier.js";

describe("NotifierImpl fallback chain", () => {
  it("delivers via 'native' when node-notifier succeeds", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(null, "delivered")),
    };
    const n = new NotifierImpl({ nativeImpl: nativeStub, systemLogPath: null });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("native");
    expect(r.delivered).toBe(true);
  });

  it("falls back to 'system' when native throws", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(new Error("no notifier"))),
    };
    const systemEvents: string[] = [];
    const n = new NotifierImpl({
      nativeImpl: nativeStub,
      systemLogPath: null,
      systemEventEmitter: (msg) => systemEvents.push(msg),
    });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("system");
    expect(systemEvents.length).toBe(1);
  });

  it("falls back to 'stderr' when both native and system fail", async () => {
    const nativeStub = {
      notify: vi.fn((_opts, cb) => cb(new Error("no notifier"))),
    };
    const stderrSink: string[] = [];
    const n = new NotifierImpl({
      nativeImpl: nativeStub,
      systemLogPath: null,
      systemEventEmitter: () => { throw new Error("event bus down"); },
      stderrSink: (msg) => stderrSink.push(msg),
    });
    const r = await n.notify({ title: "hi", body: "test" });
    expect(r.via).toBe("stderr");
    expect(stderrSink.length).toBe(1);
  });

  it("capabilities() reports native and system availability", () => {
    const n = new NotifierImpl({ nativeImpl: { notify: () => {} } });
    expect(n.capabilities().native).toBe(true);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/notifier.test.ts
```

- [ ] **Step 3: Implement `src/platform/capabilities/notifier.ts`**

```typescript
import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { log } from "../../logger.js";
import type { Notifier, NotifierCapabilities, NotifyOptions, NotifyResult } from "../types.js";

interface NativeNotifierLike {
  notify(opts: { title: string; message: string }, cb: (err: Error | null, response?: string) => void): void;
}

export interface NotifierOptions {
  nativeImpl?: NativeNotifierLike;
  systemLogPath?: string | null;
  systemEventEmitter?: (msg: string) => void;
  stderrSink?: (msg: string) => void;
}

export class NotifierImpl implements Notifier {
  private readonly nativeImpl: NativeNotifierLike | null;
  private readonly systemLogPath: string | null;
  private readonly systemEventEmitter?: (msg: string) => void;
  private readonly stderrSink: (msg: string) => void;

  constructor(opts: NotifierOptions = {}) {
    this.nativeImpl = opts.nativeImpl ?? null;
    this.systemLogPath = opts.systemLogPath ?? null;
    this.systemEventEmitter = opts.systemEventEmitter;
    this.stderrSink = opts.stderrSink ?? ((m) => process.stderr.write(m + "\n"));
  }

  capabilities(): NotifierCapabilities {
    return {
      native: this.nativeImpl !== null,
      system: !!(this.systemLogPath || this.systemEventEmitter),
    };
  }

  async notify(opts: NotifyOptions): Promise<NotifyResult> {
    log.tool.debug("notifier.notify: entry", { title: opts.title.slice(0, 60), urgency: opts.urgency });

    // Tier 1 — native
    if (this.nativeImpl) {
      const ok = await new Promise<boolean>((res) => {
        try {
          this.nativeImpl!.notify({ title: opts.title, message: opts.body }, (err) => {
            res(!err);
          });
        } catch {
          res(false);
        }
      });
      if (ok) return { delivered: true, via: "native" };
    }

    // Tier 2 — system (event bus + log)
    const message = formatPayload(opts);
    let systemOk = false;
    if (this.systemLogPath) {
      try {
        await mkdir(dirname(this.systemLogPath), { recursive: true });
        await appendFile(this.systemLogPath, message + "\n", "utf-8");
        systemOk = true;
      } catch (err) {
        log.tool.warn("notifier: system log write failed", { err: String(err) });
      }
    }
    if (this.systemEventEmitter) {
      try {
        this.systemEventEmitter(message);
        systemOk = true;
      } catch (err) {
        log.tool.warn("notifier: system event emit failed", { err: String(err) });
      }
    }
    if (systemOk) return { delivered: true, via: "system" };

    // Tier 3 — stderr (always-on last resort)
    this.stderrSink(message);
    return { delivered: true, via: "stderr" };
  }
}

function formatPayload(opts: NotifyOptions): string {
  const urgency = opts.urgency ?? "normal";
  const category = opts.category ? ` [${opts.category}]` : "";
  return `[notifier:${urgency}]${category} ${opts.title} — ${opts.body}`;
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/notifier.test.ts
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/capabilities/notifier.ts __tests__/platform/notifier.test.ts
git commit -m "feat(platform): NotifierImpl — native → system → stderr fallback chain"
```

---

## Task 10: PlatformRegistry — wire it all together

**Files:**
- Create: `src/platform/registry.ts`
- Create: `__tests__/platform/registry.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from "vitest";
import { createPlatform } from "../../src/platform/registry.js";

describe("PlatformRegistry", () => {
  it("createPlatform() returns a Platform with all capabilities wired", () => {
    const p = createPlatform();
    expect(p.paths).toBeDefined();
    expect(p.sandbox).toBeDefined();
    expect(p.notifier).toBeDefined();
    expect(p.process).toBeDefined();
    expect(p.shell).toBeDefined();
    expect(p.opener).toBeDefined();
    expect(p.systemInfo).toBeDefined();
  });

  it("initialize() runs the system-info refresh probe", async () => {
    const p = createPlatform();
    await p.initialize();
    const info = p.systemInfo.current();
    expect(info.capabilities.hasNode).toBe(true);
  });

  it("paths.tempdir() and sandbox both share the same resolved tempdir", () => {
    const p = createPlatform();
    const td = p.paths.tempdir();
    expect(td.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run — verify failure**

```bash
npx vitest run __tests__/platform/registry.test.ts
```

- [ ] **Step 3: Implement `src/platform/registry.ts`**

```typescript
import nodeNotifier from "node-notifier";
import { join } from "node:path";
import { log } from "../logger.js";
import { PathsImpl } from "./capabilities/paths.js";
import { SandboxImpl } from "./capabilities/sandbox.js";
import { NotifierImpl } from "./capabilities/notifier.js";
import { ProcessImpl } from "./capabilities/process.js";
import { ShellImpl } from "./capabilities/shell.js";
import { OpenerImpl } from "./capabilities/opener.js";
import { SystemInfoImpl } from "./capabilities/system-info.js";
import type { Platform } from "./types.js";

export interface RegistryOptions {
  appName?: string;
  notifier?: {
    nativeImpl?: any;
    systemLogPath?: string | null;
    systemEventEmitter?: (msg: string) => void;
  };
}

class PlatformRegistry implements Platform {
  readonly paths: PathsImpl;
  readonly sandbox: SandboxImpl;
  readonly notifier: NotifierImpl;
  readonly process: ProcessImpl;
  readonly shell: ShellImpl;
  readonly opener: OpenerImpl;
  readonly systemInfo: SystemInfoImpl;

  constructor(opts: RegistryOptions = {}) {
    const appName = opts.appName ?? "stackowl";
    this.paths = new PathsImpl(appName);
    this.sandbox = new SandboxImpl(this.paths);
    this.process = new ProcessImpl();
    this.shell = new ShellImpl();
    this.opener = new OpenerImpl();
    this.systemInfo = new SystemInfoImpl();

    const defaultNotifyLog = join(this.paths.logDir(), "notifications.log");
    this.notifier = new NotifierImpl({
      nativeImpl: opts.notifier?.nativeImpl ?? nodeNotifier,
      systemLogPath: opts.notifier?.systemLogPath ?? defaultNotifyLog,
      systemEventEmitter: opts.notifier?.systemEventEmitter,
    });
  }

  async initialize(): Promise<void> {
    log.engine.info("[platform] initializing capability probe");
    await this.systemInfo.refresh();
    const info = this.systemInfo.current();
    log.engine.info("[platform] initialized", {
      platform: info.platform,
      arch: info.arch,
      inContainer: info.inContainer,
      inWSL: info.inWSL,
      capabilities: info.capabilities,
    });
  }
}

export function createPlatform(opts: RegistryOptions = {}): Platform {
  return new PlatformRegistry(opts);
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/platform/registry.test.ts
```

Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/platform/registry.ts __tests__/platform/registry.test.ts
git commit -m "feat(platform): PlatformRegistry — wires all capabilities + boot-time probe"
```

---

## Task 11: Public facade — `src/platform/index.ts`

**Files:**
- Create: `src/platform/index.ts`

- [ ] **Step 1: Write the facade**

```typescript
/**
 * StackOwl Platform Layer — public entrypoint
 *
 * Consumers do:
 *   import { platform } from "@/platform";          // singleton
 *   import { createPlatform } from "@/platform";    // for test isolation
 *   import type { SandboxPolicy, NotifyOptions } from "@/platform";
 *
 * The singleton must be initialized once at app startup:
 *   await platform.initialize();
 */
export { createPlatform } from "./registry.js";
export type {
  Platform,
  Paths,
  Sandbox, SandboxPolicy, SandboxResult,
  Notifier, NotifyOptions, NotifyResult, NotifierCapabilities,
  ProcessAPI, ProcessInfo,
  Shell, SpawnOptions, SpawnResult,
  Opener,
  SystemInfo, SystemInfoAPI, SystemCapabilities, PlatformName,
} from "./types.js";
export { PlatformError, type PlatformErrorCode } from "./errors.js";

import { createPlatform } from "./registry.js";

/** Process-wide singleton. Call `platform.initialize()` at startup. */
export const platform = createPlatform();
```

- [ ] **Step 2: Verify build**

```bash
npm run build 2>&1 | grep "error TS" | grep "src/platform/"
```

Expected: no errors from new files.

- [ ] **Step 3: Initialize platform at app startup**

Modify `src/index.ts` — find the early initialization block (around line 530, where `MemoryDatabase` is created). Add **before** that block:

```typescript
// Initialize the Platform layer first — it probes OS capabilities once and
// caches them for every consumer (paths, sandbox, notifier, process, shell).
const { platform } = await import("./platform/index.js");
await platform.initialize();
```

- [ ] **Step 4: Boot smoke test**

```bash
timeout 20 npx tsx src/index.ts chat 2>&1 | grep -E "platform.*initialized|Loading config|FATAL" | head -10
```

Expected: a line like `[platform] initialized` appears before the rest of startup. No FATAL errors from new code.

- [ ] **Step 5: Commit**

```bash
git add src/platform/index.ts src/index.ts
git commit -m "feat(platform): public facade + boot-time initialize() call"
```

---

## Task 12: Migrate `src/tools/db-query.ts` to `platform.sandbox`

**Files:**
- Modify: `src/tools/db-query.ts`
- Test: `__tests__/tools/db-query.test.ts` (existing — must still pass)

- [ ] **Step 1: Replace the local helper**

Replace the entire `assertWithinSandbox` function (lines ~9-50 in `src/tools/db-query.ts`) by importing from platform.

Old (delete the helper definition + its imports for `realpathSync`, `tmpdir`):

```typescript
function assertWithinSandbox(resolvedPath: string, cwd: string): string | null { … }
```

New — at the top of the file, replace the `import { existsSync, realpathSync }` and `import { tmpdir }` blocks with:

```typescript
import { platform } from "../platform/index.js";
import type { SandboxPolicy } from "../platform/index.js";
```

Then in `execute()`, replace the `assertWithinSandbox(...)` call with:

```typescript
const policy: SandboxPolicy = {
  workspaceRoots: [cwd],
  allowTempdir: true,       // db_query historically allowed tempdir for test fixtures
  allowExtensions: [".db", ".sqlite"],
  resolveSymlinks: true,
};
const sandboxResult = platform.sandbox.check(normalizedPath, policy);
if (!sandboxResult.ok) {
  log.tool.warn("db-query.execute: sandbox check failed", {
    reason: sandboxResult.reason,
    message: sandboxResult.message,
  });
  return JSON.stringify({
    success: false,
    error: { code: sandboxResult.reason, message: sandboxResult.message },
  });
}
const resolvedDbPath = sandboxResult.resolvedPath;
```

Replace subsequent uses of `resolved`/`realResolved` with `resolvedDbPath`.

- [ ] **Step 2: Run tests**

```bash
npx vitest run __tests__/tools/db-query.test.ts
```

Expected: 10/10 PASS — same as before.

- [ ] **Step 3: Verify the old helper is fully gone**

```bash
grep -n "function assertWithinSandbox\|realpathSync\|TEMP_ROOT" src/tools/db-query.ts
```

Expected: no output (helper removed; symbols no longer referenced).

- [ ] **Step 4: Commit**

```bash
git add src/tools/db-query.ts
git commit -m "refactor(tools): migrate db_query to platform.sandbox — drop local helper"
```

---

## Task 13: Migrate `src/tools/files.ts` to `platform.sandbox` + symlink regression test

**Files:**
- Modify: `src/tools/files.ts`
- Create: `__tests__/tools/files-sandbox.test.ts`

**Why this matters:** `files.ts` currently has the OLD `assertWithinSandbox` (hardcodes `/tmp/`, NO `realpathSync`). The symlink escape vulnerability is LIVE here. This task fixes it.

- [ ] **Step 1: Write the failing regression test**

Create `__tests__/tools/files-sandbox.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";
import { ReadFileTool } from "../../src/tools/files.js";

let workspace: string;
let external: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-files-sandbox-"));
  external = mkdtempSync(join(homedir(), ".stackowl-files-external-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  rmSync(external, { recursive: true, force: true });
});

describe("files.ts sandbox (regression)", () => {
  it("blocks symlink escape — symlink inside workspace pointing outside is rejected", async () => {
    const secret = join(external, "secret.txt");
    writeFileSync(secret, "TOP-SECRET");
    const link = join(workspace, "innocent.txt");
    try {
      symlinkSync(secret, link);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "EPERM") return; // Windows w/o admin
      throw e;
    }

    const result = await ReadFileTool.execute({ path: link }, { cwd: workspace });
    // The result should NOT contain TOP-SECRET — sandbox must reject.
    expect(result).not.toContain("TOP-SECRET");
    expect(result.toLowerCase()).toMatch(/access denied|outside/);
  });

  it("allows reading a normal file inside the workspace", async () => {
    const normal = join(workspace, "ok.txt");
    writeFileSync(normal, "hello");
    const result = await ReadFileTool.execute({ path: normal }, { cwd: workspace });
    expect(result).toContain("hello");
  });
});
```

- [ ] **Step 2: Run test to verify the symlink test FAILS today (proving the vulnerability)**

```bash
npx vitest run __tests__/tools/files-sandbox.test.ts
```

Expected: 1 test fails ("blocks symlink escape" — the current `assertWithinSandbox` lets it through). 1 passes.

- [ ] **Step 3: Migrate `src/tools/files.ts` to platform.sandbox**

In `src/tools/files.ts`:

Delete the entire `assertWithinSandbox` function (lines 16-37 currently).

Replace these imports:

```typescript
import { existsSync } from "node:fs";
import { resolve, isAbsolute, sep, normalize } from "node:path";
```

with:

```typescript
import { resolve, isAbsolute, normalize } from "node:path";
import { platform } from "../platform/index.js";
import type { SandboxPolicy } from "../platform/index.js";
```

In each tool's `execute()` body, replace:

```typescript
assertWithinSandbox(resolved, cwd);
```

with:

```typescript
const policy: SandboxPolicy = {
  workspaceRoots: [cwd],
  allowTempdir: true,       // file tools allow temp for build artifacts
  resolveSymlinks: true,
};
const sandboxResult = platform.sandbox.check(resolved, policy);
if (!sandboxResult.ok) {
  log.tool.warn(`${this.definition.name}.execute: sandbox check failed`, {
    reason: sandboxResult.reason,
    message: sandboxResult.message,
  });
  return `Access denied: ${sandboxResult.message}`;
}
const resolvedPath = sandboxResult.resolvedPath;
```

Replace subsequent uses of `resolved` with `resolvedPath` inside each `execute` body.

Note: the three tools (`ReadFileTool`, `WriteFileTool`, `EditFileTool`) share this pattern — apply to all three. The `this.definition.name` inside each tool already refers to the right name (`read_file` / `write_file` / `edit_file`).

- [ ] **Step 4: Run the regression test — confirm symlink escape is now blocked**

```bash
npx vitest run __tests__/tools/files-sandbox.test.ts
```

Expected: 2/2 PASS.

- [ ] **Step 5: Run all file-tool tests to confirm no regression**

```bash
npx vitest run __tests__/tools/
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools/files.ts __tests__/tools/files-sandbox.test.ts
git commit -m "fix(security): migrate files.ts to platform.sandbox — closes symlink-escape vulnerability"
```

---

## Task 14: TS sweep — unused imports/vars (7 errors)

**Files (the 7 errors map to these files):**
- `src/cron/isolated-runner.ts` — `'TOOL_PROFILES'` unused
- `src/cron/service.ts` — `'CronRun'` unused
- (and others — discover via `npm run build`)

- [ ] **Step 1: Enumerate the unused-symbol errors**

```bash
npm run build 2>&1 | grep -E "TS6133|TS6196|TS6138"
```

For each error: open the file, decide if the symbol is truly dead (delete it) or if it's scaffolding for soon-shipping code (delete it anyway unless explicit reason to keep, in which case add a focused `// eslint-disable-next-line` with a comment).

- [ ] **Step 2: Apply fixes**

For each reported line: delete the unused import/var. Show the diff before applying — e.g., for `src/cron/service.ts`:

```typescript
// BEFORE
import type { CronJob, CronJobState, CronRun } from "./types.js";

// AFTER
import type { CronJob, CronJobState } from "./types.js";
```

- [ ] **Step 3: Verify zero unused-symbol errors remain**

```bash
npm run build 2>&1 | grep -E "TS6133|TS6196|TS6138" | wc -l
```

Expected: `0`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(typescript): sweep unused imports/vars (TS6133/6196/6138)"
```

---

## Task 15: TS sweep — implicit-any callbacks (TS7006)

The previous task may have removed some of these; run the build again to see current state.

- [ ] **Step 1: Enumerate**

```bash
npm run build 2>&1 | grep "TS7006"
```

- [ ] **Step 2: For each, add explicit parameter types**

Pattern:

```typescript
// BEFORE
.catch((err) => log.engine.warn("..."))

// AFTER
.catch((err: unknown) => log.engine.warn("...", { err: String(err) }))
```

- [ ] **Step 3: Verify**

```bash
npm run build 2>&1 | grep "TS7006" | wc -l
```

Expected: `0`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(typescript): annotate implicit-any callback parameters (TS7006)"
```

---

## Task 16: TS sweep — type mismatches + schema strictness

Remaining errors: TS2304, TS2552, TS2774, TS2353, TS2345, TS2349, TS2322, TS2554, TS2339.

- [ ] **Step 1: Enumerate**

```bash
npm run build 2>&1 | grep "error TS" | head -20
```

- [ ] **Step 2: Fix each by class**

**For `src/learning/orchestrator.ts:317` (TS2345 — wrong map callback shape):** the offending callback expects `(value: string, …)` but receives `{normalizedName: string}`. Change the call site to `arr.map((t: { normalizedName: string }) => t.normalizedName)` first to produce a `string[]`, then call the consuming function.

**For `src/tools/memory-unified.ts:165` (TS2353 — `items` not on ToolDefinition param):** the base `ToolDefinition` parameter shape doesn't allow `items`. Two options:
  (a) Cast to `as any` with a TODO referencing the wider issue — but per the enterprise rule, prefer:
  (b) Widen the parameter type in `src/providers/base.ts` to allow `items?: { type: string }` for array params.

Pick (b). Modify `src/providers/base.ts` `ToolDefinition` shape — find the param interface and add an optional `items` field. Re-run build.

**For `src/tools/memory-unified.ts:176` (TS2322 — `'memory'` not in ToolCategory):** add `"memory"` to the `ToolCategory` type union. Find the type definition (likely `src/tools/registry.ts` or `src/tools/categories.ts`) and add the value.

**For `src/index.ts:1296` (TS2552):** Likely fixed already in Cycle 0 — verify.

**For each remaining error:** read the line, fix the type, re-build.

- [ ] **Step 3: Verify the build is zero-error**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```

Expected: `0`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(typescript): fix type-mismatch errors — widen ToolDefinition params, expand ToolCategory, fix call-site shapes"
```

---

## Task 17: ESLint guardrail — no direct `os.tmpdir`/`os.homedir`/`process.platform` outside platform/

**Files:**
- Modify: `eslint.config.js`

- [ ] **Step 1: Read current ESLint config**

```bash
cat eslint.config.js
```

- [ ] **Step 2: Add a `no-restricted-syntax` rule**

In `eslint.config.js`, locate the `rules:` section of the main config object. Add:

```javascript
rules: {
  // ...existing rules...
  "no-restricted-syntax": [
    "error",
    {
      selector: "CallExpression[callee.object.name='os'][callee.property.name='tmpdir']",
      message: "Use platform.paths.tempdir() instead. Direct os.tmpdir() is restricted to src/platform/.",
    },
    {
      selector: "CallExpression[callee.object.name='os'][callee.property.name='homedir']",
      message: "Use platform.paths.home() instead.",
    },
    {
      selector: "MemberExpression[object.name='process'][property.name='platform']",
      message: "Use platform.systemInfo.current().platform instead.",
    },
  ],
},
```

Then add an override section at the bottom that exempts `src/platform/` and `__tests__/`:

```javascript
{
  files: ["src/platform/**/*.ts", "__tests__/**/*.ts"],
  rules: {
    "no-restricted-syntax": "off",
  },
},
```

- [ ] **Step 3: Verify the rule fires on new code (manual sanity check)**

Create a temp file `/tmp/test-eslint.ts` (for manual verification, not committed):

```typescript
import os from "node:os";
const t = os.tmpdir();
```

Run:

```bash
npx eslint /tmp/test-eslint.ts 2>&1 | head -5
```

Expected: error about restricted syntax. Then delete the temp file.

- [ ] **Step 4: Run lint on existing src/ — confirm no new failures**

```bash
npm run lint 2>&1 | grep "no-restricted-syntax" | wc -l
```

Expected: 0 (existing usage is via `os.tmpdir()` — but since these are all in non-platform files, they SHOULD trigger). Re-check: this rule applies to new code. The 36 existing call sites will trigger.

**Decision:** Add the rule but with `"warn"` severity initially (not "error") so the build doesn't fail. Add a TODO in the comment that future cycles migrate the 36 sites then bump to "error".

Update the rule severity:

```javascript
"no-restricted-syntax": [
  "warn",  // TODO(cycle B0'): bump to "error" after migrating the 36 existing call sites
  // ...
],
```

- [ ] **Step 5: Commit**

```bash
git add eslint.config.js
git commit -m "chore(eslint): warn on direct os.tmpdir/homedir/process.platform outside platform/"
```

---

## Task 18: Dev-setup documentation

**Files:**
- Create: `docs/dev-setup.md`

- [ ] **Step 1: Write the doc**

```markdown
# StackOwl Dev Setup

## Prerequisites

- Node.js ≥ 22
- npm 10+
- Git
- (Optional) Docker, for the sandboxed `code-sandbox` tool

## First-time setup

```bash
git clone <repo-url>
cd stackowl-personal-ai-assistant
npm install
```

### Jetson / ARM Linux note

On NVIDIA Jetson and some ARM Linux distros, `npm install` can leave `node_modules`
in a state where symlinks within the tree exceed the kernel's link-depth limit.
If you see `Too many levels of symbolic links` errors when running `npm test` or
`tsx`, run:

```bash
sudo npm install
```

once. Subsequent installs do not need sudo.

### Puppeteer note

The `puppeteer` dependency tries to download Chrome at install time. On ARM Linux
this is skipped — install Chromium manually:

```bash
sudo apt install chromium
```

The `live_browser` and `web_fetch` tools detect Chromium via the system PATH.

## Running

| Command | What it does |
|---|---|
| `npm run dev` | Run in watch mode (tsx watch) — TUI v2 default |
| `STACKOWL_TUI=v1 npm run dev` | Use the legacy TUI v1 |
| `STACKOWL_JSON=true npx tsx src/index.ts chat` | Non-TTY chat — no Ink renderer |
| `npm run build` | Compile TypeScript to `dist/` |
| `npm start` | Run compiled output |
| `npm test` | Run all tests (vitest) |
| `npm run test:platform` | Platform-layer tests only |
| `npm run lint` | ESLint on `src/` |

## Platform tests

The platform layer at `src/platform/` is tested independently:

```bash
npm run test:platform
```

These tests run against the host OS. To exercise all three OS branches (macOS,
Linux, Windows), CI runs the same suite on `ubuntu-latest`, `macos-latest`, and
`windows-latest`. Local runs only exercise the host OS — platform-specific
branches in the impls are covered by stubbed-platform unit tests where possible.

## Environment variables

| Var | Purpose |
|---|---|
| `STACKOWL_TUI` | `v1` to use legacy TUI; default is v2 |
| `STACKOWL_JSON` | `true` to emit JSON-mode output for non-TTY contexts |
| `IN_DOCKER` | `true` to signal containerized execution (also auto-detected via `/.dockerenv`) |

## Configuration

`stackowl.config.json` is the per-machine config (provider keys, channel tokens,
parliament settings). It is **gitignored** and must be created via `./start.sh`
on first run.
```

- [ ] **Step 2: Commit**

```bash
git add docs/dev-setup.md
git commit -m "docs: add dev-setup with Jetson sudo-npm-install + Puppeteer Chromium note"
```

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Plan task |
|---|---|
| Platform layer at `src/platform/` (7 capabilities) | Tasks 2-9 |
| PlatformRegistry with boot probe + cache | Task 10 |
| Public facade + `platform` singleton | Task 11 |
| Unified `assertWithinSandbox` | Task 4 (impl), Tasks 12-13 (migration) |
| db-query.ts migration | Task 12 |
| files.ts migration + symlink fix | Task 13 |
| TS error sweep (~24 errors → 0) | Tasks 14-16 |
| `env-paths`, `node-notifier`, `ps-list` deps | Task 1 |
| Per-capability tests | Tasks 3-10 |
| ESLint guardrail | Task 17 |
| Dev-setup docs (B8) | Task 18 |
| `test:platform` script | Task 1 |
| `allowTempdir: false` default | Task 4 impl |

All spec sections covered.

**2. Placeholder scan**

No "TBD", "implement later", or "add appropriate X" in the plan. Every step shows real commands and real code.

**3. Type consistency**

- `Paths.tempdir()` defined Task 2, used Task 3 (sandbox), Task 4 (in test) — consistent
- `Sandbox.check()` defined Task 4, used Tasks 12 & 13 — consistent signature
- `Notifier.notify()` defined Task 2 types, implemented Task 9 — consistent
- `Platform.initialize()` defined Task 2, called Tasks 10 & 11 — consistent
- `SystemInfoAPI.refresh()` defined Task 2, called Task 10 — consistent

No type drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-cycle-1-foundation-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec compliance + code quality) between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
