import { describe, it, expect, beforeEach, vi } from "vitest";
import { ResultSynthesizer } from "../../src/delegation/result-synthesizer.js";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SubOwlResult } from "../../src/delegation/sub-owl-runner.js";

describe("ResultSynthesizer", () => {
  let mockProvider: ModelProvider;
  let synthesizer: ResultSynthesizer;
  let mockResults: SubOwlResult[];

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Synthesized response based on all subtasks.",
      }),
    } as unknown as ModelProvider;

    synthesizer = new ResultSynthesizer(mockProvider);

    mockResults = [
      {
        taskId: "t1",
        description: "Search for info",
        output: "Found relevant data about topic",
        success: true,
        iterations: 2,
        durationMs: 500,
      },
      {
        taskId: "t2",
        description: "Analyze data",
        output: "Analysis complete: key insights identified",
        success: true,
        iterations: 1,
        durationMs: 300,
      },
    ];
  });

  describe("synthesize", () => {
    it("calls LLM with formatted results", async () => {
      const result = await synthesizer.synthesize(
        "Research and analyze topic X",
        mockResults,
      );

      expect(mockProvider.chat).toHaveBeenCalled();
      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).toContain("Research and analyze topic X");
      expect(messages[1].content).toContain("t1");
      expect(messages[1].content).toContain("t2");
    });

    it("filters failed results by default", async () => {
      const mixedResults: SubOwlResult[] = [
        ...mockResults,
        {
          taskId: "t3",
          description: "Failed step",
          output: "[Failed: timeout]",
          success: false,
          iterations: 0,
          durationMs: 0,
        },
      ];

      await synthesizer.synthesize("Task with failure", mixedResults);

      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).not.toContain("Failed");
    });

    it("includes failed results when option set", async () => {
      const mixedResults: SubOwlResult[] = [
        ...mockResults,
        {
          taskId: "t3",
          description: "Failed step",
          output: "[Failed: timeout]",
          success: false,
          iterations: 0,
          durationMs: 0,
        },
      ];

      await synthesizer.synthesize("Task with failure", mixedResults, {
        includeFailedResults: true,
      });

      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).toContain("Failed");
    });

    it("returns fallback when LLM fails", async () => {
      mockProvider.chat = vi.fn().mockRejectedValue(new Error("LLM unavailable"));

      const result = await synthesizer.synthesize("Task", mockResults);

      expect(result).toContain("Found relevant data");
      expect(result).toContain("Analysis complete");
    });

    it("returns message when no successful results", async () => {
      const failedResults: SubOwlResult[] = [
        {
          taskId: "t1",
          description: "Failed",
          output: "[Failed]",
          success: false,
          iterations: 0,
          durationMs: 0,
        },
      ];

      const result = await synthesizer.synthesize("Task", failedResults);
      expect(result).toBe("No successful results to synthesize.");
    });

    it("respects maxResultLength option", async () => {
      mockResults[0].output = "A".repeat(3000);

      await synthesizer.synthesize("Task", mockResults, { maxResultLength: 500 });

      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content.length).toBeLessThan(3000);
    });
  });
});
