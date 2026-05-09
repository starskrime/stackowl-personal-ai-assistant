import { describe, it, expect, beforeEach } from "vitest";
import { ToolMastery, MasteryLevel } from "../../src/tools/tool-mastery.js";

describe("ToolMastery", () => {
  let mastery: ToolMastery;

  beforeEach(() => {
    mastery = new ToolMastery();
  });

  describe("recordAttempt", () => {
    it("starts at novice level", () => {
      const profile = mastery.getMasteryProfile("web_search");
      expect(profile.masteryLevel).toBe("novice");
      expect(profile.confidenceMultiplier).toBe(0.6);
      expect(profile.totalAttempts).toBe(0);
      expect(profile.successRate).toBe(0);
    });

    it("promotes to intermediate after 5 attempts with 50% success", () => {
      for (let i = 0; i < 5; i++) {
        mastery.recordAttempt("shell", i < 3);
      }
      const profile = mastery.getMasteryProfile("shell");
      expect(profile.masteryLevel).toBe("intermediate");
      expect(profile.totalAttempts).toBe(5);
      expect(profile.successRate).toBeCloseTo(0.6, 1);
    });

    it("promotes to expert after 10 attempts with 75% success", () => {
      for (let i = 0; i < 10; i++) {
        mastery.recordAttempt("read_file", i < 8);
      }
      const profile = mastery.getMasteryProfile("read_file");
      expect(profile.masteryLevel).toBe("expert");
    });

    it("promotes to master after 20 attempts with 90% success", () => {
      for (let i = 0; i < 20; i++) {
        mastery.recordAttempt("write_file", i < 18);
      }
      const profile = mastery.getMasteryProfile("write_file");
      expect(profile.masteryLevel).toBe("master");
    });

    it("calculates correct success rate", () => {
      mastery.recordAttempt("test_tool", true);
      mastery.recordAttempt("test_tool", true);
      mastery.recordAttempt("test_tool", false);

      const profile = mastery.getMasteryProfile("test_tool");
      expect(profile.successRate).toBeCloseTo(0.667, 2);
    });
  });

  describe("getConfidenceMultiplier", () => {
    it("returns novice multiplier for new tools", () => {
      expect(mastery.getConfidenceMultiplier("unknown")).toBe(0.6);
    });

    it("returns correct multiplier for each level", () => {
      expect(mastery.getConfidenceMultiplier("new_tool")).toBe(0.6);

      for (let i = 0; i < 5; i++) {
        mastery.recordAttempt("intermediate_tool", true);
      }
      expect(mastery.getConfidenceMultiplier("intermediate_tool")).toBe(0.8);

      for (let i = 0; i < 10; i++) {
        mastery.recordAttempt("expert_tool", true);
      }
      expect(mastery.getConfidenceMultiplier("expert_tool")).toBe(1.0);

      for (let i = 0; i < 20; i++) {
        mastery.recordAttempt("master_tool", true);
      }
      expect(mastery.getConfidenceMultiplier("master_tool")).toBe(1.2);
    });
  });

  describe("getMasteryLevel", () => {
    it("returns novice for new tools", () => {
      expect(mastery.getMasteryLevel("new_tool")).toBe("novice");
    });
  });

  describe("getAllMasteryProfiles", () => {
    it("returns empty array initially", () => {
      expect(mastery.getAllMasteryProfiles()).toEqual([]);
    });

    it("returns all tracked profiles", () => {
      mastery.recordAttempt("tool_a", true);
      mastery.recordAttempt("tool_b", true);

      const profiles = mastery.getAllMasteryProfiles();
      expect(profiles).toHaveLength(2);
    });
  });
});
