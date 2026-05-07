import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

// ---------------------------------------------------------------------------
// Chokidar mock — must be hoisted so it's available before imports
// ---------------------------------------------------------------------------
const { mockWatcher, chokidarWatchMock } = vi.hoisted(() => {
  const mockWatcher = {
    on: vi.fn().mockReturnThis(),
    close: vi.fn(),
  };
  return {
    mockWatcher,
    chokidarWatchMock: vi.fn().mockReturnValue(mockWatcher),
  };
});

vi.mock("chokidar", () => ({
  watch: chokidarWatchMock,
}));

// ---------------------------------------------------------------------------
// node:fs mock — existsSync for path resolution; others for handleFileChange
// ---------------------------------------------------------------------------
const { existsSyncMock, readFileSyncMock, statSyncMock } = vi.hoisted(() => ({
  existsSyncMock: vi.fn(() => true),
  readFileSyncMock: vi.fn(() => "content v1"),
  statSyncMock: vi.fn(() => ({ size: 100 })),
}));

vi.mock("node:fs", () => ({
  existsSync: existsSyncMock,
  readFileSync: readFileSyncMock,
  statSync: statSyncMock,
  readdirSync: vi.fn(() => []),
}));

import { join } from "node:path";
import { FileSystemCollector } from "../../src/signals/collectors.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract the handler registered for a given chokidar event name. */
function getChokidarHandler(eventName: string): ((...args: any[]) => void) | undefined {
  for (const call of mockWatcher.on.mock.calls) {
    if (call[0] === eventName) return call[1];
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FileSystemCollector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    existsSyncMock.mockReturnValue(true);
    readFileSyncMock.mockReturnValue("content v1");
    statSyncMock.mockReturnValue({ size: 100 } as any);
    // Restore mockWatcher.on chaining after clearAllMocks
    mockWatcher.on.mockReturnThis();
  });

  // -------------------------------------------------------------------------
  // Basic metadata
  // -------------------------------------------------------------------------

  it("registers as push-mode with source=perch", () => {
    const c = new FileSystemCollector("/tmp");
    expect(c.mode).toBe("push");
    expect(c.source).toBe("perch");
  });

  // -------------------------------------------------------------------------
  // Test A — uses chokidar watch (not fs.watch) when started
  // -------------------------------------------------------------------------

  it("uses chokidar watch when started", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});

    expect(chokidarWatchMock).toHaveBeenCalled();

    // All four event listeners must be registered
    const eventNames = mockWatcher.on.mock.calls.map((call: any[]) => call[0]);
    expect(eventNames).toContain("add");
    expect(eventNames).toContain("change");
    expect(eventNames).toContain("unlink");
    expect(eventNames).toContain("error");
  });

  // -------------------------------------------------------------------------
  // Test B — uses configuredPaths when provided
  // -------------------------------------------------------------------------

  it("uses configuredPaths when provided", () => {
    const paths = ["/custom/path1", "/custom/path2"];
    const c = new FileSystemCollector("/tmp", paths);
    c.start!(() => {});

    expect(chokidarWatchMock).toHaveBeenCalledWith(
      paths,
      expect.any(Object),
    );
  });

  // -------------------------------------------------------------------------
  // Test C — falls back to src/ heuristic when no configuredPaths
  // -------------------------------------------------------------------------

  it("falls back to src/ heuristic when no configuredPaths", () => {
    existsSyncMock.mockImplementation((p: string) =>
      p === join("/root", "src"),
    );
    const c = new FileSystemCollector("/root");
    c.start!(() => {});

    const firstArg = chokidarWatchMock.mock.calls[0][0] as string[];
    expect(firstArg).toContain(join("/root", "src"));
  });

  // -------------------------------------------------------------------------
  // Test D — stop() calls watcher.close()
  // -------------------------------------------------------------------------

  it("stop() calls watcher.close()", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    c.stop();
    expect(mockWatcher.close).toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Behavioral: coarse prefilter rejects node_modules, dist, .git, dotfiles, .tmp
  // -------------------------------------------------------------------------

  it("rejects coarse-prefilter paths (node_modules, dist, .git, dotfiles, .tmp)", () => {
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);

    const changeHandler = getChokidarHandler("change")!;
    const targetDir = join("/tmp", "src"); // existsSync returns true for src
    changeHandler(join(targetDir, "node_modules/foo.js"));
    changeHandler(join(targetDir, "dist/x.js"));
    changeHandler(join(targetDir, ".git/HEAD"));
    changeHandler(join(targetDir, ".env"));
    changeHandler(join(targetDir, "x.tmp"));

    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Behavioral: accepts arbitrary extensions (classifier handles relevance)
  // -------------------------------------------------------------------------

  it("accepts arbitrary extensions (relies on classifier for relevance)", () => {
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);

    const changeHandler = getChokidarHandler("change")!;
    const targetDir = join("/tmp", "src");
    changeHandler(join(targetDir, "something.exoticext"));

    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });

  // -------------------------------------------------------------------------
  // Behavioral: deduplication by content hash
  // -------------------------------------------------------------------------

  it("dedups by content hash", () => {
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);

    const changeHandler = getChokidarHandler("change")!;
    const targetDir = join("/tmp", "src");
    changeHandler(join(targetDir, "a.ts"));
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);

    emit.mockClear();
    // Same content → same hash → no new emission
    changeHandler(join(targetDir, "a.ts"));
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Behavioral: debounce batches multiple events into one emission
  // -------------------------------------------------------------------------

  it("debounces multiple events within 5s window into one emission", () => {
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);

    let i = 0;
    readFileSyncMock.mockImplementation(() => `v${i++}`);

    const changeHandler = getChokidarHandler("change")!;
    const targetDir = join("/tmp", "src");
    changeHandler(join(targetDir, "a.ts"));
    changeHandler(join(targetDir, "b.ts"));
    changeHandler(join(targetDir, "c.ts"));

    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });
});
