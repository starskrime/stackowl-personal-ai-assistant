# Cycle 2 — Tools + Safety + Durability

**Date:** 2026-05-11
**Owner:** Bakir
**Status:** Approved (sections 1-10 confirmed via brainstorming dialogue)

## Goal

Close the remaining 6 audit gaps from the StackOwl competitive analysis on top of the Cycle 1 Platform layer. Six items, all enterprise-grade, all cross-platform (macOS/Linux/Windows):

| Item | What |
|---|---|
| B5 | `edit_file` gains `replace_all: boolean` parameter |
| B1 | Two new tools: `list_directory` (flat/recursive/glob) and `search_files` (hybrid ripgrep + JS fallback) |
| B2 | `git_tool` gains write operations (add/commit/checkout/push/pull/merge/rebase/reset/branch_create/branch_delete/tag/fetch) |
| B6 | `schedule.ts` durability — promote to `src/schedule/` module with SQLite-backed `ScheduleStore` and boot-time hydration |
| B4 | Wire `schedule.runner` and `heartbeat/proactive` to `platform.notifier`; add new `notification_send` tool exposing the notifier to the LLM |
| B3 | `code-sandbox.ts` actual Docker isolation with non-root, no-network-by-default, resource-limited containers; host fallback when Docker unavailable |

## Non-goals

- Migration of the 36 existing `os.tmpdir()` / `process.platform` call sites — covered by future B0' cleanup cycle. ESLint guardrail from Cycle 1 prevents new ones.
- Skill marketplace (ClawHub competitor) — 6+ months work, out of Cycle scope.
- Multi-agent inter-session messaging primitive.
- Voice wake / native mobile apps.
- Tree-sitter repo maps.

## Architecture

```
src/tools/
├── files.ts                        # MODIFIED — EditFileTool.replace_all (B5)
├── filesystem/
│   ├── list-directory.ts           # NEW (B1)
│   └── search-files.ts             # NEW (B1)
├── code-sandbox.ts                 # REWRITTEN — Docker isolation + host fallback (B3)
├── dev/git.ts                      # EXTENDED — write actions + destructive gating (B2)
├── notification-send.ts            # NEW — exposes platform.notifier to LLM (B4)
└── schedule.ts                     # REWRITTEN — thin tool over src/schedule/* (B6)

src/schedule/                       # NEW MODULE (B6)
├── types.ts                        # ScheduledJob, JobStore interfaces
├── store.ts                        # SQLite-backed persistence
└── runner.ts                       # Timer lifecycle + hydration on boot

src/platform/types.ts               # add hasRipgrep + hasDockerImagesPulled to SystemCapabilities
src/platform/capabilities/system-info.ts
                                    # probe rg + docker image inventory

src/memory/db.ts                    # schema migration: scheduled_jobs table

src/heartbeat/proactive.ts          # MODIFIED — route delivery through platform.notifier (B4)
```

### Design principles (carried from Cycle 1)

- **Interface-driven** — every new module exports stable contracts; impls behind factories where reasonable.
- **Cover all platforms** — every code path runs on macOS, Linux, Windows. No OS-locked behavior in shared code.
- **Structured errors** — typed error codes (`E_NETWORK_REQUIRED`, `E_DOCKER_UNAVAILABLE`, `E_DESTRUCTIVE_BLOCKED`, etc.), never bare strings.
- **Capability probing + caching** — features detected at boot via `platform.systemInfo.refresh()`, results cached; no per-call probing.
- **Fallback chains** — every external dependency (ripgrep, Docker, native notifier) has an explicit degradation path documented in the tool description so the LLM can adjust.
- **4-point logging standard** — entry / decision / step / exit per tool call. Errors always logged.

## Detailed designs

### B5 — `edit_file` `replace_all`

Backward-compatible single-flag addition.

**Parameter addition:**
```ts
replace_all: {
  type: "boolean",
  description: "If true, replaces every occurrence of old_string. If false or omitted, replaces only the first.",
}
```

**Behaviour:**
- `undefined` or `false` → existing semantics (first occurrence only)
- `true` → `content.split(oldString).join(newString)` — exact-match, left-to-right, non-overlapping
- Result string reports replacement count: `"Replaced N occurrences of '...' in <path>"`
- Zero matches → existing error `"old_string not found in <path>"`

**Edge cases:**
- Empty `old_string` → `INVALID_INPUT` error (would infinite-replace)
- `old_string === new_string` → no-op, returns `"0 replacements (no-op: replacement equals search)"`, file not rewritten
- Overlapping patterns → split/join handles correctly
- Multi-line strings → identical handling

### B1 — `list_directory` + `search_files`

#### `list_directory`

**Parameters:**
```ts
{
  path: string;                  // workspace-relative or absolute
  recursive?: boolean;           // default false
  glob?: string;                 // e.g. "**/*.ts" — when set implies recursive=true
  include_hidden?: boolean;      // default false (skip .git, .env, dotfiles)
  respect_gitignore?: boolean;   // default true
  max_results?: number;          // default 500, hard cap 5000
}
```

**Result:**
```ts
{
  entries: Array<{
    path: string;                // POSIX-style relative path
    type: "file" | "dir" | "symlink";
    size?: number;               // bytes for files
    modified?: string;           // ISO timestamp
  }>;
  truncated: boolean;
  totalScanned: number;
}
```

**Implementation:**
- Sandboxed via `platform.sandbox.check(absPath, { workspaceRoots: [cwd], allowTempdir: false, resolveSymlinks: true })`.
- Recursion via `fs.opendir` async iterator (streaming, no slurp).
- Glob via `micromatch` (~6KB transitive dep already present via vitest).
- `.gitignore` parsing via the `ignore` npm package (~10KB, widely used, matches git's own semantics including negation patterns).
- Hard exclusions (always skipped): `.git`, `node_modules`, `.next`, `dist`, `build`, `coverage`, `.cache`.
- Symlinks reported as `type:"symlink"` with the resolved target in `path`. Symlinks pointing outside the workspace are filtered out.
- Result paths normalized to forward-slash for cross-platform LLM consumption.

#### `search_files` — hybrid backend

**Parameters:**
```ts
{
  pattern: string;
  path?: string;                  // defaults to cwd
  regex?: boolean;                // default false (literal)
  case_sensitive?: boolean;       // default false
  glob?: string;                  // restrict file matching (e.g. "*.ts")
  max_matches?: number;           // default 200, hard cap 2000
  context_lines?: number;         // default 0
}
```

**Result:**
```ts
{
  matches: Array<{
    path: string;                 // POSIX relative path
    line: number;
    column: number;
    preview: string;              // the matched line
    before?: string[];            // context_lines before
    after?: string[];             // context_lines after
  }>;
  truncated: boolean;
  via: "ripgrep" | "js-fallback";
}
```

**Hybrid implementation:**
- **Primary:** when `platform.systemInfo.current().capabilities.hasRipgrep === true`, shell out to `rg --json` and parse JSON-lines output. Maps to ~10-100× speedup on large repos.
  - Flags: `-i`/`-S` for case, `--regexp`/`--fixed-strings`, `--glob`, `--max-count`, `--context`, `--no-config`, `--no-ignore-vcs` only when `respect_gitignore: false`.
  - Defensive parsing — read only required fields (`type`, `data.path.text`, `data.line_number`, `data.lines.text`); unknown fields ignored.
- **Fallback:** when ripgrep unavailable OR `STACKOWL_DISABLE_RG=true`, JS implementation streams each file via `createReadStream` line-by-line. Identical result shape.
- Same sandbox check on `path` for both paths.
- Binary file detection: skip files where first 8KB contains a null byte (matches ripgrep heuristic).

### B2 — `git_tool` writes

Extends the existing `GitTool` with write actions. Policy: **all writes allowed by default. Only `push --force`, `reset --hard`, and `branch_delete --force` require `i_understand_destructive: true` in the same call.**

**New action enum entries:**

| Action | Args | Destructive? |
|---|---|---|
| `add` | `paths: string[]` (or `["."]`) | no |
| `commit` | `message: string`, optional `amend?: boolean` | no (amend warns if HEAD shared with remote) |
| `checkout` | `target: string`, optional `create_branch?: boolean` | no |
| `push` | optional `remote` (default `origin`), `branch`, `force?: boolean` | force=true |
| `pull` | optional `remote`, `branch`, `rebase?: boolean` | no |
| `fetch` | optional `remote` | no |
| `merge` | `branch: string`, optional `no_ff?: boolean`, `abort?: boolean` | no |
| `rebase` | optional `onto: string`, `abort?: boolean`, `continue?: boolean` | no |
| `reset` | `target: string`, `mode: "soft"\|"mixed"\|"hard"` | mode=hard |
| `branch_create` | `name: string`, optional `from: string` | no |
| `branch_delete` | `name: string`, `force?: boolean` | force=true |
| `tag` | `name: string`, optional `message`, `delete?: boolean` | no |
| `stash_save` (existing) | optional `message` | no |
| `stash_pop` (existing) | none | no |

**Destructive-action gate:**

```ts
const isDestructive =
  (action === "push" && args.force === true) ||
  (action === "reset" && args.mode === "hard") ||
  (action === "branch_delete" && args.force === true);

if (isDestructive && args.i_understand_destructive !== true) {
  return JSON.stringify({
    success: false,
    error: {
      code: "DESTRUCTIVE_ACTION_BLOCKED",
      message: `${action}${args.force ? " --force" : ""} is destructive. Pass i_understand_destructive: true to proceed.`,
      hint: "This action can permanently destroy work. Confirm with the user before retrying.",
    },
  });
}
```

The flag must be set on the SAME tool call — never a session-level setting. Every destructive action logged at WARN level with the full git command for audit.

**Execution path:** all git commands shell out via `platform.shell.exec("git", [args...], { cwd, timeoutMs })` — cross-platform, timeout-aware, structured result. No direct `child_process.spawn` in `git.ts`.

**Branch / repo guards:**
- All actions reject if `cwd` not inside a git repo (check `git rev-parse --show-toplevel` before any write).
- Push to `main`/`master` with `force: true` includes the remote URL in the destructive-block error message so the LLM can confirm right repo before retry.

### B6 — `schedule` durability

Promote `src/tools/schedule.ts` from a one-file tool with in-memory `Map` to a proper module backed by SQLite.

#### Module shape

**`src/schedule/types.ts`**
```ts
export interface ScheduledJob {
  id: string;
  type: "remind" | "repeat";
  message: string;
  scheduleAt?: string;
  intervalMs?: number;
  nextFireAt: string;
  createdAt: string;
  status: "active" | "fired" | "cancelled" | "expired";
  metadata: {
    urgency?: "low" | "normal" | "critical";
    category?: string;
    channel?: string;
    userId?: string;
  };
}
```

**`src/schedule/store.ts`** — `ScheduleStore` wraps a SQLite handle (`MemoryDatabase`):
- `add(job)`, `update(id, patch)`, `remove(id)`, `list(filter?)`, `due(now)` (status='active' AND `nextFireAt <= now`).

**`src/schedule/runner.ts`** — `ScheduleRunner(store, notifier)`:
- `start()` — hydrate active jobs from store, then for each: if expired (>5min overdue) → mark `expired` and notify with `[Missed Reminder] {message}` once; if future `remind` → `setTimeout(delta)`; if `repeat` → `setInterval(intervalMs)`.
- `stop()` — clear all timers (clean shutdown).
- `scheduleJob(job)` / `cancelJob(id)` — used by the tool to add/cancel.

#### Schema migration

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

Added as an idempotent `CREATE TABLE IF NOT EXISTS` in `MemoryDatabase.createSchema()` — no destructive migration; existing tables untouched.

#### Bootstrap wiring

In `src/index.ts`, after `memoryDb` is initialized and `platform.initialize()` completes:
```ts
const scheduleStore = new ScheduleStore(memoryDb);
const scheduleRunner = new ScheduleRunner(scheduleStore, platform.notifier);
await scheduleRunner.start();
process.on("SIGTERM", () => scheduleRunner.stop());
process.on("SIGINT", () => scheduleRunner.stop());
```

The schedule tool's `execute()` now delegates to `scheduleRunner.scheduleJob()` / `cancelJob()` and `scheduleStore.list()`.

### B4 — Cross-platform notification delivery

`platform.notifier` already exists from Cycle 1. This wires consumers and exposes the notifier to the LLM.

**Wiring changes:**
- `src/schedule/runner.ts` (from B6) calls `platform.notifier.notify()` on fire instead of bare `onProgress`. The `via` field of the result is logged.
- `src/heartbeat/proactive.ts` routes through the notifier when no specific channel adapter is configured for the user.
- Cron jobs with `deliver: true` and no `deliveryTarget` configured fall through to `platform.notifier.notify()` instead of logging `[DELIVER_PENDING]`.

**New `notification_send` tool:**

```ts
// Parameters
{
  title: string;
  body: string;
  urgency?: "low" | "normal" | "critical";   // default "normal"
  category?: string;                          // grouping hint
}

// Result
{ delivered: boolean; via: "native" | "system" | "stderr" }
```

Rate-limited via module-level `Map<sessionId, RateWindow>`: 10 notifications/minute/session, aged out every 60s. Hitting the limit returns `E_RATE_LIMITED` with a clear error message.

### B3 — `code-sandbox` Docker isolation

The biggest item. Today's `src/tools/code-sandbox.ts` spawns Python/JS via `spawn()` on host — no isolation. The tool is named "sandbox" but isn't one.

#### Public contract (unchanged for LLM, new optional args)

```ts
// Parameters
{
  language: "python" | "javascript" | "typescript";
  code: string;
  timeoutMs?: number;                            // default 30000, max 300000
  allow_network?: boolean;                       // default false
  workspace_access?: "none" | "ro" | "rw";       // default "ro"
  packages?: string[];                           // pip/npm installs inside container
}

// Result
{
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  via: "docker" | "host";
  warning?: string;                              // when via=host (degraded isolation)
  timedOut: boolean;
  oomKilled?: boolean;
}
```

#### Execution path

```
platform.systemInfo.capabilities.hasDocker?
  ├─ true → runInDocker(opts)
  └─ false → runOnHost(opts) with warning "Docker not detected — running on host without isolation"
```

#### `runInDocker(opts)`

Container invocation as argv to `platform.shell.exec("docker", [...])`:

```
docker run --rm
  --network=none                                # unless allow_network=true → --network=bridge
  --memory=512m --memory-swap=512m
  --cpus=1
  --pids-limit=100
  --read-only
  --tmpfs /tmp:size=64m,exec
  --tmpfs /work-out:size=16m
  --user 65534:65534
  --cap-drop=ALL
  --security-opt=no-new-privileges
  -v <abs-cwd>:/work:<ro|rw>                    # only when workspace_access != "none"
  -w /work
  -e PYTHONDONTWRITEBYTECODE=1
  -e NODE_OPTIONS=--no-warnings
  <image>
  <interpreter> -                               # read code from stdin
```

Code is piped via `child.stdin.write(code)` — never embedded in argv (avoids escape bugs).

**Image selection:**
- `python` → `python:3.12-slim` (~50MB)
- `javascript` / `typescript` → `node:22-alpine` (~40MB; TS via baked-in `tsx`)

**Image management:** `platform.systemInfo.refresh()` probes `docker images --format '{{.Repository}}:{{.Tag}}'` at boot. The new `SystemCapabilities.hasDockerImagesPulled: { python: boolean; node: boolean }` reflects which sandbox images are local. On first call with a missing image, return `E_IMAGE_NOT_PULLED` with the suggested `docker pull` command rather than blocking 30s on a download.

**Packages:** when `opts.packages` is set, prepend an install step inside the container:
- Python: `pip install --user --no-warn-script-location --quiet <pkgs> && <user_code>`
- Node: `npm install --silent --prefix=/tmp/node_modules <pkgs>` followed by user code

Network must be `bridge` (`allow_network: true`) for installs to succeed; otherwise return `E_NETWORK_REQUIRED`.

#### `runOnHost(opts)` — fallback

Identical contract, runs `python3 -c <code>` / `node -e <code>` via `platform.shell.exec`. Result includes `warning: "Docker unavailable — code ran on host without isolation"`. `workspace_access: "rw"` is rejected without Docker (refuses to silently allow); `ro` and `none` are best-effort with a warning explaining the limitation.

## Migration + cross-cutting

- **Backward compatibility:** every existing tool signature preserved; new args are optional with safe defaults.
- **New SystemCapabilities:** `hasRipgrep`, `hasDockerImagesPulled: { python: boolean; node: boolean }` — probed at boot via existing `commandAvailable()` helper.
- **Logging:** 4-point standard everywhere.
- **Cross-platform tests:** every new test runs on the host OS; CI matrix (Cycle 1 plan) covers Mac/Linux/Windows for all platform-touching paths.

## Testing strategy

| Subject | Tests | Notes |
|---|---|---|
| `edit_file` `replace_all` | 4 | extend existing test file: true with N matches, false with single match, empty old_string, same-old-new no-op |
| `list_directory` | 7 | flat / recursive / glob / hidden / gitignore / sandbox / max_results truncation |
| `search_files` JS path | 6 | regex / literal / case / glob / binary skip / context |
| `search_files` ripgrep path | 3 | JSON parsing, --max-count respected, same result shape (skipped via `STACKOWL_DISABLE_RG` if rg missing) |
| `git_tool` writes | 13 | one happy-path per action in mkdtempSync git repo + destructive-gate tests |
| `schedule.store` | 6 | CRUD + due() + cross-session persistence |
| `schedule.runner` | 5 | hydrate expired, hydrate future, hydrate repeat, cancel timer, stop cleanly |
| `notification_send` | 3 | happy path, rate-limit after 10 calls, structured rate-limit error |
| `code-sandbox` Docker path | 10 | pure-python, network blocked, network allowed, read-only fs, tmp writable, timeout, OOM, packages, fork-bomb pids-limit, image-not-pulled |
| `code-sandbox` host fallback | 2 | Docker unavailable warning; rw rejected without Docker |
| `code-sandbox` integration | 1 | end-to-end via tool registry |

**Total: ~60 new tests.** Existing tests stay green (no contract changes).

## Risks

| Risk | Mitigation |
|---|---|
| Combined scope balloons Cycle 2 to ~25-30 tasks | Subagent-driven execution with per-task review keeps quality high; each task remains bite-sized |
| Docker tests can't run on Jetson without Docker daemon | Tests skip when `hasDocker: false`; CI on `ubuntu-latest` runs them |
| Schedule SQLite migration touches existing DB | Schema change is additive (new table only); existing tables untouched |
| Heartbeat double-delivery (channel adapter AND notifier both fire) | Notifier only fires when no channel context — explicit conditional, not a parallel path |
| Ripgrep JSON output schema changes across versions | Defensive parsing reads only required fields; unknown fields ignored |
| `git push` blocks on auth prompt on misconfigured repo | 30s default timeout, returns "Push timed out — likely auth issue" with stderr verbatim |
| `i_understand_destructive` becomes a footgun (LLM learns to always pass it) | Returned hint text is explicit; every destructive action logged at WARN with full command for audit trail |
| `.gitignore` parser misses edge cases (negation, `**/`, `!pattern`) | Use `ignore` npm package (~10KB, widely used, matches git semantics) |
| Very large repo enumeration (100k+ files) blows memory | Streaming via `fs.opendir`; `max_results` hard cap; `truncated:true` signals partial result |
| Windows path separator inconsistency in glob results | Normalize to forward-slash in result paths |
| Docker daemon goes down mid-session | Calls fail with clear error and degrade to host fallback with warning |
| Image pull blocks 30s+ on first use | Probed at boot; refuse with actionable error rather than block |
| Workspace mount on macOS VirtIO could leak host paths | `--volume :ro` enforced; symlinks inside the mount that point outside aren't followed by Docker on macOS |

## Deliverables

1. `src/tools/files.ts` — `EditFileTool` gains `replace_all`.
2. `src/tools/filesystem/list-directory.ts` + `search-files.ts` — two new tools.
3. `src/tools/dev/git.ts` — 13 new write actions with destructive gating.
4. `src/schedule/types.ts` + `store.ts` + `runner.ts` — new module.
5. `src/tools/schedule.ts` — rewritten as thin delegate over `src/schedule/*`.
6. `src/memory/db.ts` — `scheduled_jobs` table schema.
7. `src/heartbeat/proactive.ts` — routed through `platform.notifier`.
8. `src/tools/notification-send.ts` — new tool exposing the notifier.
9. `src/tools/code-sandbox.ts` — rewritten with Docker isolation + host fallback.
10. `src/platform/types.ts` + `capabilities/system-info.ts` — new capability probes (`hasRipgrep`, `hasDockerImagesPulled`).
11. `__tests__/` additions: ~60 new tests across the subjects above.
12. `package.json` adds `micromatch` and `ignore` runtime deps.
13. Documentation updates: `docs/dev-setup.md` notes the Docker prerequisite for full sandbox isolation.

## Out of scope (deferred)

- Migration of the 36 existing direct `os.tmpdir()` / `process.platform` call sites — future B0' cleanup cycle.
- Skill marketplace (ClawHub competitor).
- Multi-agent inter-session messaging primitive.
- Voice wake / native mobile apps.
- Tree-sitter repo maps.
