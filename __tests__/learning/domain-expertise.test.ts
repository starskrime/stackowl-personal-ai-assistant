import { describe, it, expect } from "vitest";
import { DomainExpertiseTracker } from "../../src/learning/domain-expertise.js";

describe("DomainExpertiseTracker", () => {
  describe("initial state", () => {
    it("returns 0.5 for unknown domain", () => {
      const tracker = new DomainExpertiseTracker();
      expect(tracker.getConfidence("unknown")).toBe(0.5);
    });

    it("getTopDomains returns empty array when no domains", () => {
      const tracker = new DomainExpertiseTracker();
      expect(tracker.getTopDomains(5)).toEqual([]);
    });

    it("getDomainStats returns undefined for unknown domain", () => {
      const tracker = new DomainExpertiseTracker();
      expect(tracker.getDomainStats("unknown")).toBeUndefined();
    });
  });

  describe("confidence updates on success", () => {
    it("increases confidence on successful tool execution", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("typescript", true);
      expect(tracker.getConfidence("typescript")).toBe(0.55);
    });

    it("accumulates multiple successes", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("rust", true);
      tracker.recordToolExecution("rust", true);
      tracker.recordToolExecution("rust", true);
      expect(tracker.getConfidence("rust")).toBe(0.65);
    });

    it("caps confidence at 1.0", () => {
      const tracker = new DomainExpertiseTracker();
      for (let i = 0; i < 20; i++) {
        tracker.recordToolExecution("go", true);
      }
      expect(tracker.getConfidence("go")).toBe(1.0);
    });
  });

  describe("confidence updates on failure", () => {
    it("decreases confidence on failed tool execution", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("python", false);
      expect(tracker.getConfidence("python")).toBe(0.4);
    });

    it("accumulates multiple failures", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("java", false);
      tracker.recordToolExecution("java", false);
      expect(tracker.getConfidence("java")).toBe(0.3);
    });

    it("floors confidence at 0.0", () => {
      const tracker = new DomainExpertiseTracker();
      for (let i = 0; i < 20; i++) {
        tracker.recordToolExecution("csharp", false);
      }
      expect(tracker.getConfidence("csharp")).toBe(0.0);
    });
  });

  describe("cautious mode", () => {
    it("is not cautious initially (confidence starts at 0.5)", () => {
      const tracker = new DomainExpertiseTracker();
      expect(tracker.isCautious("newdomain")).toBe(false);
    });

    it("becomes cautious when confidence drops below 0.3", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("ruby", false);
      tracker.recordToolExecution("ruby", false);
      tracker.recordToolExecution("ruby", false);
      expect(tracker.isCautious("ruby")).toBe(true);
    });

    it("is not cautious after failures if still above threshold", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("perl", false);
      expect(tracker.isCautious("perl")).toBe(false);
    });
  });

  describe("getTopDomains", () => {
    it("returns domains sorted by confidence descending", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("alpha", false);
      tracker.recordToolExecution("alpha", false);
      tracker.recordToolExecution("beta", true);
      tracker.recordToolExecution("beta", true);
      tracker.recordToolExecution("gamma", true);

      const top = tracker.getTopDomains(3);
      expect(top[0].domain).toBe("beta");
      expect(top[1].domain).toBe("gamma");
      expect(top[2].domain).toBe("alpha");
    });

    it("limits results to requested count", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("a", true);
      tracker.recordToolExecution("b", true);
      tracker.recordToolExecution("c", true);

      expect(tracker.getTopDomains(2)).toHaveLength(2);
    });
  });

  describe("getDomainStats", () => {
    it("returns full record for known domain", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("haskell", true);
      tracker.recordToolExecution("haskell", false);

      const stats = tracker.getDomainStats("haskell");
      expect(stats).toEqual({
        confidence: 0.45,
        successCount: 1,
        failureCount: 1,
        totalAttempts: 2,
        lastUpdated: expect.any(String),
      });
    });

    it("returns undefined for unknown domain", () => {
      const tracker = new DomainExpertiseTracker();
      expect(tracker.getDomainStats("unknown")).toBeUndefined();
    });
  });

  describe("adjustConfidence", () => {
    it("increases confidence by delta", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.adjustConfidence("elixir", 0.2);
      expect(tracker.getConfidence("elixir")).toBe(0.7);
    });

    it("decreases confidence by negative delta", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.adjustConfidence("scala", -0.3);
      expect(tracker.getConfidence("scala")).toBe(0.2);
    });

    it("caps at max 1.0", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.adjustConfidence("kotlin", 1.0);
      expect(tracker.getConfidence("kotlin")).toBe(1.0);
    });

    it("floors at 0.0", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.adjustConfidence("dart", -1.0);
      expect(tracker.getConfidence("dart")).toBe(0.0);
    });
  });

  describe("domain normalization", () => {
    it("treats domains case-insensitively", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("SWIFT", true);
      expect(tracker.getConfidence("swift")).toBe(0.55);
    });

    it("trims whitespace from domain names", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("  rust  ", true);
      expect(tracker.getConfidence("rust")).toBe(0.55);
    });

    it("ignores empty domain strings", () => {
      const tracker = new DomainExpertiseTracker();
      tracker.recordToolExecution("", true);
      expect(tracker.getConfidence("anything")).toBe(0.5);
    });
  });
});