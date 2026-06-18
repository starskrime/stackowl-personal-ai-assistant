// __tests__/cli/mcp-command.test.ts
import { describe, it, expect, vi } from "vitest";
import { CommandRegistry } from "../../src/cli/commands.js";

function makeMockGateway() {
  return {
    getMcpManager: vi.fn().mockReturnValue({
      listServers: vi.fn().mockReturnValue([
        { name: "test-server", transport: "stdio", connected: true, toolCount: 2, tools: ["a", "b"] },
      ]),
      formatStatus: vi.fn().mockReturnValue("status output"),
      addServer: vi.fn().mockResolvedValue(2),
      removeServer: vi.fn().mockResolvedValue(undefined),
      updateServer: vi.fn().mockResolvedValue(2),
      reconnect: vi.fn().mockResolvedValue(2),
      getServer: vi.fn().mockReturnValue(null),
    }),
    getToolRegistry: vi.fn().mockReturnValue({ getAllDefinitions: vi.fn().mockReturnValue([]) }),
    getConfig: vi.fn().mockReturnValue({ mcp: { servers: [] } }),
    getWorkspacePath: vi.fn().mockReturnValue("/tmp"),
  } as any;
}

describe("CLI /mcp command", () => {
  it("'mcp' is registered in CommandRegistry topLevelNames", () => {
    const registry = new CommandRegistry();
    expect(registry.topLevelNames()).toContain("mcp");
  });

  it("/mcp list prints server names", async () => {
    const registry = new CommandRegistry();
    const mockUi = { printLines: vi.fn(), printInfo: vi.fn(), printError: vi.fn() } as any;
    await registry.handle("/mcp list", mockUi, makeMockGateway());
    const calls = mockUi.printLines.mock.calls.flat(2).join(" ");
    expect(calls).toContain("test-server");
  });

  it("/mcp with no sub-command shows status (default to 'status')", async () => {
    const registry = new CommandRegistry();
    const mockUi = { printLines: vi.fn(), printInfo: vi.fn(), printError: vi.fn() } as any;
    await registry.handle("/mcp", mockUi, makeMockGateway());
    expect(mockUi.printLines).toHaveBeenCalled();
  });
});
