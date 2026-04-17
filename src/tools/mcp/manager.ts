/**
 * StackOwl — MCP Server Manager
 *
 * Manages connections to multiple MCP servers at runtime.
 * Responsibilities:
 *   - Boot-time: connect all servers declared in config
 *   - Runtime:   add / remove / reconnect individual servers
 *   - Status:    report connected servers + tool counts
 *   - Cleanup:   unregister tools when a server disconnects
 */

import { MCPClient, type MCPServerConfig } from "./client.js";
import type { ToolRegistry } from "../registry.js";
import { log } from "../../logger.js";

export type { MCPServerConfig };

export interface MCPServerStatus {
  name: string;
  transport: "stdio" | "sse";
  connected: boolean;
  toolCount: number;
  tools: string[];
}

export class MCPManager {
  private clients: Map<string, MCPClient> = new Map();
  /** serverName → list of prefixed tool names registered in the ToolRegistry */
  private toolNames: Map<string, string[]> = new Map();

  // ─── Boot-time ────────────────────────────────────────────────────

  /**
   * Connect to all MCP servers declared in config.
   * Individual failures are logged and skipped — they don't abort startup.
   * Returns total number of tools registered.
   */
  async connectAll(
    servers: MCPServerConfig[],
    toolRegistry: ToolRegistry,
  ): Promise<number> {
    let total = 0;
    for (const server of servers) {
      try {
        const count = await this.connect(server, toolRegistry);
        total += count;
      } catch (err) {
        log.engine.warn(
          `[MCP] Startup: failed to connect "${server.name}": ${
            err instanceof Error ? err.message : err
          }`,
        );
      }
    }
    return total;
  }

  // ─── Runtime management ───────────────────────────────────────────

  /**
   * Connect to a single MCP server and register its tools.
   * Safe to call at runtime (re-entrant for new servers).
   */
  async connect(
    config: MCPServerConfig,
    toolRegistry: ToolRegistry,
  ): Promise<number> {
    // Disconnect existing connection to the same server first
    if (this.clients.has(config.name)) {
      this.disconnect(config.name, toolRegistry);
    }

    const client = new MCPClient(config);
    await client.connect();

    const mcpTools = await client.listTools();
    const implementations = client.toToolImplementations();

    const registeredNames: string[] = [];
    for (const impl of implementations) {
      try {
        toolRegistry.register(impl);
        registeredNames.push(impl.definition.name);
      } catch (err) {
        log.engine.warn(
          `[MCP] "${config.name}" tool "${impl.definition.name}" skipped: ${
            err instanceof Error ? err.message : err
          }`,
        );
      }
    }

    this.clients.set(config.name, client);
    this.toolNames.set(config.name, registeredNames);

    log.engine.info(
      `[MCP] "${config.name}" (${config.transport}): registered ${registeredNames.length} tool(s)`,
    );

    toolRegistry.reindexTools();
    return mcpTools.length;
  }

  /**
   * Reconnect an already-configured server (re-uses existing config).
   */
  async reconnect(serverName: string, toolRegistry: ToolRegistry): Promise<number> {
    const existing = this.clients.get(serverName);
    if (!existing) {
      throw new Error(`No MCP server named "${serverName}" is registered.`);
    }

    // Read config from the existing client before disconnecting
    const config = (existing as any).config as MCPServerConfig;
    return this.connect(config, toolRegistry);
  }

  /**
   * Disconnect a server and unregister all its tools.
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

    toolRegistry.reindexTools();
    log.engine.info(
      `[MCP] "${serverName}": disconnected, unregistered ${names.length} tool(s)`,
    );
  }

  /**
   * Disconnect all servers and clean up.
   */
  disconnectAll(toolRegistry: ToolRegistry): void {
    for (const name of [...this.clients.keys()]) {
      this.disconnect(name, toolRegistry);
    }
  }

  /**
   * Dynamically resolve and connect an NPM/npx-published MCP server.
   * Example: connectNpx("@modelcontextprotocol/server-filesystem", registry, ["~/Desktop"])
   */
  async connectNpx(
    packageName: string,
    toolRegistry: ToolRegistry,
    args: string[] = [],
  ): Promise<number> {
    const safeName = packageName.replace(/[^a-zA-Z0-9_-]/g, "_");
    const config: MCPServerConfig = {
      name: safeName,
      transport: "stdio",
      command: "npx",
      args: ["-y", packageName, ...args],
    };
    log.engine.info(`[MCP] Resolving NPM package: ${packageName}`);
    return this.connect(config, toolRegistry);
  }

  // ─── Status ──────────────────────────────────────────────────────

  /**
   * List all registered MCP servers with their current status.
   */
  listServers(): MCPServerStatus[] {
    return [...this.clients.entries()].map(([name, client]) => ({
      name,
      transport: (client as any).config.transport as "stdio" | "sse",
      connected: client.isConnected,
      toolCount: this.toolNames.get(name)?.length ?? 0,
      tools: this.toolNames.get(name) ?? [],
    }));
  }

  /**
   * Get status for a single server by name.
   */
  getServer(name: string): MCPServerStatus | null {
    const client = this.clients.get(name);
    if (!client) return null;
    return {
      name,
      transport: (client as any).config.transport as "stdio" | "sse",
      connected: client.isConnected,
      toolCount: this.toolNames.get(name)?.length ?? 0,
      tools: this.toolNames.get(name) ?? [],
    };
  }

  /**
   * Format a human-readable status report (used by /mcp status command).
   */
  formatStatus(): string {
    const servers = this.listServers();
    if (servers.length === 0) {
      return "No MCP servers configured.";
    }

    const lines: string[] = [`📡 <b>MCP Servers</b> (${servers.length} configured)\n`];
    for (const s of servers) {
      const dot = s.connected ? "🟢" : "🔴";
      const transport = s.transport === "sse" ? "🌐 SSE" : "⚙️ stdio";
      lines.push(
        `${dot} <b>${s.name}</b>  <i>${transport}</i>  ${s.toolCount} tool(s)`,
      );
      if (s.tools.length > 0 && s.tools.length <= 8) {
        lines.push(
          `   ${s.tools.map((t) => `<code>${t}</code>`).join(", ")}`,
        );
      } else if (s.tools.length > 8) {
        const preview = s.tools.slice(0, 6).map((t) => `<code>${t}</code>`).join(", ");
        lines.push(`   ${preview} … +${s.tools.length - 6} more`);
      }
    }
    return lines.join("\n");
  }
}
