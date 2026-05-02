import { describe, it, expect } from "vitest";
import type { ToolDefinition, ExecutionPolicy } from "../../src/providers/base.js";

describe("ToolDefinition extensions", () => {
  it("accepts deprecated flag", () => {
    // Filtering of deprecated tools from getAllDefinitions() is tested in registry-platform.test.ts (Task 4).
    const def: ToolDefinition = {
      name: "old_tool",
      description: "deprecated",
      parameters: { type: "object", properties: {} },
      deprecated: true,
    };
    expect(def.deprecated).toBe(true);
  });

  it("accepts platforms array", () => {
    const def: ToolDefinition = {
      name: "mac_tool",
      description: "mac only",
      parameters: { type: "object", properties: {} },
      platforms: ["darwin"],
    };
    expect(def.platforms).toContain("darwin");
  });

  it("accepts capabilities array", () => {
    const def: ToolDefinition = {
      name: "search_tool",
      description: "search",
      parameters: { type: "object", properties: {} },
      capabilities: ["web_fetch", "web_search"],
    };
    expect(def.capabilities).toHaveLength(2);
  });

  it("accepts executionPolicy", () => {
    const policy: ExecutionPolicy = { timeoutMs: 10000, maxRetries: 2, fallbackChain: ["other_tool"] };
    const def: ToolDefinition = {
      name: "slow_tool",
      description: "slow",
      parameters: { type: "object", properties: {} },
      executionPolicy: policy,
    };
    expect(def.executionPolicy?.timeoutMs).toBe(10000);
  });
});
