import { describe, it, expect, beforeEach, vi } from "vitest";
import { SubOwlExecutor } from "../../src/delegation/subowl-executor.js";
import type { ToolImplementation } from "../../src/tools/registry.js";

describe("SubOwlExecutor", () => {
  let executor: SubOwlExecutor;
  let mockToolRegistry: Map<string, ToolImplementation>;
  let mockTool: ToolImplementation;

  beforeEach(() => {
    mockTool = {
      definition: { name: "test_tool", description: "Test tool" },
      execute: vi.fn().mockResolvedValue("Tool executed successfully"),
    } as unknown as ToolImplementation;

    mockToolRegistry = new Map([["test_tool", mockTool]]);
    executor = new SubOwlExecutor(mockToolRegistry);
  });

  describe("executeSubtask", () => {
    it("executes all tools in the task", async () => {
      const result = await executor.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["test_tool"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.success).toBe(true);
      expect(result.toolsUsed).toContain("test_tool");
      expect(result.output).toBe("Tool executed successfully");
    });

    it("returns failure when no tools available", async () => {
      const executorEmpty = new SubOwlExecutor(new Map());

      const result = await executorEmpty.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["nonexistent"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.success).toBe(false);
      expect(result.output).toContain("No output");
    });

    it("continues when one tool fails", async () => {
      const failingTool: ToolImplementation = {
        definition: { name: "failing_tool", description: "Failing tool" },
        execute: vi.fn().mockRejectedValue(new Error("Tool failed")),
      } as unknown as ToolImplementation;

      const registry = new Map([
        ["failing_tool", failingTool],
        ["test_tool", mockTool],
      ]);
      const exec = new SubOwlExecutor(registry);

      const result = await exec.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["failing_tool", "test_tool"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.success).toBe(true);
      expect(result.toolsUsed).toContain("test_tool");
    });

    it("records errors in result", async () => {
      const failingTool: ToolImplementation = {
        definition: { name: "failing_tool", description: "Failing tool" },
        execute: vi.fn().mockRejectedValue(new Error("Failed")),
      } as unknown as ToolImplementation;

      const registry = new Map([["failing_tool", failingTool]]);
      const exec = new SubOwlExecutor(registry);

      const result = await exec.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["failing_tool"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.error).toBeDefined();
      expect(result.error).toContain("Failed");
    });
  });

  describe("setToolRegistry", () => {
    it("allows updating the registry", async () => {
      const newTool: ToolImplementation = {
        definition: { name: "new_tool", description: "New tool" },
        execute: vi.fn().mockResolvedValue("New result"),
      } as unknown as ToolImplementation;

      executor.setToolRegistry(new Map([["new_tool", newTool]]));

      const result = await executor.executeSubtask(
        {
          id: "t1",
          description: "Test",
          tools: ["new_tool"],
          dependsOn: [],
          expectedOutput: "output",
        },
        { cwd: "/tmp" },
      );

      expect(result.toolsUsed).toContain("new_tool");
    });
  });
});
