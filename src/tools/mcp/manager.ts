/**
 * StackOwl — MCP Server Manager
 *
 * Manages connections to multiple MCP servers. Reads config,
 * connects to each server, discovers tools, and registers
 * them in the ToolRegistry with namespaced names.
 */

import { MCPClient, type MCPServerConfig } from "./client.js";
import type { ToolRegistry } from "../registry.js";
import { log } from "../../logger.js";

export class MCPManager {
  private clients: Map<string, MCPClient> = new Map();
  private toolNames: Map<string, string[]> = new Map(); // serverName → registered tool names

  /**
   * Connect to all configured MCP servers and register their tools.
   */
  async connectAll(
    servers: MCPServerConfig[],
    toolRegistry: ToolRegistry,
  ): Promise<number> {
    let totalTools = 0;

    for (const serverConfig of servers) {
      try {
        const count = await this.connect(serverConfig, toolRegistry);
        totalTools += count;
      } catch (err) {
        log.engine.warn(
          `[MCP] Failed to connect to "${serverConfig.name}": ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    return totalTools;
  }

  /**
   * Connect to a single MCP server and register its tools.
   */
  async connect(
    config: MCPServerConfig,
    toolRegistry: ToolRegistry,
  ): Promise<number> {
    const client = new MCPClient(config);
    await client.connect();

    const mcpTools = await client.listTools();
    const implementations = client.toToolImplementations();

    const registeredNames: string[] = [];
    for (const impl of implementations) {
      toolRegistry.register(impl);
      registeredNames.push(impl.definition.name);
    }

    this.clients.set(config.name, client);
    this.toolNames.set(config.name, registeredNames);

    log.engine.info(
      `[MCP] "${config.name}": registered ${mcpTools.length} tool(s): ${registeredNames.join(", ")}`,
    );

    return mcpTools.length;
  }

  /**
   * Disconnect a specific MCP server and unregister its tools.
   */
  disconnect(serverName: string, toolRegistry: ToolRegistry): void {
    const client = this.clients.get(serverName);
    if (client) {
      client.disconnect();
      this.clients.delete(serverName);
    }

    const names = this.toolNames.get(serverName) ?? [];
    for (const name of names) {
      toolRegistry.unregister(name);
    }
    this.toolNames.delete(serverName);

    log.engine.info(
      `[MCP] "${serverName}": disconnected, unregistered ${names.length} tool(s)`,
    );
  }

  /**
   * Disconnect all MCP servers.
   */
  disconnectAll(toolRegistry: ToolRegistry): void {
    for (const name of [...this.clients.keys()]) {
      this.disconnect(name, toolRegistry);
    }
  }

  /**
   * List connected MCP servers and their tool counts.
   */
  listServers(): { name: string; toolCount: number; connected: boolean }[] {
    return [...this.clients.entries()].map(([name, client]) => ({
      name,
      toolCount: this.toolNames.get(name)?.length ?? 0,
      connected: client.isConnected,
    }));
  }
}
