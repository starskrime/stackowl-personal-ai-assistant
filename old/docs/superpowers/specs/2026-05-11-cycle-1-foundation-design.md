# Cycle 1 — Foundation: Platform Layer + Unified Sandbox + TS Sweep

**Date:** 2026-05-11
**Owner:** Bakir
**Status:** Approved (sections 1-7 confirmed via brainstorming dialogue)

## Goal

Build the foundation that the rest of Option B (Tools, Safety cycles) sits on:

1. A new **Platform layer** at `src/platform/` that gives every OS-touching consumer a single, capability-probed, fallback-chained API for paths, sandboxing, notifications, processes, shell spawning, opener, and system info. Mac/Windows/Linux first-class.
2. **Unify the duplicated `assertWithinSandbox`** helper currently scattered across `src/tools/db-query.ts` and `src/tools/files.ts` into one shared module — eliminating the symlink-escape vulnerability that still exists in `files.ts`.
3. Sweep the ~24 pre-existing TypeScript errors so the build is clean and CI can fail-on-error.
4. Document the dev-setup quirk (`sudo npm install` requirement on this Jetson) so future contributors don't hit the same wall.

## Non-goals

- **Migrating every existing `os.tmpdir()` / `process.platform` call site** to the new platform layer. There are 36 such call sites. Cycle 1 ships the new module + the unification fix. Cycles 2 & 3 (and a later B0' cleanup pass) migrate the rest opportunistically.
- **Cycle 2 (file-search + git writes + edit replace_all)** and **Cycle 3 (Docker code-sandbox + schedule durability + cross-platform notifications)** are separate deliveries with their own specs.

## Architecture

### Top-level shape

```
src/platform/
├── index.ts                  # Public facade — re-exports the registered Platform instance
├── types.ts                  # Capability interfaces
├── registry.ts               # PlatformRegistry — wires impls, caches probe results, exposes typed errors
├── errors.ts                 # PlatformError + typed error codes
├── capabilities/
│   ├── paths.ts              # tempdir, homedir, configDir, cacheDir, dataDir (env-paths-backed)
│   ├── sandbox.ts            # the ONE assertWithinSandbox (realpath-aware, allowlist-based)
│   ├── notifier.ts           # native (node-notifier) → system (event bus) → stderr fallback chain
│   ├── process.ts            # ps-list wrap + cross-platform kill/isAlive/currentInfo
│   ├── shell.ts              # platform-correct command spawning (sh vs cmd vs powershell)
│   ├── opener.ts             # open URL/file in default app (open / xdg-open / start)
│   └── system-info.ts        # OS, arch, release, locale, inContainer, inWSL, capability matrix
└── __tests__/
    ├── sandbox.test.ts       # realpath escape, ext rejection, Docker bypass, Win paths
    ├── notifier.test.ts      # fallback-chain test with injected node-notifier stub
    ├── paths.test.ts         # branches per platform
    ├── process.test.ts       # isAlive, listProcesses, kill (where safe)
    └── system-info.test.ts   # capability matrix probe
```

### Design principles

Per `feedback_enterprise_architecture` memory rule:

- **Interface-driven**: every capability has a contract in `types.ts`. Implementations register through `PlatformRegistry`. Consumers depend on the interface, not the impl.
- **Capability probing at boot**: detection runs once on `PlatformRegistry.initialize()`. Results cached. A `refresh()` exists for explicit re-detection (e.g., after install of a missing CLI). No per-call probing.
- **Fallback chains**: each capability that can fail defines an explicit degradation path. UX impact documented per fallback.
- **Structured errors**: `PlatformError extends Error` with `.code: PlatformErrorCode` and `.cause?: unknown`. Never bare strings.
- **Test doubles**: every external dependency (node-notifier, ps-list, env-paths) is injectable via the registry. Test mode swaps in stubs.
- **Cross-platform first**: no `darwin`-only, `linux`-only, or `win32`-only behaviour in shared code. Every code path runs on every supported platform — branches inside the capability impls, never bubbled up to consumers.
- **4-point logging** at entry / decision / step / exit per capability call.

## Capability contracts

### `Paths`

```ts
interface Paths {
  tempdir(): string;          // realpath-resolved once at boot
  home(): string;
  configDir(appName?: string): string;   // env-paths-backed; defaults to "stackowl"
  cacheDir(appName?: string): string;
  dataDir(appName?: string): string;
  logDir(appName?: string): string;
  isInside(child: string, root: string): boolean;  // post-realpath boundary check
}
```

Implementation: wraps `env-paths` (5KB, zero-dep). All return paths are realpath-resolved absolute. `isInside` is the canonical path-comparison helper — used by `sandbox.ts` and never re-implemented.

### `Sandbox`

```ts
interface SandboxPolicy {
  workspaceRoots: string[];       // explicit allowlist roots; realpath-resolved on policy construction
  allowTempdir?: boolean;          // default false (security-sensitive). callers opt in to allow tempdir.
  allowExtensions?: string[];      // e.g. [".db", ".sqlite"] for db_query. Empty = any.
  resolveSymlinks?: boolean;       // default true — symlink-escape prevention. Off only for trusted callers.
}

type PlatformErrorCode =
  | "E_OUTSIDE_SANDBOX"
  | "E_EXTENSION_BLOCKED"
  | "E_PATH_INVALID"
  | "E_DOCKER_BYPASS_LOGGED"
  | "E_PLATFORM_UNSUPPORTED"
  | "E_CAPABILITY_MISSING";

interface SandboxResult {
  ok: boolean;
  resolvedPath: string;            // realpath-resolved (or lexical fallback when file doesn't exist yet)
  reason?: PlatformErrorCode;
  message?: string;                // human-readable, includes the resolved path and allowed roots
}

interface Sandbox {
  check(rawPath: string, policy: SandboxPolicy): SandboxResult;
}
```

Behaviour:

- `path.resolve(rawPath)` first to expand relative paths against the caller's cwd.
- `realpathSync(resolved)` to defeat symlink escapes; on `ENOENT` (file doesn't exist yet) fall back to the lexical resolved path with a log warning at debug level.
- Boundary check: post-realpath `startsWith(root + sep) || === root` for each root.
- `allowExtensions` enforced after boundary so LLM sees the more meaningful error first.
- Docker bypass: `/.dockerenv` exists OR `IN_DOCKER=true` env. **Logged at info level once per boot**, not per call.
- Returns `SandboxResult` — never throws. Callers translate to their preferred error shape (string for tool returns, throw for library code).

**`allowTempdir` defaults to `false`** — security-sensitive default. Tools that legitimately need temp access (test fixtures, build artifacts) opt in explicitly. This is a deliberate change from the current behavior where `/tmp` was implicitly allowed everywhere.

### `Notifier`

```ts
interface NotifyOptions {
  title: string;
  body: string;
  urgency?: "low" | "normal" | "critical";
  category?: string;       // for grouping (e.g., "reminder", "task-complete", "alert")
}

interface NotifyResult {
  delivered: boolean;
  via: "native" | "system" | "stderr";  // which tier succeeded
  reason?: PlatformErrorCode;
}

interface Notifier {
  notify(opts: NotifyOptions): Promise<NotifyResult>;
  capabilities(): { native: boolean; system: boolean };
}
```

Fallback chain (highest fidelity → lowest):

1. **Native** — `node-notifier` (handles macOS `osascript`, Linux `notify-send`, Windows Toast under one API). On failure, log the underlying error code and proceed.
2. **System** — append to `~/.stackowl/notifications.log` AND emit `notifier:fallback` to the gateway event bus so connected channels (Telegram/Discord/WhatsApp) can pick up critical notifications.
3. **Stderr** — last-resort plain-text write. Guaranteed visible for CLI/headless users.

Each tier logs entry/exit with the result tier; `via` is the answer to "which path actually delivered."

### `Process`

```ts
interface ProcessInfo {
  pid: number;
  ppid?: number;
  name: string;
  cmd?: string;
  cpu?: number;
  memory?: number;
}

interface ProcessAPI {
  list(filter?: { name?: string; pid?: number }): Promise<ProcessInfo[]>;
  kill(pid: number, signal?: NodeJS.Signals): Promise<boolean>;  // true if signal delivered
  isAlive(pid: number): boolean;                                  // process.kill(pid, 0) wrap
  currentInfo(): ProcessInfo;
}
```

Implementation: `ps-list` for `list()`. `process.kill` for Unix; on Windows, `SIGKILL` is simulated via `taskkill /F /PID <pid>` so true force-kill semantics match user expectation. `isAlive` uses the `process.kill(pid, 0)` no-signal trick wrapped to return boolean.

### `Shell`

```ts
interface SpawnOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  inputStdin?: string;
}

interface SpawnResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  timedOut: boolean;
}

interface Shell {
  exec(command: string, opts?: SpawnOptions): Promise<SpawnResult>;
}
```

Platform-aware shell selection:
- `darwin` / `linux`: `/bin/sh -c <cmd>`
- `win32`: `cmd.exe /d /s /c <cmd>` for `.cmd`/`.bat`/`.exe`; `powershell.exe -NoProfile -Command <cmd>` for scripts

`exec` is the cross-platform command runner that Cycle 2 (`git_tool` writes), Cycle 3 (`code-sandbox` host fallback), and anywhere else we shell out will consume.

### `Opener`

```ts
interface Opener {
  open(target: string): Promise<{ launched: boolean; via: string }>;
}
```

- macOS: `open <target>`
- Linux: `xdg-open` → `gnome-open` → `kde-open` (each probed via `which` once at boot)
- Windows: `start "" <target>` via `cmd.exe`

### `SystemInfo`

```ts
interface SystemCapabilities {
  hasNotifier: boolean;
  hasOpener: boolean;
  hasDocker: boolean;
  hasGit: boolean;
  hasPython: boolean;
  hasNode: boolean;
}

interface SystemInfo {
  platform: "darwin" | "linux" | "win32" | "freebsd" | "openbsd" | "sunos" | "aix";
  arch: string;
  release: string;
  locale: string;
  inContainer: boolean;
  inWSL: boolean;
  capabilities: SystemCapabilities;
}

interface SystemInfoAPI {
  current(): SystemInfo;
  refresh(): Promise<SystemInfo>;
}
```

Probed once at boot via `PlatformRegistry.initialize()`. Capability matrix lives here — single source of truth for "can we do X on this machine?". `refresh()` re-runs the probes (useful after `apt install` or similar mid-session).

## Sandbox unification — concrete migration

### Before (current state)

- `src/tools/db-query.ts:assertWithinSandbox()` — has `realpathSync`, uses `os.tmpdir()` (just fixed)
- `src/tools/files.ts:assertWithinSandbox()` — duplicate; hardcodes `/tmp/`, no `realpathSync` — **symlink-escape vulnerable**

### After

Both files import from `@platform/capabilities/sandbox`:

```ts
import { platform } from "@/platform";

const policy: SandboxPolicy = {
  workspaceRoots: [context.cwd ?? process.cwd()],
  allowTempdir: true,        // file tools: tests/temp builds need this
  // db-query: prefer allowTempdir: false in prod; opt in only for test contexts
  allowExtensions: [],       // file tools: any extension OK; db-query: [".db", ".sqlite"]
  resolveSymlinks: true,
};

const result = platform.sandbox.check(resolvedPath, policy);
if (!result.ok) {
  return JSON.stringify({ success: false, error: { code: result.reason, message: result.message } });
}
```

### Migration steps

1. Build `src/platform/capabilities/sandbox.ts` + tests.
2. Update `db-query.ts` to call `platform.sandbox.check()` instead of its local helper.
3. Update `files.ts` to call `platform.sandbox.check()` — **this is the fix** for the symlink-escape vulnerability that's currently live in production.
4. Delete both local `assertWithinSandbox` functions.
5. Verify every existing test passes; add a symlink-escape regression test for the `files.ts` consumer.

## TypeScript error sweep

Group the errors by class (run `npm run build` to enumerate; current count is ~24):

| Class | Examples | Approach |
|---|---|---|
| Unused imports/vars | TS6133, TS6196 | Remove if truly unused; if scaffolding for soon-shipping code, add `// eslint-disable-next-line @typescript-eslint/no-unused-vars` with a TODO link. |
| Implicit any callbacks | TS7006 | Add explicit `(err: unknown)`, `(value: string, index: number)`, etc. |
| Shadow scoping / missing imports | TS2304, TS2552, TS2774 | Case-by-case. The `index.ts:1296` case during Cycle 0 was this class. |
| Strict JSON schema | TS2353, TS2322 | Either relax base `ToolDefinition` type once with discriminated union, OR fix the offending tool definitions. Pick the path that touches fewer files. |

One commit per class (4 commits total) so `git blame` reads clean. After the sweep: `npm run build` reports zero errors. Optionally add `"strict": true` enforcement to CI (gate change for a follow-up).

## Testing strategy

### Per capability

- **paths**: assert `tempdir()` equals `realpathSync(os.tmpdir())`. Stub `process.platform` to `darwin`, `linux`, `win32` in turn, assert each `configDir()` resolves to the right OS-canonical root.
- **sandbox**: full matrix — workspace allow, workspace deny, tempdir allow (opt-in), tempdir deny (default), symlink-escape attempt, extension whitelist hit/miss, Docker bypass detection, missing-file lexical fallback, Windows-style `C:\\…` paths. Use `mkdtempSync` + real symlinks.
- **notifier**: inject a stub `node-notifier` factory that returns success / failure. Confirm `via: "native"` on success, `via: "system"` when native fails and event bus is reachable, `via: "stderr"` when both fail.
- **process**: list current process, assert `current.pid === process.pid`. `isAlive(process.pid)` true. `isAlive(99999)` false. `kill(child.pid, "SIGTERM")` after spawning a node child.
- **shell**: platform-branched. `exec("echo hi")` on darwin/linux; `exec("Write-Output hi")` on win32 (skipped at runtime if not on Windows). All assert `exitCode === 0`, `stdout.trim() === "hi"`.
- **opener**: dry-run mode — assert the right command would be spawned, don't actually open anything.
- **system-info**: `current()` returns the expected `platform` matching `process.platform`. `capabilities.hasNode` always true (we're running on Node). `inContainer` matches `/.dockerenv` presence.

### Integration

A single `test:platform` script runs all platform tests. Locally runs against the host OS. CI runs on `ubuntu-latest`, `macos-latest`, `windows-latest`.

### Gates

- All new tests pass.
- All existing tests still pass (no regressions).
- `npm run build` reports zero TS errors after Cycle 1 sweep.

## Dev environment doc (B8)

New file: `docs/dev-setup.md`. Captures:

- `node_modules` ownership quirk on this Jetson — `sudo npm install` once if you see `EACCES` or symlink-loop errors.
- How to verify `npm test` passes locally.
- Where the platform-layer tests live and how to run them per-OS.
- The `STACKOWL_JSON=true` env var for non-TTY chat mode.

## Dependencies added

- `env-paths` (~5KB, zero-dep) — OS-correct config/cache/data dirs
- `node-notifier` (~150KB) — cross-platform native notifications
- `ps-list` (~10KB, zero-dep) — cross-platform process enumeration

All are widely-used, MIT-licensed, mature (5+ years stable). Each is replaceable behind the Platform capability interfaces if we need to swap implementations later.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `node-notifier` requires native binaries on some Linux distros | Capability probe falls back to system/stderr tier automatically. UX degrades gracefully — never crashes. |
| `realpathSync` on a never-existing path throws ENOENT | Caught; falls back to lexical path with a debug log. Documented in sandbox contract. |
| Migrating only 2 files (db-query, files) leaves 36 other `os.tmpdir`/`process.platform` call sites | Out of scope for Cycle 1. ESLint rule (or pre-commit grep) prevents *new* direct usage; later cleanup cycle migrates the rest. |
| Tests can't run in this dev env (`sudo npm install` needed) | Documented in `dev-setup.md`. CI runs on clean fresh installs so tests are guaranteed to execute somewhere. |
| `allowTempdir: false` default tightens behavior — existing callers may regress | Migration step explicitly audits each caller; tests confirm. Tools that need temp access opt in (file tools yes, db-query optionally). |

## Deliverables

1. `src/platform/` module with all 7 capability impls + types + registry + errors.
2. `src/tools/db-query.ts` and `src/tools/files.ts` migrated to use `platform.sandbox.check()` — old local helpers deleted.
3. Full `__tests__/platform/` test suite covering every capability.
4. ~24 pre-existing TS errors fixed across the codebase, grouped into 4 commits by error class.
5. `docs/dev-setup.md` documenting the `sudo npm install` quirk and platform test runs.
6. `package.json` adds `env-paths`, `node-notifier`, `ps-list` (caret ranges).
7. ESLint rule (`no-restricted-syntax`) flagging new direct `os.tmpdir()` / `os.homedir()` / `process.platform` usage outside `src/platform/`. Prevents regression: the 36 existing call sites stay until a future cleanup cycle, but no new ones can be added.

## Out of scope (deferred to later cycles)

- Migration of the other 36 `process.platform`/`os.tmpdir()` call sites (B0' cleanup pass).
- Cycle 2 contents (B1, B5, B2).
- Cycle 3 contents (B3, B6, B4) — though Cycle 3's notification work will consume `platform.notifier` directly.
