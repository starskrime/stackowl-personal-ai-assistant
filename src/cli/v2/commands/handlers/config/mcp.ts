/**
 * /config mcp <verb> — MCP server connections namespace.
 *
 * list | add <name> --transport <stdio|sse> [--command <cmd>] [--args <a,b>]
 *         [--url <url>] [--description <desc>]
 * | remove <name> --confirm
 * | enable <name> | disable <name>
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigMcp: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.mcp: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":    return mcpList(ctx);
    case "add":     return mcpAdd(ctx, rest);
    case "remove":  return mcpRemove(ctx, rest);
    case "enable":  return mcpToggle(ctx, rest, true);
    case "disable": return mcpToggle(ctx, rest, false);
    default:
      return {
        kind: "error",
        text: "Usage: /config mcp <list|add|remove|enable|disable>",
      };
  }
};

async function mcpList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.mcp.list: entry");
  const servers = ctx.getOwlGateway().getConfig().mcp?.servers ?? [];
  if (servers.length === 0) {
    return { kind: "system-message", text: "No MCP servers configured." };
  }
  const lines = servers.map((s) => {
    const state = s.enabled === false ? "disabled" : "enabled";
    const transport = s.transport === "stdio" ? `cmd=${s.command ?? "?"}` : `url=${s.url ?? "?"}`;
    return `  ${s.name.padEnd(16)} [${state}]  ${transport}  ${s.description ?? ""}`;
  });
  log.cli.debug("config.mcp.list: exit", { count: servers.length });
  return { kind: "system-message", text: ["MCP Servers:", ...lines].join("\n") };
}

async function mcpAdd(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.mcp.add: entry", { args });
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /config mcp add <name> --transport <stdio|sse> [options]" };

  const flags = parseFlags(args.slice(1));
  const transport = flags["transport"] as "stdio" | "sse" | undefined;
  if (transport !== "stdio" && transport !== "sse") {
    return { kind: "error", text: "Transport must be 'stdio' or 'sse'. Pass --transport <stdio|sse>." };
  }

  const cfg = ctx.getOwlGateway().getConfig();
  const existing = cfg.mcp?.servers ?? [];
  if (existing.some((s) => s.name === name)) {
    return { kind: "error", text: `MCP server "${name}" already exists.` };
  }

  const entry: (typeof existing)[number] = {
    name,
    transport,
    installedAt: new Date().toISOString(),
  };
  if (flags["command"]) entry.command = flags["command"];
  if (flags["args"])    entry.args = flags["args"]!.split(",").map((a) => a.trim());
  if (flags["url"])     entry.url = flags["url"];
  if (flags["description"]) entry.description = flags["description"];

  log.cli.debug("config.mcp.add: step — appending server", { name, transport });
  const result = await applyPatch(ctx, "mcp", { servers: [...existing, entry] }, { restartRequired: true });
  log.cli.debug("config.mcp.add: exit", { name });
  return result;
}

async function mcpRemove(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.mcp.remove: entry", { args });
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /config mcp remove <name> --confirm" };
  if (!args.includes("--confirm")) {
    return { kind: "error", text: `⚠ This removes MCP server "${name}". Re-run with --confirm to proceed.` };
  }

  const cfg = ctx.getOwlGateway().getConfig();
  const existing = cfg.mcp?.servers ?? [];
  if (!existing.some((s) => s.name === name)) {
    return { kind: "error", text: `MCP server "${name}" not found.` };
  }

  log.cli.debug("config.mcp.remove: step — removing server", { name });
  const result = await applyPatch(ctx, "mcp", { servers: existing.filter((s) => s.name !== name) }, { restartRequired: true });
  log.cli.debug("config.mcp.remove: exit", { name });
  return result;
}

async function mcpToggle(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
  enabled: boolean,
): Promise<CommandResult> {
  log.cli.debug("config.mcp.toggle: entry", { args, enabled });
  const name = args[0];
  if (!name) return { kind: "error", text: `Usage: /config mcp ${enabled ? "enable" : "disable"} <name>` };

  const cfg = ctx.getOwlGateway().getConfig();
  const existing = cfg.mcp?.servers ?? [];
  if (!existing.some((s) => s.name === name)) {
    return { kind: "error", text: `MCP server "${name}" not found.` };
  }

  const servers = existing.map((s) => s.name === name ? { ...s, enabled } : s);
  const result = await applyPatch(ctx, "mcp", { servers }, { restartRequired: true });
  log.cli.debug("config.mcp.toggle: exit", { name, enabled });
  return result;
}

function parseFlags(args: string[]): Record<string, string> {
  const flags: Record<string, string> = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i]!;
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const val = args[i + 1];
      if (val && !val.startsWith("--")) {
        flags[key] = val;
        i++;
      }
    }
  }
  return flags;
}
