import { describe, it, expect } from "vitest";
import { SemanticToolGate } from "../../src/intelligence/semantic-tool-gate.js";
import type { ToolDefinition } from "../../src/providers/base.js";

const mockTools: ToolDefinition[] = [
  { name: "web", description: "Search the web and fetch URLs", parameters: { type: "object", properties: {}, required: [] } },
  { name: "memory", description: "Store and retrieve user memories and facts", parameters: { type: "object", properties: {}, required: [] } },
  { name: "calendar", description: "Read and write Apple Calendar events", parameters: { type: "object", properties: {}, required: [] } },
  { name: "shell", description: "Execute shell commands and scripts", parameters: { type: "object", properties: {}, required: [] } },
  { name: "vision", description: "Analyze images using multimodal AI", parameters: { type: "object", properties: {}, required: [] } },
];

describe("SemanticToolGate", () => {
  it("returns at most limit tools", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("search the internet for news", 2);
    expect(result.length).toBeLessThanOrEqual(2);
  });

  it("returns web tool for a search query", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("find information on the web", 3);
    expect(result.map(t => t.name)).toContain("web");
  });

  it("returns memory tool for a memory query", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("remember this for later", 3);
    expect(result.map(t => t.name)).toContain("memory");
  });

  it("returns all tools when query is empty string", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("", mockTools.length);
    expect(result.length).toBe(mockTools.length);
  });
});
