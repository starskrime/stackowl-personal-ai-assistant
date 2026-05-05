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
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
} from "../../src/signals/collectors.js";

describe("GitStatusCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("emits at priority low regardless of file count (no hardcoded bump)", async () => {
    (execSync as any).mockImplementation((cmd: string) => {
      if (cmd.includes("status"))
        return Array.from({ length: 20 }, (_, i) => ` M f${i}.ts`).join("\n");
      return "";
    });
    const c = new GitStatusCollector("/tmp");
    expect(c.mode).toBe("poll");
    const signals = await c.collect!();
    expect(signals.length).toBeGreaterThan(0);
    for (const s of signals) {
      expect(s.priority).toBe("low");
    }
  });

  it("returns empty array when git command throws", async () => {
    (execSync as any).mockImplementation(() => {
      throw new Error("not a repo");
    });
    const c = new GitStatusCollector("/tmp");
    const signals = await c.collect!();
    expect(signals).toEqual([]);
  });
});

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
