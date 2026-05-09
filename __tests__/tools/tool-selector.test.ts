import { describe, it, expect, beforeEach, vi } from "vitest";
import { ToolSelector } from "../../src/tools/tool-selector.js";
import type { ToolDefinition } from "../../src/providers/base.js";
import type { ApproachPattern } from "../../src/learning/approach-library.js";

describe("ToolSelector", () => {
  let mockGetEffectivenessScore: ReturnType<typeof vi.fn>;
  let mockGetPatterns: ReturnType<typeof vi.fn>;
  let selector: ToolSelector;
  let mockTools: ToolDefinition[];

  beforeEach(() => {
    mockGetEffectivenessScore = vi.fn();
    mockGetPatterns = vi.fn();
    selector = new ToolSelector(mockGetEffectivenessScore, mockGetPatterns);

    mockTools = [
      { name: "web_search", description: "Search the web" },
      { name: "read_file", description: "Read a file" },
      { name: "shell", description: "Run shell commands" },
    ] as ToolDefinition[];
  });

  describe("selectTool", () => {
    it("selects tool with highest effectiveness score", () => {
      mockGetEffectivenessScore.mockImplementation((owl, tool, task) => {
        if (tool === "web_search") return 0.9;
        if (tool === "read_file") return 0.7;
        return 0.5;
      });

      const result = selector.selectTool({
        taskType: "research",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.selectedTool.name).toBe("web_search");
      expect(result.effectivenessScore).toBe(0.9);
    });

    it("returns alternatives sorted by score", () => {
      mockGetEffectivenessScore.mockImplementation((owl, tool, task) => {
        if (tool === "web_search") return 0.9;
        if (tool === "read_file") return 0.7;
        return 0.5;
      });

      const result = selector.selectTool({
        taskType: "research",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.alternatives).toHaveLength(2);
      expect(result.alternatives[0].tool.name).toBe("read_file");
      expect(result.alternatives[1].tool.name).toBe("shell");
    });

    it("uses default score when no history exists", () => {
      mockGetEffectivenessScore.mockReturnValue(0.5);

      const result = selector.selectTool({
        taskType: "unknown_task",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.effectivenessScore).toBe(0.5);
    });

    it("applies recency bonus for recent patterns", () => {
      mockGetEffectivenessScore.mockReturnValue(0.7);
      mockGetPatterns.mockReturnValue({
        owlName: "Hoot",
        toolName: "web_search",
        taskType: "research",
        successCount: 5,
        failureCount: 1,
        effectivenessScore: 0.8,
        lastSuccessAt: new Date().toISOString(),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      } as ApproachPattern);

      const result = selector.selectTool({
        taskType: "research",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.effectivenessScore).toBeGreaterThan(0.7);
    });
  });
});
