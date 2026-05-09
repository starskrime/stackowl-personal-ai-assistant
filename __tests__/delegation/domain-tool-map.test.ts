import { describe, it, expect, beforeEach } from "vitest";
import { DomainToolMap } from "../../src/delegation/domain-tool-map.js";

describe("DomainToolMap", () => {
  let map: DomainToolMap;

  beforeEach(() => {
    map = new DomainToolMap();
  });

  describe("getToolsForDomain", () => {
    it("returns base tools when no stats", () => {
      const tools = map.getToolsForDomain("research");
      expect(tools).toContain("web_fetch");
      expect(tools).toContain("web_search");
    });

    it("sorts by success rate when stats available", () => {
      map.recordOutcome("research", "web_fetch", false);
      map.recordOutcome("research", "web_fetch", false);
      map.recordOutcome("research", "web_search", true);
      map.recordOutcome("research", "web_search", true);

      const tools = map.getToolsForDomain("research");
      expect(tools[0]).toBe("web_search");
    });

    it("handles unknown domain", () => {
      const tools = map.getToolsForDomain("unknown_domain");
      expect(tools).toContain("recall");
    });
  });

  describe("recordOutcome", () => {
    it("updates success rate correctly", () => {
      map.recordOutcome("coding", "read_file", true);
      map.recordOutcome("coding", "read_file", true);
      map.recordOutcome("coding", "read_file", false);

      const stats = map.getToolStats("coding", "read_file");
      expect(stats?.successRate).toBeCloseTo(0.667, 2);
      expect(stats?.totalAttempts).toBe(3);
    });

    it("tracks multiple tools per domain", () => {
      map.recordOutcome("research", "web_fetch", true);
      map.recordOutcome("research", "web_search", false);

      const fetchStats = map.getToolStats("research", "web_fetch");
      const searchStats = map.getToolStats("research", "web_search");

      expect(fetchStats?.successRate).toBe(1);
      expect(searchStats?.successRate).toBe(0);
    });
  });

  describe("getDomainStats", () => {
    it("returns null for unknown domain", () => {
      expect(map.getDomainStats("unknown")).toBeNull();
    });

    it("returns stats map for known domain", () => {
      map.recordOutcome("research", "web_fetch", true);

      const stats = map.getDomainStats("research");
      expect(stats).not.toBeNull();
      expect(stats?.get("web_fetch")).toBeDefined();
    });
  });

  describe("addToolToDomain", () => {
    it("adds new tool to domain", () => {
      map.addToolToDomain("research", "new_tool");

      const tools = map.getToolsForDomain("research");
      expect(tools).toContain("new_tool");
    });

    it("does not duplicate existing tools", () => {
      map.addToolToDomain("research", "web_fetch");

      const tools = map.getToolsForDomain("research");
      const count = tools.filter((t) => t === "web_fetch").length;
      expect(count).toBe(1);
    });
  });
});
