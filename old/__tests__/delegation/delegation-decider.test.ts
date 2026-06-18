import { describe, it, expect, beforeEach } from "vitest";
import { DelegationDecider } from "../../src/delegation/delegation-decider.js";

describe("DelegationDecider", () => {
  let decider: DelegationDecider;

  beforeEach(() => {
    decider = new DelegationDecider();
  });

  describe("assessComplexity", () => {
    it("returns low score for simple tasks", () => {
      const score = decider.assessComplexity("Simple task", {});
      expect(score).toBeLessThan(0.5);
    });

    it("returns high score for multi-step tasks", () => {
      const score = decider.assessComplexity(
        "Research and write a comprehensive report",
        { estimatedSubtasks: 5 },
      );
      expect(score).toBeGreaterThanOrEqual(0.3);
    });

    it("increases score for cross-domain tasks", () => {
      const score = decider.assessComplexity("Complex multi-domain task", {
        requiresDifferentDomains: true,
      });
      expect(score).toBeGreaterThanOrEqual(0.2);
    });

    it("increases score for long tasks", () => {
      const longTask = "A".repeat(600);
      const score = decider.assessComplexity(longTask, {});
      expect(score).toBeGreaterThanOrEqual(0.15);
    });

    it("caps score at 1.0", () => {
      const score = decider.assessComplexity("A".repeat(600), {
        estimatedSubtasks: 10,
        hasDependencyChains: true,
        requiresDifferentDomains: true,
        hasUncertainty: true,
      });
      expect(score).toBeLessThanOrEqual(1);
    });
  });

  describe("decide", () => {
    it("returns direct for low complexity", () => {
      const decision = decider.decide("Simple one-step task");
      expect(decision.mode).toBe("direct");
      expect(decision.complexityScore).toBeLessThan(0.6);
    });

    it("returns delegated for high complexity", () => {
      const decision = decider.decide("Research, analyze, write, and review", {
        estimatedSubtasks: 5,
        hasDependencyChains: true,
      });
      expect(decision.mode).toBe("delegated");
    });

    it("returns delegated when estimated subtasks >= 3", () => {
      const decision = decider.decide("Task", {
        estimatedSubtasks: 3,
      });
      expect(decision.mode).toBe("delegated");
    });

    it("includes estimated parallel tasks when delegated", () => {
      const decision = decider.decide("Complex task", {
        estimatedSubtasks: 5,
        hasDependencyChains: true,
      });
      expect(decision.estimatedParallelTasks).toBeDefined();
      expect(decision.estimatedParallelTasks).toBeLessThanOrEqual(5);
    });

    it("includes reasoning in decision", () => {
      const decision = decider.decide("Task");
      expect(decision.reasoning).toContain("complexity");
    });

    it("caps estimated parallel tasks at 5", () => {
      const decision = decider.decide("Task", {
        estimatedSubtasks: 10,
      });
      expect(decision.estimatedParallelTasks).toBe(5);
    });
  });
});
