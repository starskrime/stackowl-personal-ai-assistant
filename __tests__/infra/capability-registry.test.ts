import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  registerCapability,
  getCapability,
  getAllCapabilities,
  getDegradedCapabilities,
  buildDegradationPrompt,
  snapshotLog,
  _resetForTest,
} from "../../src/infra/capability-registry.js";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
    },
  },
}));

beforeEach(() => {
  _resetForTest();
});

describe("CapabilityRegistry", () => {
  describe("registerCapability / getCapability", () => {
    it("registers a FULL capability and retrieves it", () => {
      registerCapability("x", "FULL");
      const entry = getCapability("x");
      expect(entry).toBeDefined();
      expect(entry!.name).toBe("x");
      expect(entry!.status).toBe("FULL");
    });

    it("stores the optional reason", () => {
      registerCapability("episodicMemory", "DEGRADED", "ARM embedder not available");
      const entry = getCapability("episodicMemory");
      expect(entry!.reason).toBe("ARM embedder not available");
    });

    it("records registeredAt as a non-zero timestamp", () => {
      const before = Date.now();
      registerCapability("db", "FULL");
      const after = Date.now();
      const entry = getCapability("db");
      expect(entry!.registeredAt).toBeGreaterThanOrEqual(before);
      expect(entry!.registeredAt).toBeLessThanOrEqual(after);
    });

    it("returns undefined for an unregistered capability", () => {
      expect(getCapability("nonexistent")).toBeUndefined();
    });

    it("re-registering with a different status updates the entry, not duplicates", () => {
      registerCapability("x", "FULL");
      registerCapability("x", "DEGRADED", "updated");
      const all = getAllCapabilities();
      const xs = all.filter((e) => e.name === "x");
      expect(xs).toHaveLength(1);
      expect(xs[0].status).toBe("DEGRADED");
    });
  });

  describe("getDegradedCapabilities", () => {
    it("includes DEGRADED entries", () => {
      registerCapability("x", "DEGRADED", "reason");
      const degraded = getDegradedCapabilities();
      expect(degraded.some((e) => e.name === "x")).toBe(true);
    });

    it("includes OFFLINE entries", () => {
      registerCapability("contextPipeline", "OFFLINE", "missing deps");
      const degraded = getDegradedCapabilities();
      expect(degraded.some((e) => e.name === "contextPipeline")).toBe(true);
    });

    it("excludes FULL entries", () => {
      registerCapability("healthy", "FULL");
      const degraded = getDegradedCapabilities();
      expect(degraded.some((e) => e.name === "healthy")).toBe(false);
    });

    it("returns an empty array when all capabilities are FULL", () => {
      registerCapability("a", "FULL");
      registerCapability("b", "FULL");
      expect(getDegradedCapabilities()).toHaveLength(0);
    });
  });

  describe("buildDegradationPrompt", () => {
    it("returns empty string when no capabilities registered", () => {
      expect(buildDegradationPrompt()).toBe("");
    });

    it("returns empty string when all registered capabilities are FULL", () => {
      registerCapability("db", "FULL");
      registerCapability("memoryBus", "FULL");
      expect(buildDegradationPrompt()).toBe("");
    });

    it("returns a formatted string containing ⚠️ when DEGRADED entries exist", () => {
      registerCapability("episodicMemory", "DEGRADED", "ARM embedder not available");
      const prompt = buildDegradationPrompt();
      expect(prompt).toContain("⚠️");
      expect(prompt).toContain("episodicMemory");
    });

    it("includes the capability name and status in the output", () => {
      registerCapability("contextPipeline", "OFFLINE", "missing db/memoryBus/factStore/episodicMemory");
      const prompt = buildDegradationPrompt();
      expect(prompt).toContain("contextPipeline");
      expect(prompt).toContain("OFFLINE");
    });

    it("includes the reason in the output when provided", () => {
      registerCapability("synthesisProvider", "DEGRADED", "synthesis provider not registered");
      const prompt = buildDegradationPrompt();
      expect(prompt).toContain("synthesis provider not registered");
    });

    it("lists multiple degraded entries", () => {
      registerCapability("a", "OFFLINE", "reason a");
      registerCapability("b", "DEGRADED", "reason b");
      registerCapability("c", "FULL");
      const prompt = buildDegradationPrompt();
      expect(prompt).toContain("a");
      expect(prompt).toContain("b");
      expect(prompt).not.toContain("- c:");
    });
  });

  describe("snapshotLog", () => {
    it("returns the correct event field", () => {
      const snap = snapshotLog();
      expect(snap.event).toBe("capability.snapshot");
    });

    it("returns degradedCount = 0 and fullCount = 0 when empty", () => {
      const snap = snapshotLog();
      expect(snap.degradedCount).toBe(0);
      expect(snap.fullCount).toBe(0);
    });

    it("returns correct degradedCount and fullCount", () => {
      registerCapability("a", "FULL");
      registerCapability("b", "FULL");
      registerCapability("c", "DEGRADED", "reason");
      registerCapability("d", "OFFLINE", "reason");
      const snap = snapshotLog();
      expect(snap.fullCount).toBe(2);
      expect(snap.degradedCount).toBe(2);
    });

    it("includes all capabilities in the snapshot", () => {
      registerCapability("x", "FULL");
      registerCapability("y", "OFFLINE", "gone");
      const snap = snapshotLog();
      const caps = snap.capabilities as Array<{ name: string }>;
      expect(caps).toHaveLength(2);
      expect(caps.some((e) => e.name === "x")).toBe(true);
      expect(caps.some((e) => e.name === "y")).toBe(true);
    });
  });

  describe("_resetForTest", () => {
    it("clears all registered entries", () => {
      registerCapability("a", "FULL");
      registerCapability("b", "DEGRADED");
      _resetForTest();
      expect(getAllCapabilities()).toHaveLength(0);
    });
  });
});
