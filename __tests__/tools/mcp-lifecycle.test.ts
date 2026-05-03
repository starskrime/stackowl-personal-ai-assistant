import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("MCP tools — full registry lifecycle", () => {
  it("emits tool:start and tool:result for MCP-registered tool", async () => {
    const bus = new GatewayEventBus();
    const registry = new ToolRegistry();
    registry.setEventBus(bus);

    registry.register({
      definition: {
        name: "mcp_github_search",
        description: "search github via MCP server",
        parameters: { type: "object", properties: {} },
      },
      category: "external",
      source: "mcp",
      execute: async () => "ok",
    });

    const start = vi.fn();
    const result = vi.fn();
    bus.on("tool:start", start);
    bus.on("tool:result", result);

    await registry.execute(
      "mcp_github_search",
      {},
      { cwd: "/tmp", engineContext: { sessionId: "s1" } as any },
    );

    expect(start).toHaveBeenCalledTimes(1);
    expect(result).toHaveBeenCalledTimes(1);
    expect((start.mock.calls[0][0] as any).toolName).toBe("mcp_github_search");
    expect((result.mock.calls[0][0] as any).success).toBe(true);
  });

  it("records MCP tool execution to ToolTracker like a builtin", async () => {
    const bus = new GatewayEventBus();
    const registry = new ToolRegistry();
    registry.setEventBus(bus);

    const recordSuccess = vi.fn();
    const recordFailure = vi.fn();
    registry.setTracker({
      recordSuccess,
      recordFailure,
    } as any);

    registry.register({
      definition: {
        name: "mcp_postgres_query",
        description: "query postgres via MCP",
        parameters: { type: "object", properties: {} },
      },
      category: "external",
      source: "mcp",
      execute: async () => "row1",
    });

    await registry.execute(
      "mcp_postgres_query",
      {},
      { cwd: "/tmp", engineContext: { sessionId: "s2" } as any },
    );

    expect(recordSuccess).toHaveBeenCalledWith(
      "mcp_postgres_query",
      expect.any(Number),
      expect.objectContaining({ sessionId: "s2" }),
    );
  });

  it("emits tool:result success=false on MCP tool failure", async () => {
    const bus = new GatewayEventBus();
    const registry = new ToolRegistry();
    registry.setEventBus(bus);

    registry.register({
      definition: {
        name: "mcp_failing",
        description: "fails on purpose",
        parameters: { type: "object", properties: {} },
      },
      category: "external",
      source: "mcp",
      execute: async () => {
        throw new Error("upstream MCP server crashed");
      },
    });

    const result = vi.fn();
    bus.on("tool:result", result);

    await expect(
      registry.execute(
        "mcp_failing",
        {},
        { cwd: "/tmp", engineContext: { sessionId: "s3" } as any },
      ),
    ).rejects.toThrow();

    expect(result).toHaveBeenCalled();
    expect((result.mock.calls[0][0] as any).success).toBe(false);
  });
});
