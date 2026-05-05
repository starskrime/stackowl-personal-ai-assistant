import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const { watchMock, existsSyncMock, readFileSyncMock, statSyncMock } =
  vi.hoisted(() => ({
    watchMock: vi.fn(),
    existsSyncMock: vi.fn(() => true),
    readFileSyncMock: vi.fn(() => "content v1"),
    statSyncMock: vi.fn(() => ({ size: 100 })),
  }));

vi.mock("node:fs", () => ({
  watch: watchMock,
  existsSync: existsSyncMock,
  readFileSync: readFileSyncMock,
  statSync: statSyncMock,
  readdirSync: vi.fn(() => []),
}));

import { FileSystemCollector } from "../../src/signals/collectors.js";

describe("FileSystemCollector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    existsSyncMock.mockReturnValue(true);
    readFileSyncMock.mockReturnValue("content v1");
    statSyncMock.mockReturnValue({ size: 100 } as any);
  });

  it("registers as push-mode with source=perch", () => {
    const c = new FileSystemCollector("/tmp");
    expect(c.mode).toBe("push");
    expect(c.source).toBe("perch");
  });

  it("calls fs.watch when start() is invoked", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(watchMock).toHaveBeenCalled();
  });

  it("rejects coarse-prefilter paths (node_modules, dist, .git, dotfiles, .tmp)", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => {
      captured = cb;
      return { close: vi.fn() };
    });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "node_modules/foo.js");
    captured("change", "dist/x.js");
    captured("change", ".git/HEAD");
    captured("change", ".env");
    captured("change", "x.tmp");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("accepts arbitrary extensions (relies on classifier for relevance)", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => {
      captured = cb;
      return { close: vi.fn() };
    });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "src/something.exoticext");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });

  it("dedups by content hash", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => {
      captured = cb;
      return { close: vi.fn() };
    });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);

    emit.mockClear();
    captured("change", "src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("debounces multiple events within 5s window into one emission", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => {
      captured = cb;
      return { close: vi.fn() };
    });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    let i = 0;
    readFileSyncMock.mockImplementation(() => `v${i++}`);
    captured("change", "src/a.ts");
    captured("change", "src/b.ts");
    captured("change", "src/c.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });
});
