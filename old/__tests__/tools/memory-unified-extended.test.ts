import { describe, it, expect, vi } from "vitest";
import { createMemoryUnifiedTool } from "../../src/tools/memory-unified.js";

const mockCtx = { userId: "user1" } as any;

describe("memory unified — write and invalidate actions", () => {
  it("action:write calls write dep with args and context", async () => {
    const writeFn = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: { written: "fact" }, error: null }));
    const tool = createMemoryUnifiedTool({ write: writeFn });
    await tool.execute({ action: "write", content: "User prefers dark mode", category: "prefs", confidence: 0.9 }, mockCtx);
    expect(writeFn).toHaveBeenCalledOnce();
    expect(writeFn).toHaveBeenCalledWith(
      expect.objectContaining({ action: "write", content: "User prefers dark mode" }),
      mockCtx
    );
  });

  it("action:invalidate calls invalidate dep with args and context", async () => {
    const invalidateFn = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: { invalidated: 1 }, error: null }));
    const tool = createMemoryUnifiedTool({ invalidate: invalidateFn });
    await tool.execute({ action: "invalidate", query: "dark mode" }, mockCtx);
    expect(invalidateFn).toHaveBeenCalledOnce();
    expect(invalidateFn).toHaveBeenCalledWith(
      expect.objectContaining({ action: "invalidate", query: "dark mode" }),
      mockCtx
    );
  });

  it("unsupported action returns ACTION_NOT_SUPPORTED error", async () => {
    const tool = createMemoryUnifiedTool({});
    const result = await tool.execute({ action: "delete" }, mockCtx);
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACTION_NOT_SUPPORTED");
  });
});
