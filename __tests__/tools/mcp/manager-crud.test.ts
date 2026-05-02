// __tests__/tools/mcp/manager-crud.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MCPManager } from "../../../src/tools/mcp/manager.js";
import type { ToolRegistry } from "../../../src/tools/registry.js";
import type { StackOwlConfig } from "../../../src/config/loader.js";

const mockRegistry = {
  register: vi.fn(),
  unregister: vi.fn(),
  reindexTools: vi.fn(),
} as unknown as ToolRegistry;

const mockConfig: StackOwlConfig = {
  defaultProvider: "anthropic",
  defaultModel: "claude-sonnet",
  workspace: "workspace",
  mcp: { servers: [] },
} as unknown as StackOwlConfig;

const mockSaveConfig = vi.fn().mockResolvedValue(undefined);

describe("MCPManager CRUD", () => {
  let manager: MCPManager;

  beforeEach(() => {
    vi.clearAllMocks();
    manager = new MCPManager();
  });

  it("addServer calls saveConfig after successful connect", async () => {
    const connectSpy = vi
      .spyOn(manager, "connect")
      .mockResolvedValue(2);

    const newServer = {
      name: "test-server",
      transport: "stdio" as const,
      command: "echo",
      description: "Test server",
      installedAt: "2026-05-02T00:00:00.000Z",
    };

    await manager.addServer(newServer, mockRegistry, mockConfig, "/tmp", mockSaveConfig);

    expect(connectSpy).toHaveBeenCalledWith(
      expect.objectContaining({ name: "test-server" }),
      mockRegistry,
    );
    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: { servers: expect.arrayContaining([expect.objectContaining({ name: "test-server" })]) },
    }));
  });

  it("addServer does NOT call saveConfig when connect fails", async () => {
    vi.spyOn(manager, "connect").mockRejectedValue(new Error("connection refused"));

    const newServer = {
      name: "bad-server",
      transport: "stdio" as const,
      command: "nonexistent-cmd",
    };

    await expect(
      manager.addServer(newServer, mockRegistry, mockConfig, "/tmp", mockSaveConfig),
    ).rejects.toThrow("connection refused");
    expect(mockSaveConfig).not.toHaveBeenCalled();
  });

  it("removeServer disconnects and calls saveConfig", async () => {
    const disconnectSpy = vi.spyOn(manager, "disconnect").mockImplementation(() => {});
    mockConfig.mcp = {
      servers: [{ name: "old-server", transport: "stdio" as const }],
    };

    await manager.removeServer("old-server", mockRegistry, mockConfig, "/tmp", mockSaveConfig);

    expect(disconnectSpy).toHaveBeenCalledWith("old-server", mockRegistry);
    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: { servers: [] },
    }));
  });

  it("updateServer patches config in-place and calls saveConfig", async () => {
    const reconnectSpy = vi.spyOn(manager, "reconnect").mockResolvedValue(3);
    mockConfig.mcp = {
      servers: [{ name: "my-server", transport: "stdio" as const, command: "npx" }],
    };

    await manager.updateServer(
      "my-server",
      { description: "Updated desc", enabled: false },
      mockRegistry,
      mockConfig,
      "/tmp",
      mockSaveConfig,
    );

    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: {
        servers: [expect.objectContaining({ name: "my-server", description: "Updated desc", enabled: false })],
      },
    }));
    expect(reconnectSpy).toHaveBeenCalledWith("my-server", mockRegistry);
  });

  it("updateServer rolls back config patch when reconnect fails", async () => {
    vi.spyOn(manager, "reconnect").mockRejectedValue(new Error("reconnect failed"));
    mockConfig.mcp = {
      servers: [{ name: "my-server", transport: "stdio" as const, command: "old-cmd" }],
    };

    await expect(
      manager.updateServer(
        "my-server",
        { command: "new-cmd" },
        mockRegistry,
        mockConfig,
        "/tmp",
        mockSaveConfig,
      ),
    ).rejects.toThrow("reconnect failed");

    // Config must be rolled back to original value
    expect(mockConfig.mcp!.servers[0]!.command).toBe("old-cmd");
    expect(mockSaveConfig).not.toHaveBeenCalled();
  });
});
