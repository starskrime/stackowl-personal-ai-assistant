import { describe, it, expect, vi } from "vitest";
import { createMacosSystemTool } from "../../../src/tools/macos/system-unified.js";
import type { ToolContext } from "../../../src/tools/registry.js";

function makeCtx(): ToolContext {
  return { cwd: "/", engineContext: {} as any };
}

describe("macos system unified tool", () => {
  it("tool name is 'macos_system'", () => {
    const tool = createMacosSystemTool({});
    expect(tool.definition.name).toBe("macos_system");
  });

  it("platforms includes 'darwin'", () => {
    const tool = createMacosSystemTool({});
    expect(tool.definition.platforms).toContain("darwin");
  });

  it("action:spotlight dispatches to spotlight implementation", async () => {
    const mockSpotlight = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: [] }));
    const tool = createMacosSystemTool({ spotlight: mockSpotlight });
    await tool.execute({ action: "spotlight", query: "resume.pdf" }, makeCtx());
    expect(mockSpotlight).toHaveBeenCalledWith(
      expect.objectContaining({ action: "spotlight", query: "resume.pdf" }),
      makeCtx(),
    );
  });

  it("unknown action returns structured error", async () => {
    const tool = createMacosSystemTool({});
    const result = await tool.execute({ action: "unknown_action" }, makeCtx());
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACTION_NOT_SUPPORTED");
  });
});
