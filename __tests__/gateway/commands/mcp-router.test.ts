// __tests__/gateway/commands/mcp-router.test.ts
import { describe, it, expect, vi } from "vitest";
import { McpCommandRouter } from "../../../src/gateway/commands/mcp-router.js";
import type { MCPManager } from "../../../src/tools/mcp/manager.js";
import type { ToolRegistry } from "../../../src/tools/registry.js";

const mockRegistry = { register: vi.fn(), unregister: vi.fn(), reindexTools: vi.fn() } as unknown as ToolRegistry;

function makeMockManager(overrides: Partial<MCPManager> = {}): MCPManager {
  return {
    listServers: vi.fn().mockReturnValue([
      { name: "fs-server", transport: "stdio", connected: true, toolCount: 4, tools: ["read_file"] },
    ]),
    addServer: vi.fn().mockResolvedValue(4),
    removeServer: vi.fn().mockResolvedValue(undefined),
    connect: vi.fn().mockResolvedValue(4),
    disconnect: vi.fn(),
    reconnect: vi.fn().mockResolvedValue(4),
    formatStatus: vi.fn().mockReturnValue("status output"),
    updateServer: vi.fn().mockResolvedValue(4),
    getServer: vi.fn().mockReturnValue(null),
    ...overrides,
  } as unknown as MCPManager;
}

describe("McpCommandRouter.dispatch", () => {
  it("list returns server names", async () => {
    const mgr = makeMockManager();
    const result = await McpCommandRouter.dispatch("list", [], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: {} as any, basePath: "/tmp", saveConfig: vi.fn(),
    });
    expect(result).toContain("fs-server");
  });

  it("add calls addServer", async () => {
    const mgr = makeMockManager();
    await McpCommandRouter.dispatch("add", ["my-pkg"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [] } } as any, basePath: "/tmp",
      saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.addServer).toHaveBeenCalled();
  });

  it("remove calls removeServer", async () => {
    const mgr = makeMockManager();
    await McpCommandRouter.dispatch("remove", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.removeServer).toHaveBeenCalledWith(
      "fs-server", mockRegistry, expect.anything(), "/tmp", expect.any(Function),
    );
  });

  it("enable sets enabled:true and calls updateServer", async () => {
    const mgr = makeMockManager({
      updateServer: vi.fn().mockResolvedValue(4),
    });
    await McpCommandRouter.dispatch("enable", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.updateServer).toHaveBeenCalledWith(
      "fs-server", { enabled: true }, mockRegistry, expect.anything(), "/tmp", expect.any(Function),
    );
  });

  it("disable calls disconnect (not updateServer)", async () => {
    const mgr = makeMockManager();
    await McpCommandRouter.dispatch("disable", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.disconnect).toHaveBeenCalledWith("fs-server", mockRegistry);
    expect(mgr.updateServer).not.toHaveBeenCalled();
  });

  it("unknown verb returns error string", async () => {
    const mgr = makeMockManager();
    const result = await McpCommandRouter.dispatch("bogus", [], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: {} as any, basePath: "/tmp", saveConfig: vi.fn(),
    });
    expect(result).toMatch(/unknown.*bogus/i);
  });
});
