// __tests__/integration/tool-cortex-7d.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { VisionTool }      from "../../src/tools/vision.js";
import { DocumentTool }    from "../../src/tools/document.js";
import { CodeSandboxTool } from "../../src/tools/code-sandbox.js";
import { DbQueryTool }     from "../../src/tools/db-query.js";
import { ScheduleTool }    from "../../src/tools/schedule.js";

describe("Tool Cortex 7d — tool registration", () => {
  it("all 5 new tools have unique names", () => {
    const names = [
      VisionTool.definition.name,
      DocumentTool.definition.name,
      CodeSandboxTool.definition.name,
      DbQueryTool.definition.name,
      ScheduleTool.definition.name,
    ];
    const unique = new Set(names);
    expect(unique.size).toBe(5);
  });

  it("all 5 tools can be registered in a ToolRegistry without collision", () => {
    const registry = new ToolRegistry();
    expect(() => {
      registry.register(VisionTool);
      registry.register(DocumentTool);
      registry.register(CodeSandboxTool);
      registry.register(DbQueryTool);
      registry.register(ScheduleTool);
    }).not.toThrow();
  });

  it("getAllDefinitions returns all 5 new tools after registration", () => {
    const registry = new ToolRegistry();
    registry.register(VisionTool);
    registry.register(DocumentTool);
    registry.register(CodeSandboxTool);
    registry.register(DbQueryTool);
    registry.register(ScheduleTool);

    const defs = registry.getAllDefinitions();
    const names = defs.map((d) => d.name);
    expect(names).toContain("vision");
    expect(names).toContain("document");
    expect(names).toContain("sandbox");
    expect(names).toContain("db_query");
    expect(names).toContain("schedule");
  });

  it("McpCommandRouter.dispatch('list') returns string response", async () => {
    const { McpCommandRouter } = await import("../../src/gateway/commands/mcp-router.js");
    const mockManager = {
      listServers: vi.fn().mockReturnValue([
        { name: "test", transport: "stdio", connected: true, toolCount: 1, tools: ["t"] },
      ]),
    } as any;
    const result = await McpCommandRouter.dispatch("list", [], {
      mcpManager: mockManager,
      toolRegistry: {} as any,
      config: {} as any,
      basePath: "/tmp",
      saveConfig: vi.fn(),
    });
    expect(typeof result).toBe("string");
    expect(result).toContain("test");
  });

  it("toolError and toolSuccess produce correct shapes", async () => {
    const { toolError, toolSuccess } = await import("../../src/tools/tool-error.js");
    const errOut = JSON.parse(toolError("TEST_CODE", "test message"));
    expect(errOut.success).toBe(false);
    expect(errOut.error.code).toBe("TEST_CODE");

    const okOut = JSON.parse(toolSuccess({ x: 1 }));
    expect(okOut.success).toBe(true);
    expect(okOut.data.x).toBe(1);
  });
});
