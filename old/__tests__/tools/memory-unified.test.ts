import { describe, it, expect, vi } from "vitest";
import { createMemoryUnifiedTool } from "../../src/tools/memory-unified.js";
import type { ToolContext } from "../../src/tools/registry.js";

function makeCtx(): ToolContext {
  return { cwd: "/", engineContext: {} as any };
}

describe("memory unified tool", () => {
  it("tool name is 'memory'", () => {
    const tool = createMemoryUnifiedTool({} as any);
    expect(tool.definition.name).toBe("memory");
  });

  it("action:search dispatches to search implementation", async () => {
    const mockSearch = vi.fn().mockResolvedValue("search results");
    const tool = createMemoryUnifiedTool({ search: mockSearch } as any);
    await tool.execute({ action: "search", query: "project discussion" }, makeCtx());
    expect(mockSearch).toHaveBeenCalledWith(expect.objectContaining({ query: "project discussion" }), makeCtx());
  });

  it("action:store dispatches to store implementation", async () => {
    const mockStore = vi.fn().mockResolvedValue("stored");
    const tool = createMemoryUnifiedTool({ store: mockStore } as any);
    await tool.execute({ action: "store", content: "important fact" }, makeCtx());
    expect(mockStore).toHaveBeenCalledWith(expect.objectContaining({ content: "important fact" }), makeCtx());
  });

  it("unknown action returns structured error", async () => {
    const tool = createMemoryUnifiedTool({} as any);
    const result = await tool.execute({ action: "unknown" }, makeCtx());
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
  });
});
