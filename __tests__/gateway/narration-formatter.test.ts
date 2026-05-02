import { describe, it, expect } from "vitest";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";

describe("formatToolEvent", () => {
  it("returns search narration for tool:start with duckduckgo_search", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "duckduckgo_search",
      args: { query: "TypeScript 5.5 release notes" },
      turnId: "t1",
    });
    expect(msg).toBe('Searching the web for "TypeScript 5.5 release notes"…');
  });

  it("returns fetch narration for tool:start with web_crawl", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "web_crawl",
      args: { url: "https://example.com/docs" },
      turnId: "t1",
    });
    expect(msg).toContain("Fetching");
    expect(msg).toContain("example.com");
  });

  it("returns null for tool:result success (silent on success)", () => {
    const msg = formatToolEvent({
      type: "tool:result",
      toolName: "web_crawl",
      success: true,
      durationMs: 200,
      truncated: false,
    });
    expect(msg).toBeNull();
  });

  it("returns failure narration for tool:result failure", () => {
    const msg = formatToolEvent({
      type: "tool:result",
      toolName: "web_crawl",
      success: false,
      durationMs: 100,
      truncated: false,
    });
    expect(msg).toContain("failed");
  });

  it("returns blocked narration with suggestion", () => {
    const msg = formatToolEvent({
      type: "tool:goal_blocked",
      toolName: "duckduckgo_search",
      subGoal: "find price data",
      suggestion: "try web_crawl with specific URL",
    });
    expect(msg).toContain("try web_crawl with specific URL");
  });

  it("returns null for tool:goal_advance (silent on progress)", () => {
    const msg = formatToolEvent({
      type: "tool:goal_advance",
      toolName: "web_crawl",
      subGoal: "find article",
      verdict: "ADVANCES",
    });
    expect(msg).toBeNull();
  });

  it("formats memory search narration", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "recall_memory",
      args: { query: "last project discussion" },
      turnId: "t1",
    });
    expect(msg).toContain("Searching memory");
  });

  it("formats generic tool narration for unknown tool", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "some_unknown_tool",
      args: {},
      turnId: "t1",
    });
    expect(msg).toBe("Using some_unknown_tool…");
  });
});
