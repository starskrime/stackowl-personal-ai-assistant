import { describe, it, expect, beforeEach, vi } from "vitest";
import { TaskDecomposer } from "../../src/delegation/decomposer.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("TaskDecomposer", () => {
  let mockProvider: ModelProvider;
  let decomposer: TaskDecomposer;

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            id: "t1",
            description: "Research the topic",
            tools: ["web_search", "web_fetch"],
            dependsOn: [],
            expectedOutput: "Research findings",
          },
          {
            id: "t2",
            description: "Write summary",
            tools: ["write_file"],
            dependsOn: ["t1"],
            expectedOutput: "Summary document",
          },
        ]),
      }),
    } as unknown as ModelProvider;

    decomposer = new TaskDecomposer(mockProvider);
  });

  describe("decompose", () => {
    it("returns a valid decomposition plan", async () => {
      const plan = await decomposer.decompose(
        "Research topic X and write a summary",
      );

      expect(plan.subtasks).toHaveLength(2);
      expect(plan.parallelGroups).toBeDefined();
      expect(plan.originalTask).toBe("Research topic X and write a summary");
      expect(plan.totalSteps).toBe(2);
    });

    it("handles LLM parse errors with fallback", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: "invalid json",
      });

      const plan = await decomposer.decompose("Simple task");

      expect(plan.subtasks).toHaveLength(1);
      expect(plan.subtasks[0].id).toBe("t1");
      expect(plan.parallelGroups).toEqual([["t1"]]);
    });

    it("handles empty LLM response", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: "[]",
      });

      const plan = await decomposer.decompose("Task");

      expect(plan.subtasks).toHaveLength(1);
    });

    it("builds parallel groups correctly", async () => {
      const plan = await decomposer.decompose("Research and write");

      expect(plan.parallelGroups.length).toBeGreaterThanOrEqual(1);
    });
  });
});
