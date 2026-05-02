import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import type { ToolImplementation } from "../../src/tools/registry.js";

function makeTool(name: string, platforms?: NodeJS.Platform[], deprecated?: boolean): ToolImplementation {
  return {
    definition: { name, description: "test", parameters: { type: "object", properties: {} }, platforms, deprecated },
    category: "filesystem" as any,
    execute: async () => "ok",
  };
}

describe("ToolRegistry platform enforcement", () => {
  it("returns platform error envelope when tool is not supported on current OS", async () => {
    const registry = new ToolRegistry();
    const wrongPlatform = process.platform === "darwin" ? ["linux"] : ["darwin"];
    registry.register(makeTool("linux_only_tool", wrongPlatform as any));
    const result = await registry.execute("linux_only_tool", {}, { cwd: "/" });
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("PLATFORM_NOT_SUPPORTED");
  });

  it("executes tool normally when platforms includes current OS", async () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("current_platform_tool", [process.platform as NodeJS.Platform]));
    const result = await registry.execute("current_platform_tool", {}, { cwd: "/" });
    expect(result).toBe("ok");
  });

  it("executes tool normally when platforms is undefined (all platforms)", async () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("universal_tool"));
    const result = await registry.execute("universal_tool", {}, { cwd: "/" });
    expect(result).toBe("ok");
  });
});

describe("ToolRegistry deprecated filter", () => {
  it("excludes deprecated tools from getAllDefinitions()", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("active_tool"));
    registry.register(makeTool("old_tool", undefined, true));
    const defs = registry.getAllDefinitions();
    expect(defs.map(d => d.name)).toContain("active_tool");
    expect(defs.map(d => d.name)).not.toContain("old_tool");
  });
});

describe("ToolRegistry event emission", () => {
  it("emits tool:start before execution and tool:result after", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);

    const startHandler = vi.fn();
    const resultHandler = vi.fn();
    bus.on("tool:start", startHandler);
    bus.on("tool:result", resultHandler);

    registry.register(makeTool("emit_test_tool"));
    await registry.execute("emit_test_tool", { x: 1 }, { cwd: "/" });

    expect(startHandler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:start", toolName: "emit_test_tool" })
    );
    expect(resultHandler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:result", toolName: "emit_test_tool", success: true })
    );
  });

  it("emits tool:result with success: false when tool throws", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);
    const resultHandler = vi.fn();
    bus.on("tool:result", resultHandler);
    const failingTool: ToolImplementation = {
      definition: { name: "fail_tool", description: "x", parameters: { type: "object", properties: {} } },
      category: "filesystem" as any,
      execute: async () => { throw new Error("boom"); },
    };
    registry.register(failingTool);
    await expect(registry.execute("fail_tool", {}, { cwd: "/" })).rejects.toThrow();
    expect(resultHandler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:result", toolName: "fail_tool", success: false })
    );
  });
});
