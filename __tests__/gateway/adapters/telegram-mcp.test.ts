// __tests__/gateway/adapters/telegram-mcp.test.ts
import { describe, it, expect, vi } from "vitest";
import { McpCommandRouter } from "../../../src/gateway/commands/mcp-router.js";

// Test that McpCommandRouter covers all verbs the Telegram adapter delegates to.
// The adapter is thin — we test the router directly (adapter delegates, not duplicates).

const EXPECTED_VERBS = ["list", "status", "add", "remove", "enable", "disable", "tools", "reconnect", "install"];

describe("Telegram /mcp delegates to McpCommandRouter", () => {
  it("all expected verbs return string responses (not throws)", async () => {
    const mockManager = {
      listServers: vi.fn().mockReturnValue([]),
      formatStatus: vi.fn().mockReturnValue("no servers"),
      addServer: vi.fn().mockResolvedValue(0),
      removeServer: vi.fn().mockResolvedValue(undefined),
      updateServer: vi.fn().mockResolvedValue(0),
      reconnect: vi.fn().mockResolvedValue(0),
      getServer: vi.fn().mockReturnValue(null),
    };
    const deps = {
      mcpManager: mockManager as any,
      toolRegistry: {} as any,
      config: { mcp: { servers: [] } } as any,
      basePath: "/tmp",
      saveConfig: vi.fn().mockResolvedValue(undefined),
    };

    for (const verb of EXPECTED_VERBS) {
      const result = await McpCommandRouter.dispatch(verb, ["dummy-arg"], deps);
      expect(typeof result).toBe("string");
    }
  });

  it("unknown verb returns error string containing verb name", async () => {
    const result = await McpCommandRouter.dispatch("foobar", [], {
      mcpManager: { listServers: vi.fn().mockReturnValue([]) } as any,
      toolRegistry: {} as any,
      config: {} as any,
      basePath: "/tmp",
      saveConfig: vi.fn(),
    });
    expect(result).toMatch(/foobar/i);
  });
});
