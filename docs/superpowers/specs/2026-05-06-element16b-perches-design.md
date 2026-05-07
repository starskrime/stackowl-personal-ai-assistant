# Element 16b — Perches Subsystem Design Spec

**Date:** 2026-05-06  
**Status:** Draft — awaiting Boss approval  
**Scope:** Wiring and correctness fixes only. No new architecture. No new files.  
**Net file delta:** 0 new files, 5 modified files

---

## Background

StackOwl's Perches subsystem (`SignalPool` + 6 collectors + `AmbientContextLayer`) is 60% functional. The ambient context pipeline is wired end-to-end: `FileSystemCollector` watches for file changes, `SignalPool` classifies and verifies signals, and `AmbientContextLayer` injects promoted signals into the system prompt as `<ambient_context>` XML. Three things are broken that prevent the pipeline from working correctly in production:

1. **Heartbeat never fires** — signals never expire, TTL is effectively infinite, stale context accumulates.
2. **ProactiveIntentionLoop receives `signalPool: undefined`** — ambient signals can never drive proactive outreach.
3. **Config schema missing** — `StackOwlConfig` has no `perches?` field, forcing unsafe `(as any)` casts and blocking TypeScript validation.

Two additional improvements:

4. **FileSystemCollector uses native `fs.watch`** — unreliable on Linux without `recursive` support. Chokidar v4 is already installed and used in two other files in the codebase.
5. **Watch paths are hardcoded** — `FileSystemCollector` always watches `{workspace}/src/` with no config override.

Two audit gaps are already fixed and require no action:

- **G3** (`signal:promoted` unhandled): live handler at `core.ts:2735` with `broadcastProactive()` and channel-parity narration.
- **G6** (clipboard consent): `DEFAULT_CONSENT.clipboard = false` already in `types.ts:46`.

---

## Locked Decisions

| ID | Decision | Files |
|----|----------|-------|
| D1 | Add `perches?` field to `StackOwlConfig`; fix two `(as any)` casts | `loader.ts`, `index.ts` |
| D2 | Switch `FileSystemCollector` to chokidar v4 (already installed) | `collectors.ts` |
| D3 | Wire `heartbeatTick()` inside `SignalPool.start()` | `pool.ts` |
| D4 | Accept `watchPaths` from config in `FileSystemCollector` | `collectors.ts`, `index.ts` |
| D5 | Add `setSignalPool()` to `ProactiveIntentionLoop`; call after pool construction | `proactive-loop.ts`, `index.ts` |

---

## D1 — Config Schema (`src/config/loader.ts` + `src/index.ts`)

### What breaks without this

- `b.config.perches` is accessed via `(b.config as any).perches?.consent` at `index.ts:1204` — TypeScript silent, no IDE autocomplete, no validation.
- `mutateConsent()` at `loader.ts:669` uses `(config as any).perches ??= {}` — same issue.

### Interface addition

Add to `StackOwlConfig` in `src/config/loader.ts` (after the `council?` block, around line 210):

```typescript
/** Ambient signal mesh (Perches) configuration */
perches?: {
  /** Per-source consent overrides. Falls back to DEFAULT_CONSENT when absent. */
  consent?: ConsentMap;
  /** Maximum signals retained in pool. Default: 32 */
  maxSignals?: number;
  /** FileSystemCollector debounce window (ms). Default: 5000 */
  fileWatchDebounceMs?: number;
  /** If set, only these sources are registered as collectors. Default: all. */
  enabledSources?: SignalSource[];
  /** Override watched paths for FileSystemCollector. Default: workspace src/ or root. */
  watchPaths?: string[];
};
```

`ConsentMap` and `SignalSource` are already imported at `loader.ts:11–12` — no new imports needed.

### Fix cast in `mutateConsent()` (`loader.ts:669`)

```typescript
// Before:
const perches = ((config as any).perches ??= {});
const consent = (perches.consent ??= {});

// After:
const perches = (config.perches ??= {});
const consent = (perches.consent ??= {});
```

### Fix cast in `index.ts` (SignalPool construction, ~line 1202)

```typescript
// Before:
config: {
  maxSignals: 32,
  consent: ((b.config as any).perches?.consent) ?? {},
},

// After:
config: {
  maxSignals: b.config.perches?.maxSignals ?? 32,
  consent: b.config.perches?.consent ?? {},
  enabledSources: b.config.perches?.enabledSources,
},
```

### No `DEFAULT_CONFIG` entry needed

The `perches` field is optional. When absent, `SignalPool.injectSignal()` at `pool.ts:131` falls back: `const allowed = consent[signal.source] ?? DEFAULT_CONSENT[signal.source]`. Adding a `DEFAULT_CONFIG.perches` block would pollute generated `stackowl.config.json` files and is redundant.

---

## D2 — FileSystemCollector → Chokidar (`src/signals/collectors.ts`)

### Why native `fs.watch` is the gap

- `{ recursive: true }` on `fs.watch` is unreliable on Linux — no native kernel-level recursive support on many distros.
- `fs.watch` does not reliably report rename/delete events across platforms.
- Chokidar v4 is already installed (`"chokidar": "^4.0.3"`) and used in `src/reload/manager.ts:11` and `src/skills/loader.ts:8` with identical named-import pattern.

### Import change

```typescript
// Remove:
import {
  readdirSync,
  statSync,
  existsSync,
  readFileSync,
  watch,          // ← remove this
} from "node:fs";
import { join } from "node:path";

// Add:
import {
  readdirSync,
  statSync,
  existsSync,
  readFileSync,
} from "node:fs";
import { join, relative } from "node:path";
import { watch as chokidarWatch, type FSWatcher } from "chokidar";
```

### Watcher field type

```typescript
// Before:
private watcher: ReturnType<typeof watch> | null = null;

// After:
private watcher: FSWatcher | null = null;
```

### `start()` replacement

Replace the `try { this.watcher = watch(...) }` block with:

```typescript
try {
  this.watcher = chokidarWatch(dirsToWatch, {
    persistent: false,
    ignoreInitial: true,
    usePolling: false,
  });
  this.watcher.on("add", (p) =>
    this.handleFileChange("rename", relative(this.targetDir, p)),
  );
  this.watcher.on("change", (p) =>
    this.handleFileChange("change", relative(this.targetDir, p)),
  );
  this.watcher.on("unlink", (p) =>
    this.handleFileChange("rename", relative(this.targetDir, p)),
  );
  this.watcher.on("error", (err) =>
    log.engine.warn(`[FileSystemCollector] ${(err as Error).message}`),
  );
} catch (err) {
  log.engine.warn(
    `[FileSystemCollector] start failed: ${(err as Error).message}`,
  );
}
```

**`persistent: false`** — chokidar will not keep the Node process alive after other work finishes.  
**`ignoreInitial: true`** — don't fire events for files that already existed at watch start.  
**5s debounce unchanged** — `handleFileChange()` → `flush()` at 5000ms coalesces bursts.

### `stop()` unchanged

`this.watcher?.close()` at `collectors.ts:309` already works for `FSWatcher`. No change needed.

---

## D3 — Heartbeat Wiring (`src/signals/pool.ts`)

### What breaks without this

`heartbeatTick()` (pool.ts:185) expires TTL-stale signals and re-verifies medium/high signals against the active goal. Without it being called, signals accumulate indefinitely — a signal from a file change 2 hours ago stays in the pool and continues injecting into system prompts.

### Fix

At the end of `SignalPool.start()`, after the collector loop (after `pool.ts:78`):

```typescript
// Heartbeat: expire TTL-stale signals and re-verify pending medium/high signals.
this.timers.push(
  setInterval(() => {
    void this.heartbeatTick();
  }, 60_000),
);
```

**Why inside `start()`:** `this.timers` is swept by `stop()` at `pool.ts:83–84` — the interval is auto-cleared on shutdown with zero external plumbing. No `clearInterval` call needed anywhere else.

**60s interval:** TTL values in StackOwl's collectors range from 30s (clipboard) to 360s (time/system). 60s heartbeat ensures 2 cycles maximum for the shortest-lived signals. Within production SOTA (Mary's research, Section 4).

---

## D4 — Configurable watchPaths (`src/signals/collectors.ts` + `src/index.ts`)

### Constructor change

```typescript
// Before:
constructor(private rootPath: string) {}

// After:
constructor(
  private rootPath: string,
  private configuredPaths?: string[],
) {}
```

### Path resolution in `start()`

Replace the hardcoded `targetDir` logic (lines 288–289) with:

```typescript
const dirsToWatch: string[] =
  this.configuredPaths && this.configuredPaths.length > 0
    ? this.configuredPaths
    : (() => {
        const srcDir = join(this.rootPath, "src");
        return [existsSync(srcDir) ? srcDir : this.rootPath];
      })();
this.targetDir = dirsToWatch[0];  // used by handleFileChange for relative() calls
```

Note: `this.targetDir` is still used in `handleFileChange()` to compute `fullPath = join(this.targetDir, filename)`. When multiple paths are watched, `this.targetDir` is set to the first entry — sufficient because chokidar always emits absolute paths which we convert to relative via `relative(this.targetDir, p)`.

### Wire in `index.ts`

```typescript
// Before (~line 1214):
signalPool.addCollector(new FileSystemCollector(b.workspacePath));

// After:
signalPool.addCollector(
  new FileSystemCollector(b.workspacePath, b.config.perches?.watchPaths),
);
```

---

## D5 — ProactiveIntentionLoop Wiring (`src/intent/proactive-loop.ts` + `src/index.ts`)

### Why the constructor can't be fixed directly

`ProactiveIntentionLoop` is constructed inside `buildGateway()` at `index.ts:1163`, which runs before `signalPool` is created at line 1197. Moving `signalPool` construction earlier would require moving all its dependencies (classifier, verifier, memoryRepo, goalGraph) before `buildGateway()` — a significant refactor. The `setSignalPool()` pattern is narrower and zero-risk.

### Method addition (`proactive-loop.ts`)

After the constructor (line 41):

```typescript
/** Wire the signal pool after construction — called from index.ts after pool is started. */
setSignalPool(pool: SignalPool): void {
  this.signalPool = pool;
}
```

The `SignalPool` type is already imported at `proactive-loop.ts:18`.

### Call site (`index.ts`)

After `gateway.ctx.signalPool = signalPool` (line 1215):

```typescript
gateway.ctx.proactiveLoop?.setSignalPool(signalPool);
```

`gateway.ctx.proactiveLoop` is typed as `ProactiveIntentionLoop` in `GatewayContext` — `?.` guards against undefined without requiring a type assertion.

### What `ProactiveIntentionLoop` does with `signalPool`

`evaluate()` at `proactive-loop.ts:102–113` reads `signalPool.getState().signals` and surfaces `critical` or `high` priority signals as `ambient_signal` proactive items (priority 50, below commitments at 100 and stale intents at 80). This is a read-only path — no promotion, no emission. No conflict with `AmbientContextLayer`.

---

## Signal → Context Flow (Post-Fix)

```
FileSystemCollector.start()
  └─ chokidarWatch(dirsToWatch, { persistent: false, ignoreInitial: true })
       ├─ on("add") / on("change") / on("unlink")
       └─ handleFileChange() → 5s debounce → flush()
              └─ emitFn(makeSignal("perch", ...))
                     └─ SignalPool.injectSignal()
                            ├─ Gate 1: consent check (DEFAULT_CONSENT.perch = true)
                            ├─ Gate 2: enabledSources check
                            ├─ Stage 1: SignalClassifier cheap-tier → confidence → priority
                            ├─ Emits signal:emitted to GatewayEventBus
                            └─ If priority == "high":
                                   GoalVerifier.verify() → if ADVANCES:
                                          signal.userSurfaceable = true
                                          Emits signal:promoted → core.ts:2735 → broadcastProactive()
                                          memoryRepo.insertBatch() [reflexive memory]

HeartbeatTick (every 60s, auto-wired in start())
  ├─ Expire TTL-stale signals (signal:expired events)
  └─ Re-verify medium/high non-surfaceable signals against active goal

AmbientContextLayer.shouldFire()
  └─ !t.isConversational && signalPool.hasHighPrioritySignals()
         └─ build() → signalPool.toContextBlock(8) → <ambient_context> XML in system prompt

ProactiveIntentionLoop.evaluate()  [now wired after D5]
  └─ signalPool.getState().signals → surface critical/high as ambient_signal proactive items
```

---

## File Change Summary

| File | Change | Delta |
|------|--------|-------|
| `src/config/loader.ts` | Add `perches?` block to `StackOwlConfig`; fix `mutateConsent` cast | +12 |
| `src/signals/pool.ts` | Add heartbeat interval to `start()` | +3 |
| `src/signals/collectors.ts` | Chokidar watcher + `watchPaths` constructor param | +18, −9 |
| `src/intent/proactive-loop.ts` | Add `setSignalPool()` method | +5 |
| `src/index.ts` | Fix `(as any)` cast; pass `watchPaths`; call `setSignalPool()` | +6, −2 |

**0 new files. 0 deleted files. Net file delta = 0.**

---

## Test Coverage

Each change gets a failing-first test before implementation:

| Change | Test |
|--------|------|
| D1 config schema | TypeScript compile test: assign `config.perches = { maxSignals: 16 }` — must type-check without `as any`. Unit: `mutateConsent()` correctly updates typed `config.perches.consent`. |
| D2 chokidar watcher | Unit: mock chokidar `watch` call in `FileSystemCollector.start()`; assert `add`, `change`, `unlink` listeners are registered; assert `stop()` calls `watcher.close()`. |
| D3 heartbeat | Unit: `SignalPool.start()` — assert `heartbeatTick` is called at least once after 60s (fake timers). Assert expired signals are removed from pool. |
| D4 watchPaths | Unit: construct `FileSystemCollector(root, ["/custom/path"])` — assert `chokidarWatch` is called with `["/custom/path"]`. Without config: assert heuristic (`src/` or root) is used. |
| D5 proactive loop | Unit: construct `ProactiveIntentionLoop` with `undefined` signalPool; call `setSignalPool(mockPool)`; call `evaluate()`; assert `mockPool.getState()` was called. |

---

## What Is NOT in Scope

- No new files in `src/`
- No consent CLI command (G4/G7 — deferred, depends on D1 shipping first)
- No changes to `AmbientContextLayer` logic or priority thresholds
- No changes to `SignalClassifier` or `GoalVerifier`
- No changes to `DEFAULT_CONSENT` values (clipboard already `false`)
- No chokidar version upgrade (stays on v4, `^4.0.3`)
- No changes to the 5 poll-based collectors (Git, Time, System, ActiveFile, Clipboard)
