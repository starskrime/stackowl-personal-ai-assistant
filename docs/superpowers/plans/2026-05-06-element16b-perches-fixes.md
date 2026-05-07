# Element 16b — Perches Wiring Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire 5 broken connections in the Perches subsystem so signals expire on TTL, proactive outreach sees ambient signals, and the FileSystemCollector uses chokidar with config-driven watch paths.

**Architecture:** 5 targeted fixes to 5 existing files, 0 new files. TDD order: D1 config types → D3 heartbeat timer → D2+D4 chokidar migration + watchPaths → D5 setSignalPool method. Each task commits independently; index.ts is touched in Tasks 1, 3, and 4 with no conflicts because each edit is in a distinct block.

**Tech Stack:** TypeScript strict, Vitest (fake timers + vi.mock), chokidar v4 (`^4.0.3` already installed), Node.js `node:fs` (imports trimmed).

---

## File Structure

| File | Change |
|------|--------|
| `src/config/loader.ts` | Add `ConsentMap` import; add `perches?:` block to `StackOwlConfig`; fix `as any` cast in `mutateConsent()` |
| `src/index.ts` | Fix `as any` cast in SignalPool config block; pass `watchPaths`; call `proactiveLoop?.setSignalPool()` |
| `src/signals/pool.ts` | Add `setInterval(() => heartbeatTick(), 60_000)` at end of `start()` |
| `src/signals/collectors.ts` | Replace `watch` from `node:fs` with chokidar; add `configuredPaths?` constructor param; update `start()` |
| `src/intent/proactive-loop.ts` | Add `setSignalPool(pool: SignalPool): void` method after constructor |
| `__tests__/signals/pool-heartbeat.test.ts` | Add one new test: heartbeat is scheduled from `start()` |
| `__tests__/signals/file-system-collector.test.ts` | Rewrite: replace `node:fs.watch` mock with chokidar mock; add watchPaths tests |
| `__tests__/proactive-loop.test.ts` | Add two new tests for `setSignalPool()` |

---

## Task 1 — D1: Config Schema (`src/config/loader.ts` + `src/index.ts`)

**Files:**
- Modify: `src/config/loader.ts` lines 11, ~199, 669
- Modify: `src/index.ts` lines ~1202–1204

D1 is a type-safety fix — the runtime behavior of `mutateConsent` is identical before and after (`(config as any).perches ??= {}` works at runtime). The failing condition is TypeScript compilation after we remove the `as any` casts. The existing `__tests__/signals/consent-mutation.test.ts` covers the behavior. No new runtime test is needed.

- [ ] **Step 1: Check baseline — existing consent tests pass and tsc is clean**

```bash
npx vitest run __tests__/signals/consent-mutation.test.ts
```
Expected: 4 tests pass.

```bash
npx tsc --noEmit
```
Expected: 0 errors (the `as any` casts suppress them).

- [ ] **Step 2: Add `ConsentMap` to the import in `src/config/loader.ts` line 11**

```typescript
// Before:
import type { SignalSource } from "../ambient/types.js";

// After:
import type { SignalSource, ConsentMap } from "../ambient/types.js";
```

- [ ] **Step 3: Add the `perches?:` block to `StackOwlConfig` in `src/config/loader.ts`**

Insert after the closing `};` of the `council?` block (~line 199). The `council?` block ends with `};` and is immediately followed by `/** Cognitive Loop configuration ... */`. Insert between them:

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

- [ ] **Step 4: Fix the `as any` cast in `mutateConsent()` at `src/config/loader.ts:669`**

```typescript
// Before:
    const perches = ((config as any).perches ??= {});

// After:
    const perches = (config.perches ??= {});
```

- [ ] **Step 5: Fix the `as any` cast in `src/index.ts` SignalPool config block (~line 1202)**

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

- [ ] **Step 6: Verify — tsc clean, behavior tests still pass**

```bash
npx tsc --noEmit
```
Expected: 0 errors.

```bash
npx vitest run __tests__/signals/consent-mutation.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/config/loader.ts src/index.ts
git commit -m "feat(perches): add perches? config field to StackOwlConfig, fix as-any casts (D1)"
```

---

## Task 2 — D3: Heartbeat Wiring (`src/signals/pool.ts`)

**Files:**
- Modify: `src/signals/pool.ts` (add one `setInterval` call inside `start()`)
- Modify: `__tests__/signals/pool-heartbeat.test.ts` (append one test)

**Why this test fails first:** `heartbeatTick()` exists in `pool.ts:185` but is never called from `start()`. The fake-timer test below will show `heartbeatTick` was called 0 times after 60s.

- [ ] **Step 1: Write the failing test**

Open `__tests__/signals/pool-heartbeat.test.ts`. Add `afterEach` to the vitest import at line 1 (so it reads `import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";`).

Append this test inside the existing `describe("SignalPool.heartbeatTick", ...)` block, after the last `it(...)` at line 127:

```typescript
  it("start() schedules heartbeatTick every 60 seconds", () => {
    vi.useFakeTimers();
    const pool = makePool({});
    const tickSpy = vi
      .spyOn(pool as any, "heartbeatTick")
      .mockResolvedValue(undefined);
    pool.start();
    expect(tickSpy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(tickSpy).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(60_000);
    expect(tickSpy).toHaveBeenCalledTimes(2);
    pool.stop();
  });

  afterEach(() => {
    vi.useRealTimers();
  });
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/signals/pool-heartbeat.test.ts
```
Expected: FAIL — `AssertionError: expected "heartbeatTick" to have been called 1 time, but was called 0 times`

- [ ] **Step 3: Implement D3 — add heartbeat interval inside `SignalPool.start()`**

In `src/signals/pool.ts`, locate `start()`. The method ends at line 79 with the closing `}` of the for loop, then `}` of `start()`. Insert these lines between the for loop's closing `}` and `start()`'s closing `}`:

```typescript
    // Heartbeat: expire TTL-stale signals and re-verify pending medium/high signals.
    this.timers.push(
      setInterval(() => {
        void this.heartbeatTick();
      }, 60_000),
    );
```

The full `start()` method after the change:

```typescript
  start(): void {
    if (this.started) return;
    this.started = true;
    log.engine.info(
      `[SignalPool] starting with ${this.collectors.length} collector(s)`,
    );
    for (const c of this.collectors) {
      if (c.mode === "push" && c.start) {
        c.start((signal) => {
          void this.injectSignal(signal);
        });
      } else if (c.mode === "poll" && c.collect && c.intervalMs) {
        void this.runPollCollector(c);
        this.timers.push(
          setInterval(() => {
            if (!this.collectors.includes(c)) return;
            void this.runPollCollector(c);
          }, c.intervalMs),
        );
      }
    }
    // Heartbeat: expire TTL-stale signals and re-verify pending medium/high signals.
    this.timers.push(
      setInterval(() => {
        void this.heartbeatTick();
      }, 60_000),
    );
  }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/signals/pool-heartbeat.test.ts
```
Expected: 6 tests pass (5 original + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-heartbeat.test.ts
git commit -m "feat(perches): wire heartbeatTick() inside SignalPool.start() every 60s (D3)"
```

---

## Task 3 — D2+D4: FileSystemCollector → Chokidar + configuredPaths (`src/signals/collectors.ts` + `src/index.ts`)

**Files:**
- Modify: `src/signals/collectors.ts` (imports, watcher field type, constructor, `start()`)
- Modify: `src/index.ts` (pass `watchPaths` arg to `FileSystemCollector`)
- Rewrite: `__tests__/signals/file-system-collector.test.ts` (replace `node:fs.watch` mock with chokidar mock; add watchPaths tests)

**Why the tests fail first:** The current code imports `watch` from `node:fs`. The new test mocks `chokidar` and asserts `chokidarWatchMock` was called. Before the implementation, the code calls native `watch` (not chokidar), so the assertion fails.

**Note on shouldProcess:** The spec's D2 code omits the `shouldProcess` prefilter in the chokidar event handlers. This plan adds it back to preserve existing filtering behavior (node_modules, dist, .git, dotfiles, .tmp are skipped).

- [ ] **Step 1: Rewrite `__tests__/signals/file-system-collector.test.ts`**

Replace the entire file contents with:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const { chokidarWatchMock, existsSyncMock, readFileSyncMock, statSyncMock } =
  vi.hoisted(() => ({
    chokidarWatchMock: vi.fn(),
    existsSyncMock: vi.fn(() => true),
    readFileSyncMock: vi.fn(() => "content v1"),
    statSyncMock: vi.fn(() => ({ size: 100 })),
  }));

vi.mock("chokidar", () => ({
  watch: chokidarWatchMock,
}));

vi.mock("node:fs", () => ({
  existsSync: existsSyncMock,
  readFileSync: readFileSyncMock,
  statSync: statSyncMock,
  readdirSync: vi.fn(() => []),
}));

import { FileSystemCollector } from "../../src/signals/collectors.js";

function freshWatcher() {
  return { on: vi.fn().mockReturnThis(), close: vi.fn() };
}

function getEventHandler(
  eventName: string,
): ((absPath: string) => void) | undefined {
  const watcher = chokidarWatchMock.mock.results[0]?.value;
  const call = (watcher?.on as ReturnType<typeof vi.fn>)?.mock.calls?.find(
    ([e]: [string]) => e === eventName,
  );
  return call?.[1];
}

describe("FileSystemCollector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    existsSyncMock.mockReturnValue(true);
    readFileSyncMock.mockReturnValue("content v1");
    statSyncMock.mockReturnValue({ size: 100 } as any);
    chokidarWatchMock.mockReturnValue(freshWatcher());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("registers as push-mode with source=perch", () => {
    const c = new FileSystemCollector("/tmp");
    expect(c.mode).toBe("push");
    expect(c.source).toBe("perch");
  });

  it("calls chokidar.watch (not node:fs.watch) when start() is invoked", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(chokidarWatchMock).toHaveBeenCalledOnce();
  });

  it("passes persistent:false and ignoreInitial:true to chokidar", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(chokidarWatchMock).toHaveBeenCalledWith(
      expect.any(Array),
      expect.objectContaining({ persistent: false, ignoreInitial: true }),
    );
  });

  it("registers add, change, unlink, and error event handlers", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    const watcher = chokidarWatchMock.mock.results[0]?.value;
    const registeredEvents = (watcher.on as ReturnType<typeof vi.fn>).mock.calls.map(
      ([e]: [string]) => e,
    );
    expect(registeredEvents).toContain("add");
    expect(registeredEvents).toContain("change");
    expect(registeredEvents).toContain("unlink");
    expect(registeredEvents).toContain("error");
  });

  it("uses configuredPaths when provided", () => {
    const c = new FileSystemCollector("/tmp", ["/custom/path"]);
    c.start!(() => {});
    expect(chokidarWatchMock).toHaveBeenCalledWith(
      ["/custom/path"],
      expect.anything(),
    );
  });

  it("uses src/ heuristic when no configuredPaths and src/ exists", () => {
    existsSyncMock.mockImplementation((p: unknown) => String(p) === "/tmp/src");
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(chokidarWatchMock).toHaveBeenCalledWith(
      ["/tmp/src"],
      expect.anything(),
    );
  });

  it("falls back to rootPath when src/ does not exist", () => {
    existsSyncMock.mockReturnValue(false);
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(chokidarWatchMock).toHaveBeenCalledWith(
      ["/tmp"],
      expect.anything(),
    );
  });

  it("rejects coarse-prefilter paths (node_modules, dist, .git, dotfiles, .tmp)", () => {
    // existsSyncMock returns true → targetDir = "/tmp/src"
    existsSyncMock.mockImplementation((p: unknown) => String(p) === "/tmp/src");
    const c = new FileSystemCollector("/tmp");
    const emit = vi.fn();
    c.start!(emit);
    const changeHandler = getEventHandler("change")!;
    changeHandler("/tmp/src/node_modules/foo.js");
    changeHandler("/tmp/src/dist/x.js");
    changeHandler("/tmp/src/.git/HEAD");
    changeHandler("/tmp/src/.env");
    changeHandler("/tmp/src/x.tmp");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("accepts arbitrary extensions (relies on classifier for relevance)", () => {
    existsSyncMock.mockImplementation((p: unknown) => String(p) === "/tmp/src");
    readFileSyncMock.mockReturnValue("new content");
    const c = new FileSystemCollector("/tmp");
    const emit = vi.fn();
    c.start!(emit);
    const changeHandler = getEventHandler("change")!;
    changeHandler("/tmp/src/src/something.exoticext");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });

  it("dedups by content hash — identical content does not emit twice", () => {
    existsSyncMock.mockImplementation((p: unknown) => String(p) === "/tmp/src");
    readFileSyncMock.mockReturnValue("same content");
    const c = new FileSystemCollector("/tmp");
    const emit = vi.fn();
    c.start!(emit);
    const changeHandler = getEventHandler("change")!;

    changeHandler("/tmp/src/src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);

    emit.mockClear();
    changeHandler("/tmp/src/src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("debounces multiple events within 5s window into one emission", () => {
    existsSyncMock.mockImplementation((p: unknown) => String(p) === "/tmp/src");
    let i = 0;
    readFileSyncMock.mockImplementation(() => `v${i++}`);
    const c = new FileSystemCollector("/tmp");
    const emit = vi.fn();
    c.start!(emit);
    const changeHandler = getEventHandler("change")!;
    changeHandler("/tmp/src/src/a.ts");
    changeHandler("/tmp/src/src/b.ts");
    changeHandler("/tmp/src/src/c.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });

  it("stop() calls watcher.close()", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    const watcher = chokidarWatchMock.mock.results[0]?.value;
    c.stop!();
    expect(watcher.close).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/signals/file-system-collector.test.ts
```
Expected: FAIL — `AssertionError: expected "chokidarWatchMock" to have been called once, but was called 0 times` (current code calls `node:fs.watch`, not chokidar)

- [ ] **Step 3: Implement D2 — update imports and watcher field in `src/signals/collectors.ts`**

Replace the import block at the top of the file (lines 1–16):

```typescript
import { execSync, type ExecSyncOptions } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import {
  readdirSync,
  statSync,
  existsSync,
  readFileSync,
} from "node:fs";
import { join, relative } from "node:path";
import { watch as chokidarWatch, type FSWatcher } from "chokidar";
import { log } from "../logger.js";
import type {
  ContextSignal,
  SignalCollector,
  SignalSource,
} from "../ambient/types.js";
```

Change the `watcher` field declaration in `FileSystemCollector` (line 270):

```typescript
// Before:
  private watcher: ReturnType<typeof watch> | null = null;

// After:
  private watcher: FSWatcher | null = null;
```

- [ ] **Step 4: Implement D4 — update constructor and `start()` in `FileSystemCollector`**

Replace the constructor (line 284):

```typescript
// Before:
  constructor(private rootPath: string) {}

// After:
  constructor(
    private rootPath: string,
    private configuredPaths?: string[],
  ) {}
```

Replace `start()` (lines 286–305) with:

```typescript
  start(emit: (s: ContextSignal) => void): void {
    this.emitFn = emit;
    const dirsToWatch: string[] =
      this.configuredPaths && this.configuredPaths.length > 0
        ? this.configuredPaths
        : (() => {
            const srcDir = join(this.rootPath, "src");
            return [existsSync(srcDir) ? srcDir : this.rootPath];
          })();
    this.targetDir = dirsToWatch[0];
    try {
      this.watcher = chokidarWatch(dirsToWatch, {
        persistent: false,
        ignoreInitial: true,
        usePolling: false,
      });
      this.watcher.on("add", (p) => {
        const rel = relative(this.targetDir, p);
        if (this.shouldProcess(rel)) this.handleFileChange("rename", rel);
      });
      this.watcher.on("change", (p) => {
        const rel = relative(this.targetDir, p);
        if (this.shouldProcess(rel)) this.handleFileChange("change", rel);
      });
      this.watcher.on("unlink", (p) => {
        const rel = relative(this.targetDir, p);
        if (this.shouldProcess(rel)) this.handleFileChange("rename", rel);
      });
      this.watcher.on("error", (err) =>
        log.engine.warn(`[FileSystemCollector] ${(err as Error).message}`),
      );
    } catch (err) {
      log.engine.warn(
        `[FileSystemCollector] start failed: ${(err as Error).message}`,
      );
    }
  }
```

- [ ] **Step 5: Wire `watchPaths` in `src/index.ts` (~line 1214)**

```typescript
// Before:
    signalPool.addCollector(new FileSystemCollector(b.workspacePath));

// After:
    signalPool.addCollector(
      new FileSystemCollector(b.workspacePath, b.config.perches?.watchPaths),
    );
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
npx vitest run __tests__/signals/file-system-collector.test.ts
```
Expected: 12 tests pass.

```bash
npx vitest run __tests__/signals/boot-wiring.test.ts
```
Expected: 1 test passes.

```bash
npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add src/signals/collectors.ts src/index.ts __tests__/signals/file-system-collector.test.ts
git commit -m "feat(perches): switch FileSystemCollector to chokidar v4, add configuredPaths support (D2+D4)"
```

---

## Task 4 — D5: ProactiveIntentionLoop.setSignalPool() (`src/intent/proactive-loop.ts` + `src/index.ts`)

**Files:**
- Modify: `src/intent/proactive-loop.ts` (add `setSignalPool()` method after constructor)
- Modify: `src/index.ts` (call `proactiveLoop?.setSignalPool(signalPool)` after pool wiring)
- Modify: `__tests__/proactive-loop.test.ts` (append two tests for `setSignalPool`)

**Why the tests fail first:** `ProactiveIntentionLoop` has no `setSignalPool` method. `loop.setSignalPool(mockPool)` throws `TypeError: loop.setSignalPool is not a function`.

- [ ] **Step 1: Write the failing tests**

Open `__tests__/proactive-loop.test.ts`. Append a new describe block at the end of the file, after the last closing `});` (after line 445):

```typescript
describe("setSignalPool()", () => {
  it("wires signalPool so evaluate() uses the wired pool", () => {
    const loop = new ProactiveIntentionLoop(
      undefined,
      undefined,
      undefined,
      undefined,
    );
    const mockPool = {
      getState: vi.fn().mockReturnValue({ signals: [] }),
    };
    loop.setSignalPool(mockPool as any);
    loop.evaluate();
    expect(mockPool.getState).toHaveBeenCalled();
  });

  it("surfaces high-priority signals after wiring via setSignalPool", () => {
    const loop = new ProactiveIntentionLoop(
      undefined,
      undefined,
      undefined,
      undefined,
    );
    const signal = {
      id: "s1",
      priority: "high" as const,
      title: "Hot file",
      content: "src/index.ts changed",
      source: "perch",
    };
    const mockPool = {
      getState: vi.fn().mockReturnValue({ signals: [signal] }),
    };
    loop.setSignalPool(mockPool as any);
    const result = loop.evaluate();
    expect(result?.type).toBe("ambient_signal");
    expect(result?.priority).toBe(50);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/proactive-loop.test.ts
```
Expected: FAIL — `TypeError: loop.setSignalPool is not a function`

- [ ] **Step 3: Add `setSignalPool()` to `src/intent/proactive-loop.ts`**

Insert after the closing `{}` of the constructor (after line 41):

```typescript
  /** Wire the signal pool after construction — called from index.ts after pool is started. */
  setSignalPool(pool: SignalPool): void {
    this.signalPool = pool;
  }
```

The constructor block + new method together:

```typescript
  constructor(
    private commitmentTracker: CommitmentTracker | undefined,
    private intentStateMachine: IntentStateMachine | undefined,
    private goalGraph: GoalGraph | undefined,
    private signalPool: SignalPool | undefined,
  ) {}

  /** Wire the signal pool after construction — called from index.ts after pool is started. */
  setSignalPool(pool: SignalPool): void {
    this.signalPool = pool;
  }
```

- [ ] **Step 4: Wire the call in `src/index.ts`**

After `gateway.ctx.signalPool = signalPool` (line 1215), add:

```typescript
    gateway.ctx.proactiveLoop?.setSignalPool(signalPool);
```

The surrounding context after the change:

```typescript
    signalPool.addCollector(
      new FileSystemCollector(b.workspacePath, b.config.perches?.watchPaths),
    );
    gateway.ctx.signalPool = signalPool;
    gateway.ctx.proactiveLoop?.setSignalPool(signalPool);
    signalPool.start();
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/proactive-loop.test.ts
```
Expected: All existing tests pass + 2 new tests pass (total depends on baseline count).

- [ ] **Step 6: Run the full test suite**

```bash
npx vitest run
```
Expected: All tests pass. No regressions in `pool-heartbeat`, `file-system-collector`, `consent-mutation`, `pool-lifecycle`, `boot-wiring`, or any other signal-related test.

- [ ] **Step 7: Final tsc check**

```bash
npx tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git add src/intent/proactive-loop.ts src/index.ts __tests__/proactive-loop.test.ts
git commit -m "feat(perches): add setSignalPool() to ProactiveIntentionLoop, wire in index.ts (D5)"
```
