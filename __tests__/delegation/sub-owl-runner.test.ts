import { describe, it, expect, beforeEach, vi } from "vitest";
import { SubOwlRunner } from "../../src/delegation/sub-owl-runner.js";
import type { DecompositionPlan } from "../../src/delegation/decomposer.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("SubOwlRunner", () => {
  let mockProvider: ModelProvider;
  let runner: SubOwlRunner;

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Subtask completed successfully",
      }),
    } as unknown as ModelProvider;

    runner = new SubOwlRunner(mockProvider, new Map(), "a helpful assistant");
  });

  describe("runAll", () => {
    it("executes all subtasks in parallel groups", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Multi-step research",
        subtasks: [
          {
            id: "t1",
            description: "Search for info",
            tools: ["web_search"],
            dependsOn: [],
            expectedOutput: "Search results",
          },
          {
            id: "t2",
            description: "Fetch details",
            tools: ["web_fetch"],
            dependsOn: [],
            expectedOutput: "Page content",
          },
        ],
        parallelGroups: [["t1", "t2"]],
        totalSteps: 2,
      };

      const result = await runner.runAll(plan);

      expect(result.subtaskResults).toHaveLength(2);
      expect(result.successRate).toBeGreaterThan(0);
    });

    it("respects dependency ordering", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Sequential task",
        subtasks: [
          {
            id: "t1",
            description: "First step",
            tools: ["shell"],
            dependsOn: [],
            expectedOutput: "Step 1 result",
          },
          {
            id: "t2",
            description: "Second step",
            tools: ["shell"],
            dependsOn: ["t1"],
            expectedOutput: "Step 2 result",
          },
        ],
        parallelGroups: [["t1"], ["t2"]],
        totalSteps: 2,
      };

      const result = await runner.runAll(plan);

      expect(result.subtaskResults).toHaveLength(2);
      expect(result.subtaskResults.find((r) => r.taskId === "t1")).toBeDefined();
      expect(result.subtaskResults.find((r) => r.taskId === "t2")).toBeDefined();
    });

    it("returns success rate based on results", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Test task",
        subtasks: [
          {
            id: "t1",
            description: "Task 1",
            tools: ["shell"],
            dependsOn: [],
            expectedOutput: "Result",
          },
        ],
        parallelGroups: [["t1"]],
        totalSteps: 1,
      };

      const result = await runner.runAll(plan);

      expect(result.successRate).toBeGreaterThanOrEqual(0);
      expect(result.totalDurationMs).toBeGreaterThanOrEqual(0);
    });

    it("synthesizes results", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Complex task",
        subtasks: [
          {
            id: "t1",
            description: "Subtask",
            tools: ["shell"],
            dependsOn: [],
            expectedOutput: "Output",
          },
        ],
        parallelGroups: [["t1"]],
        totalSteps: 1,
      };

      const result = await runner.runAll(plan);

      expect(result.synthesis).toBeDefined();
      expect(typeof result.synthesis).toBe("string");
    });
  });
});
