import { describe, it, expect, vi } from "vitest";
import { createMacosCommsTool } from "../../../src/tools/macos/comms-unified.js";
import type { ToolContext } from "../../../src/tools/registry.js";

function makeCtx(): ToolContext {
  return { cwd: "/", engineContext: {} as any };
}

describe("macos comms unified tool", () => {
  it("tool name is 'macos_comms'", () => {
    const tool = createMacosCommsTool({});
    expect(tool.definition.name).toBe("macos_comms");
  });

  it("platforms includes 'darwin'", () => {
    const tool = createMacosCommsTool({});
    expect(tool.definition.platforms).toContain("darwin");
  });

  it("action:mail dispatches to mail implementation", async () => {
    const mockMail = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: [] }));
    const tool = createMacosCommsTool({ mail: mockMail });
    await tool.execute({ action: "mail", operation: "read", count: 5 }, makeCtx());
    expect(mockMail).toHaveBeenCalledWith(
      expect.objectContaining({ action: "mail", operation: "read", count: 5 }),
      makeCtx(),
    );
  });

  it("unknown action returns structured error", async () => {
    const tool = createMacosCommsTool({});
    const result = await tool.execute({ action: "unknown_action" }, makeCtx());
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACTION_NOT_SUPPORTED");
  });
});
