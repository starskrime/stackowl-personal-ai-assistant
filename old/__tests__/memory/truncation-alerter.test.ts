import { describe, it, expect, beforeEach } from "vitest";
import { TruncationAlerter } from "../../src/memory/truncation-alerter.js";

describe("TruncationAlerter", () => {
  let alerter: TruncationAlerter;

  beforeEach(() => {
    alerter = new TruncationAlerter(100, 12000);
  });

  describe("recordTruncation()", () => {
    it("records a truncation event", () => {
      const event = alerter.recordTruncation(5, 500, 20, 2000);

      expect(event.removedCount).toBe(5);
      expect(event.removedTokens).toBe(500);
      expect(event.severity).toBeDefined();
    });

    it("calculates 'partial' severity for small truncations", () => {
      const event = alerter.recordTruncation(2, 100, 20, 2000);

      expect(event.severity).toBe("partial");
    });

    it("calculates 'significant' severity for moderate truncations", () => {
      const event = alerter.recordTruncation(8, 800, 20, 2000);

      expect(event.severity).toBe("significant");
    });

    it("calculates 'severe' severity for large truncations", () => {
      const event = alerter.recordTruncation(15, 1500, 20, 2000);

      expect(event.severity).toBe("severe");
    });

    it("tracks content types", () => {
      const event = alerter.recordTruncation(5, 500, 20, 2000, ["message", "preference"]);

      expect(event.contentTypes).toContain("message");
      expect(event.contentTypes).toContain("preference");
    });

    it("categorizes affected content", () => {
      const event = alerter.recordTruncation(5, 500, 20, 2000, ["preference"]);

      expect(event.affectedCategories).toContain("preferences");
    });
  });

  describe("generateAlert()", () => {
    it("returns null for 'none' severity", () => {
      const event = alerter.recordTruncation(0, 0, 20, 2000);
      const alert = alerter.generateAlert(event);

      expect(alert).toBeNull();
    });

    it("generates alert for partial truncation", () => {
      const event = alerter.recordTruncation(2, 100, 20, 2000);
      const alert = alerter.generateAlert(event);

      expect(alert).not.toBeNull();
      expect(alert!.severity).toBe("partial");
      expect(alert!.message).toBeDefined();
    });

    it("generates alert for significant truncation", () => {
      const event = alerter.recordTruncation(8, 800, 20, 2000);
      const alert = alerter.generateAlert(event);

      expect(alert).not.toBeNull();
      expect(alert!.severity).toBe("significant");
    });

    it("generates alert for severe truncation", () => {
      const event = alerter.recordTruncation(15, 1500, 20, 2000);
      const alert = alerter.generateAlert(event);

      expect(alert).not.toBeNull();
      expect(alert!.severity).toBe("severe");
    });

    it("includes recovery hint", () => {
      const event = alerter.recordTruncation(8, 800, 20, 2000);
      const alert = alerter.generateAlert(event);

      expect(alert!.recoveryHint).toBeDefined();
    });

    it("includes suggestion based on content type", () => {
      const event = alerter.recordTruncation(5, 500, 20, 2000, ["preference"]);
      const alert = alerter.generateAlert(event);

      expect(alert!.details.suggestion).toContain("preferences");
    });
  });

  describe("shouldWarnUser()", () => {
    it("returns false when no alerts recorded", () => {
      expect(alerter.shouldWarnUser()).toBe(false);
    });

    it("returns false for partial severity", () => {
      alerter.recordTruncation(2, 100, 20, 2000);
      expect(alerter.shouldWarnUser()).toBe(false);
    });

    it("returns true for significant severity within time window", () => {
      alerter.recordTruncation(8, 800, 20, 2000);
      expect(alerter.shouldWarnUser()).toBe(true);
    });

    it("returns true for severe severity", () => {
      alerter.recordTruncation(15, 1500, 20, 2000);
      expect(alerter.shouldWarnUser()).toBe(true);
    });
  });

  describe("getRecentAlerts()", () => {
    it("returns empty array when no alerts", () => {
      expect(alerter.getRecentAlerts()).toHaveLength(0);
    });

    it("returns recent alerts up to limit", () => {
      for (let i = 0; i < 10; i++) {
        alerter.recordTruncation(i, i * 100, 20, 2000);
      }

      const recent = alerter.getRecentAlerts(5);
      expect(recent.length).toBeLessThanOrEqual(5);
    });
  });

  describe("getMostSevereRecentAlert()", () => {
    it("returns null when no alerts", () => {
      expect(alerter.getMostSevereRecentAlert()).toBeNull();
    });

    it("returns most severe alert", () => {
      alerter.recordTruncation(2, 100, 20, 2000);
      alerter.recordTruncation(15, 1500, 20, 2000);

      const mostSevere = alerter.getMostSevereRecentAlert();
      expect(mostSevere!.severity).toBe("severe");
    });
  });

  describe("buildSystemPromptAlert()", () => {
    it("returns empty string when no alerts", () => {
      expect(alerter.buildSystemPromptAlert()).toBe("");
    });

    it("returns empty string for partial alerts", () => {
      alerter.recordTruncation(2, 100, 20, 2000);
      expect(alerter.buildSystemPromptAlert()).toBe("");
    });

    it("returns alert for significant truncation", () => {
      alerter.recordTruncation(8, 800, 20, 2000);
      expect(alerter.buildSystemPromptAlert()).toContain("CONTEXT WARNING");
    });

    it("returns alert for severe truncation", () => {
      alerter.recordTruncation(15, 1500, 20, 2000);
      expect(alerter.buildSystemPromptAlert()).toContain("CONTEXT WARNING");
    });
  });

  describe("clearAlerts()", () => {
    it("clears all recent alerts", () => {
      alerter.recordTruncation(8, 800, 20, 2000);
      alerter.recordTruncation(15, 1500, 20, 2000);

      alerter.clearAlerts();

      expect(alerter.getRecentAlerts()).toHaveLength(0);
      expect(alerter.shouldWarnUser()).toBe(false);
    });
  });

  describe("severity calculation", () => {
    it("handles zero original count", () => {
      const event = alerter.recordTruncation(5, 500, 0, 0);
      expect(event.severity).toBeDefined();
    });

    it("handles edge case at threshold", () => {
      const event = alerter.recordTruncation(2, 200, 20, 2000);
      expect(event.severity).toBe("partial");
    });
  });

  describe("custom configuration", () => {
    it("respects custom maxMessages and maxTokens", () => {
      const customAlerter = new TruncationAlerter(50, 6000);
      const event = customAlerter.recordTruncation(10, 1000, 50, 6000);

      expect(event.severity).toBeDefined();
    });
  });
});
