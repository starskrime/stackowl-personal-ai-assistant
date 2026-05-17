import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
  Logger: class {
    info() {}
    warn() {}
    debug() {}
    error() {}
  },
}));

vi.mock("node:child_process", () => ({ execSync: vi.fn() }));
vi.mock("node:fs", () => ({
  readdirSync: vi.fn(() => []),
  statSync: vi.fn(),
  existsSync: () => true,
  watch: vi.fn(),
  readFileSync: vi.fn(),
}));

import { execSync } from "node:child_process";
import {
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
} from "../../src/signals/collectors.js";

describe("TimeContextCollector", () => {
  it("emits at priority low and source time_of_day", async () => {
    const c = new TimeContextCollector();
    const signals = await c.collect!();
    expect(signals[0].source).toBe("time_of_day");
    expect(signals[0].priority).toBe("low");
  });
});

describe("SystemCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("emits disk usage at priority low regardless of percentage (no hardcoded bump)", async () => {
    (execSync as any).mockImplementation((cmd: string) => {
      if (cmd.startsWith("uptime")) return "up 3 days";
      if (cmd.startsWith("df"))
        return "Filesystem  Size  Used Avail Use% Mounted on\n/dev/disk1  100G  98G  2G   95%   /";
      return "";
    });
    const c = new SystemCollector();
    const signals = await c.collect!();
    expect(signals.length).toBeGreaterThan(0);
    for (const s of signals) {
      expect(s.priority).toBe("low");
    }
  });
});

describe("ActiveFileCollector", () => {
  it("returns empty when no recent files", async () => {
    const c = new ActiveFileCollector("/tmp");
    const signals = await c.collect!();
    expect(signals).toEqual([]);
  });
});

describe("ClipboardCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns empty on non-darwin", async () => {
    const orig = process.platform;
    Object.defineProperty(process, "platform", { value: "linux" });
    const c = new ClipboardCollector();
    const signals = await c.collect!();
    expect(signals).toEqual([]);
    Object.defineProperty(process, "platform", { value: orig });
  });

  it("emits clipboard signal at priority low (truncated to 200 chars)", async () => {
    if (process.platform !== "darwin") return;
    (execSync as any).mockImplementation((cmd: string) =>
      cmd === "pbpaste" ? "x".repeat(500) : "",
    );
    const c = new ClipboardCollector();
    const signals = await c.collect!();
    expect(signals[0].priority).toBe("low");
    expect(signals[0].content.length).toBeLessThanOrEqual(204);
  });

  it("does not re-emit the same content twice", async () => {
    if (process.platform !== "darwin") return;
    (execSync as any).mockImplementation(() => "stable content");
    const c = new ClipboardCollector();
    const first = await c.collect!();
    const second = await c.collect!();
    expect(first.length).toBe(1);
    expect(second).toEqual([]);
  });
});
