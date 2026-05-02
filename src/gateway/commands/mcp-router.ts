// src/gateway/commands/mcp-router.ts
/**
 * Channel-agnostic MCP command dispatcher.
 * Both CLI and Telegram adapters call McpCommandRouter.dispatch() instead of
 * duplicating verb-handling logic.
 */
import type { MCPManager } from "../../tools/mcp/manager.js";
import type { ToolRegistry } from "../../tools/registry.js";
import type { StackOwlConfig } from "../../config/loader.js";

type SaveConfigFn = (basePath: string, config: StackOwlConfig) => Promise<void>;

export interface McpRouterDeps {
  mcpManager: MCPManager;
  toolRegistry: ToolRegistry;
  config: StackOwlConfig;
  basePath: string;
  saveConfig: SaveConfigFn;
}

const USAGE = `Available sub-commands:
  list                        — list all configured servers
  status                      — full status report
  add <npm-package> [args…]   — install + connect an npx-published server
  remove <server-name>        — disconnect + delete from config
  enable <server-name>        — mark enabled:true, reconnect
  disable <server-name>       — mark enabled:false, disconnect
  tools <server-name>         — list tools exposed by a server
  reconnect <server-name>     — re-establish a dropped connection
  install <server-name>       — alias for add`;

export class McpCommandRouter {
  static async dispatch(
    verb: string,
    args: string[],
    deps: McpRouterDeps,
  ): Promise<string> {
    const { mcpManager, toolRegistry, config, basePath, saveConfig } = deps;

    switch (verb) {
      case "list": {
        const servers = mcpManager.listServers();
        if (servers.length === 0) return "No MCP servers configured.";
        return servers
          .map((s) => `${s.connected ? "🟢" : "🔴"} ${s.name} (${s.toolCount} tools)`)
          .join("\n");
      }

      case "status": {
        return mcpManager.formatStatus();
      }

      case "add":
      case "install": {
        const pkg = args[0];
        if (!pkg) return `Usage: /mcp ${verb} <npm-package> [args…]`;
        const pkgArgs = args.slice(1);
        config.mcp ??= { servers: [] };
        const serverCfg = {
          name: pkg.replace(/[^a-zA-Z0-9_-]/g, "_"),
          transport: "stdio" as const,
          command: "npx",
          args: ["-y", pkg, ...pkgArgs],
          installedAt: new Date().toISOString(),
        };
        const count = await mcpManager.addServer(serverCfg, toolRegistry, config, basePath, saveConfig);
        return `Connected ${pkg} — ${count} tool(s) registered.`;
      }

      case "remove": {
        const name = args[0];
        if (!name) return "Usage: /mcp remove <server-name>";
        await mcpManager.removeServer(name, toolRegistry, config, basePath, saveConfig);
        return `${name} disconnected and removed from config.`;
      }

      case "enable": {
        const name = args[0];
        if (!name) return "Usage: /mcp enable <server-name>";
        await mcpManager.updateServer(name, { enabled: true }, toolRegistry, config, basePath, saveConfig);
        return `${name} enabled and reconnected.`;
      }

      case "disable": {
        const name = args[0];
        if (!name) return "Usage: /mcp disable <server-name>";
        await mcpManager.updateServer(name, { enabled: false }, toolRegistry, config, basePath, saveConfig);
        return `${name} disabled.`;
      }

      case "tools": {
        const name = args[0];
        if (!name) return "Usage: /mcp tools <server-name>";
        const server = mcpManager.getServer(name);
        if (!server) return `Server "${name}" not found.`;
        if (server.tools.length === 0) return `${name} has no registered tools.`;
        return `${name} tools:\n${server.tools.map((t) => `  • ${t}`).join("\n")}`;
      }

      case "reconnect": {
        const name = args[0];
        if (!name) return "Usage: /mcp reconnect <server-name>";
        const count = await mcpManager.reconnect(name, toolRegistry);
        return `${name} reconnected — ${count} tool(s) available.`;
      }

      default:
        return `Unknown sub-command "${verb}".\n\n${USAGE}`;
    }
  }
}
