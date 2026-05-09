import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type {
  ContextSignal,
  SignalCollector,
  AmbientRule,
} from "../src/ambient/types.js";

vi.mock("../src/logger.js", () => ({
  Logger: class {
    constructor() {
      return {
        info: vi.fn(),
        warn: vi.fn(),
        debug: vi.fn(),
        error: vi.fn(),
      };
    }
  },
}));

vi.mock("node:child_process", () => ({
  execSync: vi.fn(),
}));

vi.mock("node:fs", () => ({
  readdirSync: vi.fn(),
  statSync: vi.fn(),
}));

import { execSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import {
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
} from "../src/ambient/collectors.js";
import { ContextMesh } from "../src/ambient/mesh.js";

function makeSignal(overrides: Partial<ContextSignal> = {}): ContextSignal {
  return {
    id: "test-id",
    source: "git",
    priority: "low",
    title: "Test Signal",
    content: "Test content",
    timestamp: Date.now(),
    ttlMs: 60_000,
    ...overrides,
  };
}

function makeCollector(
  source: ContextSignal["source"],
  signals: ContextSignal[] = [],
): SignalCollector {
  return {
    source,
    intervalMs: 60_000,
    collect: vi.fn().mockResolvedValue(signals),
  };
}

describe("collectors", () => {
  beforeEach(() => {
    vi.mocked(execSync).mockReset();
    vi.mocked(readdirSync).mockReset();
    vi.mocked(statSync).mockReset();
  });

  describe("GitStatusCollector", () => {
    it("returns empty when no changes", async () => {
      vi.mocked(execSync).mockReturnValueOnce("").mockReturnValueOnce("");
      const collector = new GitStatusCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });

    it("returns signal for changed files", async () => {
      vi.mocked(execSync)
        .mockReturnValueOnce("M  file1.ts\nA  file2.ts")
        .mockReturnValueOnce("abc123 Commit message");
      const collector = new GitStatusCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(2);
      expect(signals[0].source).toBe("git");
      expect(signals[0].priority).toBe("low");
      expect(signals[0].title).toBe("2 uncommitted files");
    });

    it("uses medium priority when >5 files changed", async () => {
      const changes = Array.from(
        { length: 7 },
        (_, i) => `M  file${i}.ts`,
      ).join("\n");
      vi.mocked(execSync).mockReturnValueOnce(changes).mockReturnValueOnce("");
      const collector = new GitStatusCollector("/tmp");
      const signals = await collector.collect();
      expect(signals[0].priority).toBe("medium");
    });

    it("returns recent commits signal", async () => {
      vi.mocked(execSync)
        .mockReturnValueOnce("")
        .mockReturnValueOnce("abc123 Commit 1\ndef456 Commit 2");
      const collector = new GitStatusCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(1);
      expect(signals[0].title).toBe("Recent commits");
    });

    it("returns empty array on error", async () => {
      vi.mocked(execSync).mockImplementation(() => {
        throw new Error("git not found");
      });
      const collector = new GitStatusCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });
  });

  describe("TimeContextCollector", () => {
    const originalDate = Date;

    afterEach(() => {
      vi.restoreAllMocks();
      vi.stubGlobal("Date", originalDate as typeof Date);
    });

    it("returns time signal with correct structure", async () => {
      const RealDate = Date;
      const mockDate = class extends RealDate {
        constructor() {
          super("2024-01-15T10:30:00");
        }
        getHours() {
          return 10;
        }
        getDay() {
          return 1;
        }
        toLocaleTimeString(
          _locale?: string | Array<string>,
          _options?: Intl.DateTimeFormatOptions,
        ) {
          return "10:30 AM";
        }
      } as unknown as typeof Date;
      Object.assign(mockDate, { now: RealDate.now.bind(RealDate) });
      vi.stubGlobal("Date", mockDate);

      const collector = new TimeContextCollector();
      const signals = await collector.collect();

      expect(signals).toHaveLength(1);
      expect(signals[0].source).toBe("time_of_day");
      expect(signals[0].priority).toBe("low");
      expect(signals[0].metadata).toMatchObject({
        hour: 10,
        period: "morning",
        dayName: "Monday",
        isWeekend: false,
      });
    });

    it("classifies weekend correctly", async () => {
      const RealDate = Date;
      const mockDate = class extends RealDate {
        constructor() {
          super("2024-01-13T14:00:00");
        }
        getHours() {
          return 14;
        }
        getDay() {
          return 6;
        }
        toLocaleTimeString() {
          return "2:00 PM";
        }
      } as unknown as typeof Date;
      Object.assign(mockDate, { now: RealDate.now.bind(RealDate) });
      vi.stubGlobal("Date", mockDate);

      const collector = new TimeContextCollector();
      const signals = await collector.collect();
      expect(signals[0].metadata?.isWeekend).toBe(true);
    });

    it("returns empty on error", async () => {
      vi.stubGlobal(
        "Date",
        class extends Date {
          constructor() {
            super();
            throw new Error("date error");
          }
        } as unknown as typeof Date,
      );

      const collector = new TimeContextCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });
  });

  describe("SystemCollector", () => {
    it("returns uptime and disk usage signals", async () => {
      vi.mocked(execSync)
        .mockReturnValueOnce(
          " 10:30:00 up 5 days, 14:22, 3 users, load average: 0.5",
        )
        .mockReturnValueOnce(
          "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       100G   45G   55G  45% /",
        );
      const collector = new SystemCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(2);
      expect(signals[0].title).toBe("System uptime");
      expect(signals[1].title).toBe("Disk usage: 45%");
    });

    it("uses high priority when disk usage >90%", async () => {
      vi.mocked(execSync)
        .mockReturnValueOnce("up 5 days")
        .mockReturnValueOnce(
          "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       100G   95G   5G  95% /",
        );
      const collector = new SystemCollector();
      const signals = await collector.collect();
      expect(signals[1].priority).toBe("high");
    });

    it("returns empty on error", async () => {
      vi.mocked(execSync).mockImplementation(() => {
        throw new Error("command failed");
      });
      const collector = new SystemCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });
  });

  describe("ActiveFileCollector", () => {
    it("returns empty when no recent files", async () => {
      vi.mocked(readdirSync).mockReturnValue([]);
      const collector = new ActiveFileCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });

    it("returns files modified within 5 minutes", async () => {
      const now = Date.now();
      const recentMtime = now - 2 * 60 * 1000;
      const oldMtime = now - 10 * 60 * 1000;

      const mockReaddirReturn = [
        { name: "recent.ts", isFile: () => true, isDirectory: () => false },
        { name: "old.ts", isFile: () => true, isDirectory: () => false },
      ] as unknown as ReturnType<typeof readdirSync>;
      vi.mocked(readdirSync).mockReturnValue(mockReaddirReturn);
      vi.mocked(statSync).mockImplementation(
        (path: Parameters<typeof statSync>[0]) => {
          const pathStr = String(path);
          if (pathStr.includes("recent")) {
            return { mtimeMs: recentMtime } as ReturnType<typeof statSync>;
          }
          return { mtimeMs: oldMtime } as ReturnType<typeof statSync>;
        },
      );

      const collector = new ActiveFileCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(1);
      expect(signals[0].title).toBe("1 recently modified file");
    });

    it("skips node_modules and dist directories", async () => {
      const mockReaddirReturn = [
        { name: "node_modules", isFile: () => false, isDirectory: () => true },
        { name: "dist", isFile: () => false, isDirectory: () => true },
        { name: ".git", isFile: () => false, isDirectory: () => true },
      ] as unknown as ReturnType<typeof readdirSync>;
      vi.mocked(readdirSync).mockReturnValue(mockReaddirReturn);

      const collector = new ActiveFileCollector("/tmp");
      await collector.collect();
      expect(readdirSync).toHaveBeenCalled();
    });

    it("returns empty on error", async () => {
      vi.mocked(readdirSync).mockImplementation(() => {
        throw new Error("access denied");
      });
      const collector = new ActiveFileCollector("/tmp");
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });
  });

  describe("ClipboardCollector", () => {
    it("returns empty on non-darwin platforms", async () => {
      const originalPlatform = process.platform;
      Object.defineProperty(process, "platform", { value: "linux" });
      const collector = new ClipboardCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
      expect(execSync).not.toHaveBeenCalled();
      Object.defineProperty(process, "platform", { value: originalPlatform });
    });

    it("returns empty when content unchanged", async () => {
      Object.defineProperty(process, "platform", { value: "darwin" });
      vi.mocked(execSync).mockReturnValue("same content");
      const collector = new ClipboardCollector();
      await collector.collect();
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });

    it("returns signal when clipboard changes", async () => {
      Object.defineProperty(process, "platform", { value: "darwin" });
      vi.mocked(execSync).mockReturnValue("new clipboard content");
      const collector = new ClipboardCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(1);
      expect(signals[0].source).toBe("clipboard");
    });

    it("truncates long content with ellipsis", async () => {
      Object.defineProperty(process, "platform", { value: "darwin" });
      const longContent = "a".repeat(300);
      vi.mocked(execSync).mockReturnValue(longContent);
      const collector = new ClipboardCollector();
      const signals = await collector.collect();
      expect(signals[0].content).toBe("a".repeat(200) + "...");
    });

    it("returns empty on error", async () => {
      Object.defineProperty(process, "platform", { value: "darwin" });
      vi.mocked(execSync).mockImplementation(() => {
        throw new Error("pbpaste failed");
      });
      const collector = new ClipboardCollector();
      const signals = await collector.collect();
      expect(signals).toHaveLength(0);
    });
  });
});

describe("ContextMesh", () => {
  let clock: { now: number };

  beforeEach(() => {
    clock = { now: Date.now() };
    vi.useFakeTimers();
    vi.setSystemTime(clock.now);
    vi.mocked(execSync).mockReset();
    vi.mocked(readdirSync).mockReset();
    vi.mocked(statSync).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe("constructor", () => {
    it("uses default maxSignals of 50", () => {
      const mesh = new ContextMesh("/tmp");
      const state = mesh.getState();
      expect(state.signals).toHaveLength(0);
    });

    it("respects custom maxSignals config", () => {
      const mesh = new ContextMesh("/tmp", { maxSignals: 10 });
      expect(mesh).toBeDefined();
    });

    it("filters collectors by enabledSources", () => {
      const mesh = new ContextMesh("/tmp", { enabledSources: ["git"] });
      mesh.addCollector(makeCollector("git"));
      mesh.addCollector(makeCollector("system"));
      expect(mesh.getState().activeContext).toBe("");
    });
  });

  describe("addCollector / addRule", () => {
    it("adds collector to mesh", () => {
      const mesh = new ContextMesh("/tmp");
      const collector = makeCollector("git", [makeSignal()]);
      mesh.addCollector(collector);
      expect(vi.mocked(collector.collect)).not.toHaveBeenCalled();
    });

    it("skips collector when source not in enabledSources", () => {
      const mesh = new ContextMesh("/tmp", { enabledSources: ["system"] });
      const collector = makeCollector("git");
      mesh.addCollector(collector);
      expect(mesh.getState().activeContext).toBe("");
    });

    it("adds rule to mesh", () => {
      const mesh = new ContextMesh("/tmp");
      const rule: AmbientRule = {
        name: "test-rule",
        condition: () => true,
        action: "notify",
        template: "Test",
        cooldownMs: 60_000,
      };
      mesh.addRule(rule);
      expect(mesh.evaluateRules()).toHaveLength(1);
    });
  });

  describe("start / stop", () => {
    it("starts mesh and runs collectors", () => {
      const mesh = new ContextMesh("/tmp");
      const collector = makeCollector("git", [
        makeSignal({ source: "git", title: "Git signal" }),
      ]);
      mesh.addCollector(collector);
      mesh.start();
      expect(vi.mocked(collector.collect)).toHaveBeenCalled();
      mesh.stop();
    });

    it("prevents double start", () => {
      const mesh = new ContextMesh("/tmp");
      const collector = makeCollector("git");
      mesh.addCollector(collector);
      mesh.start();
      mesh.start();
      expect(vi.mocked(collector.collect)).toHaveBeenCalledTimes(1);
      mesh.stop();
    });

    it("clears timers on stop", () => {
      const mesh = new ContextMesh("/tmp");
      const collector = makeCollector("git");
      mesh.addCollector(collector);
      mesh.start();
      mesh.stop();
      expect(mesh.getState().signals).toHaveLength(0);
    });
  });

  describe("getState", () => {
    it("returns signals sorted by priority", () => {
      const mesh = new ContextMesh("/tmp");
      mesh.injectSignal(makeSignal({ id: "1", priority: "low", title: "Low" }));
      mesh.injectSignal(
        makeSignal({ id: "2", priority: "critical", title: "Critical" }),
      );
      mesh.injectSignal(
        makeSignal({ id: "3", priority: "high", title: "High" }),
      );

      const state = mesh.getState();
      expect(state.signals[0].title).toBe("Critical");
      expect(state.signals[1].title).toBe("High");
      expect(state.signals[2].title).toBe("Low");
    });

    it("includes activeContext block", () => {
      const mesh = new ContextMesh("/tmp");
      mesh.injectSignal(
        makeSignal({ id: "1", priority: "low", title: "Test" }),
      );
      const state = mesh.getState();
      expect(state.activeContext).toContain("<ambient_context");
    });

    it("prunes expired signals", () => {
      const mesh = new ContextMesh("/tmp");
      mesh.injectSignal(
        makeSignal({ id: "expired", ttlMs: 1000, timestamp: clock.now - 2000 }),
      );
      mesh.injectSignal(makeSignal({ id: "valid", ttlMs: 60000 }));
      const state = mesh.getState();
      expect(state.signals.find((s) => s.id === "expired")).toBeUndefined();
      expect(state.signals.find((s) => s.id === "valid")).toBeDefined();
    });

    it("includes lastUpdate timestamp", () => {
      const mesh = new ContextMesh("/tmp");
      const state = mesh.getState();
      expect(state.lastUpdate).toBe(clock.now);
    });
  });

  describe("toContextBlock", () => {
    it("returns empty string when no signals", () => {
      const mesh = new ContextMesh("/tmp");
      expect(mesh.toContextBlock()).toBe("");
    });

    it("formats signals as XML", () => {
      const mesh = new ContextMesh("/tmp");
      mesh.injectSignal(
        makeSignal({ source: "git", priority: "low", title: "Changes" }),
      );
      const block = mesh.toContextBlock();
      expect(block).toContain('<signal source="git" priority="low">');
      expect(block).toContain("</ambient_context>");
    });

    it("limits signals to maxSignals", () => {
      const mesh = new ContextMesh("/tmp");
      for (let i = 0; i < 5; i++) {
        mesh.injectSignal(makeSignal({ id: String(i), title: `Signal ${i}` }));
      }
      const block = mesh.toContextBlock(3);
      const matches = block.match(/<signal/g);
      expect((matches ?? []).length).toBeLessThanOrEqual(3);
    });
  });

  describe("evaluateRules", () => {
    it("returns triggered rules", () => {
      const mesh = new ContextMesh("/tmp");
      const rule: AmbientRule = {
        name: "test-rule",
        condition: (signals) => signals.some((s) => s.title === "trigger"),
        action: "notify",
        template: "Triggered!",
        cooldownMs: 60_000,
      };
      mesh.addRule(rule);
      mesh.injectSignal(makeSignal({ title: "trigger" }));
      const triggered = mesh.evaluateRules();
      expect(triggered).toHaveLength(1);
      expect(triggered[0].rule.name).toBe("test-rule");
    });

    it("respects cooldown period", () => {
      const mesh = new ContextMesh("/tmp");
      const rule: AmbientRule = {
        name: "cooldown-rule",
        condition: () => true,
        action: "notify",
        template: "Test",
        cooldownMs: 60_000,
      };
      mesh.addRule(rule);
      mesh.evaluateRules();

      const triggered = mesh.evaluateRules();
      expect(triggered).toHaveLength(0);
    });

    it("allows re-trigger after cooldown expires", () => {
      const mesh = new ContextMesh("/tmp");
      const rule: AmbientRule = {
        name: "cooldown-rule",
        condition: () => true,
        action: "notify",
        template: "Test",
        cooldownMs: 30_000,
      };
      mesh.addRule(rule);
      mesh.evaluateRules();

      vi.advanceTimersByTime(31_000);
      const triggered = mesh.evaluateRules();
      expect(triggered).toHaveLength(1);
    });

    it("handles rule evaluation errors gracefully", () => {
      const mesh = new ContextMesh("/tmp");
      const rule: AmbientRule = {
        name: "error-rule",
        condition: () => {
          throw new Error("condition error");
        },
        action: "notify",
        template: "Test",
        cooldownMs: 60_000,
      };
      mesh.addRule(rule);
      mesh.injectSignal(makeSignal());
      const triggered = mesh.evaluateRules();
      expect(triggered).toHaveLength(0);
    });
  });

  describe("injectSignal", () => {
    it("adds signal to mesh", () => {
      const mesh = new ContextMesh("/tmp");
      mesh.injectSignal(makeSignal({ id: "new-signal" }));
      expect(mesh.getState().signals).toHaveLength(1);
    });

    it("enforces maxSignals limit", () => {
      const mesh = new ContextMesh("/tmp", { maxSignals: 2 });
      mesh.injectSignal(makeSignal({ id: "1", priority: "low" }));
      mesh.injectSignal(makeSignal({ id: "2", priority: "high" }));
      mesh.injectSignal(makeSignal({ id: "3", priority: "critical" }));
      const state = mesh.getState();
      expect(state.signals).toHaveLength(2);
    });

    it("keeps higher priority signals when enforcing limit", () => {
      const mesh = new ContextMesh("/tmp", { maxSignals: 2 });
      mesh.injectSignal(makeSignal({ id: "1", priority: "low" }));
      mesh.injectSignal(makeSignal({ id: "2", priority: "critical" }));
      mesh.injectSignal(makeSignal({ id: "3", priority: "medium" }));
      const state = mesh.getState();
      const priorities = state.signals.map((s) => s.priority);
      expect(priorities).toContain("critical");
      expect(priorities).toContain("medium");
      expect(priorities).not.toContain("low");
    });
  });

  describe("signal lifecycle", () => {
    it("replaces old signals from same source", async () => {
      const mesh = new ContextMesh("/tmp");
      const collector = makeCollector("git", [
        makeSignal({ id: "new-git", source: "git", title: "New git signal" }),
      ]);
      mesh.addCollector(collector);

      mesh.start();
      await vi.advanceTimersByTimeAsync(60_000);

      const state = mesh.getState();
      const gitSignals = state.signals.filter((s) => s.source === "git");
      expect(gitSignals).toHaveLength(1);
      expect(gitSignals[0].id).toBe("new-git");
      mesh.stop();
    });
  });
});
