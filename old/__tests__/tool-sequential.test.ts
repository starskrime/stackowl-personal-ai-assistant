import { describe, it, expect } from "vitest";
import type { ToolDefinition } from "../src/providers/base.js";

describe("ToolDefinition sequential flag", () => {
  it("accepts sequential:true", () => {
    const tool: ToolDefinition = {
      name: "edit_file",
      description: "Edit a file",
      parameters: { type: "object", properties: {}, required: [] },
      sequential: true,
    };
    expect(tool.sequential).toBe(true);
  });

  it("defaults to undefined for parallel tools", () => {
    const tool: ToolDefinition = {
      name: "web_search",
      description: "Search",
      parameters: { type: "object", properties: {}, required: [] },
    };
    expect(tool.sequential).toBeFalsy();
  });
});
