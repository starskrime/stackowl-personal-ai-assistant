import { describe, it, expect, vi } from "vitest";
import { createWebUnifiedTool } from "../../src/tools/web-unified.js";
import type { ToolContext } from "../../src/tools/registry.js";

function makeCtx(): ToolContext {
  return { cwd: "/", engineContext: {} as any };
}

describe("web unified tool", () => {
  it("tool name is 'web'", () => {
    const tool = createWebUnifiedTool({} as any);
    expect(tool.definition.name).toBe("web");
  });

  it("action:search dispatches to search implementation", async () => {
    const mockSearch = vi.fn().mockResolvedValue("search results");
    const tool = createWebUnifiedTool({ search: mockSearch } as any);
    await tool.execute({ action: "search", query: "typescript 5.5" }, makeCtx());
    expect(mockSearch).toHaveBeenCalledWith(expect.objectContaining({ query: "typescript 5.5" }), makeCtx());
  });

  it("action:fetch dispatches to fetch implementation", async () => {
    const mockFetch = vi.fn().mockResolvedValue("page content");
    const tool = createWebUnifiedTool({ fetch: mockFetch } as any);
    await tool.execute({ action: "fetch", url: "https://example.com" }, makeCtx());
    expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ url: "https://example.com" }), makeCtx());
  });

  it("unknown action returns structured error", async () => {
    const tool = createWebUnifiedTool({} as any);
    const result = await tool.execute({ action: "unknown_action" }, makeCtx());
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
  });
});
