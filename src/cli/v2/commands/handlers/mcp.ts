import type { CommandHandler, CommandContext } from "../registry.js";
import { McpCommandRouter } from "../../../../gateway/commands/mcp-router.js";
import { saveConfig } from "../../../../config/loader.js";
import { globalBridge } from "../../events/bridge.js";

function getDeps(ctx: CommandContext) {
  const gateway = ctx.getOwlGateway();
  const mcpManager = gateway.getMcpManager();
  const toolRegistry = gateway.getToolRegistry();
  if (!mcpManager || !toolRegistry) return null;
  return {
    mcpManager,
    toolRegistry,
    config: gateway.getConfig(),
    basePath: gateway.getWorkspacePath(),
    saveConfig,
  };
}

function textToItems(text: string): Array<{ id: string; label: string }> {
  return text
    .split("\n")
    .filter((l) => l.trim())
    .map((line, i) => ({ id: `line-${i}`, label: line }));
}

export async function completeMcpServers(ctx: CommandContext, partial: string): Promise<string[]> {
  const mcpManager = ctx.getMcpManager();
  return mcpManager.listServers().map((s) => s.name).filter((n) => n.startsWith(partial));
}

export const handleMcpList: CommandHandler = async (ctx, _args) => {
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("list", [], deps);
  const lines = text.split("\n").filter((l) => l.trim());
  const items = lines.map((line, i) => {
    const match = line.match(/^(🟢|🔴)\s+(\S+)\s+\((\d+)\s+tools?\)$/);
    return match
      ? {
          id: `srv-${i}`,
          label: match[2]!,
          meta: `${match[1]} ${match[3]} tool${match[3] !== "1" ? "s" : ""}`,
        }
      : { id: `srv-${i}`, label: line.trim() };
  });
  const actions = [
    {
      key: "t",
      label: "tools",
      handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
        const serverName = item.label;
        const freshDeps = getDeps(ctx);
        if (!freshDeps) return;
        const toolsText = await McpCommandRouter.dispatch("tools", [serverName], freshDeps);
        globalBridge.openPanel("mcp-tools", {
          title: `/mcp tools ${serverName}`,
          items: textToItems(toolsText),
        });
      },
    },
    {
      key: "r",
      label: "reconnect",
      handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
        const serverName = item.label;
        const freshDeps = getDeps(ctx);
        if (!freshDeps) return;
        await McpCommandRouter.dispatch("reconnect", [serverName], freshDeps);
      },
    },
    {
      key: "d",
      label: "remove",
      confirm: "Type 'yes' to confirm removal",
      handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
        const serverName = item.label;
        const freshDeps = getDeps(ctx);
        if (!freshDeps) return;
        await McpCommandRouter.dispatch("remove", [serverName], freshDeps);
        ctx.bridge.closePanel();
      },
    },
  ];
  return {
    kind: "panel",
    payload: { title: "/mcp list", items, actions, emptyText: "No MCP servers configured." },
  };
};

export const handleMcpStatus: CommandHandler = async (ctx, _args) => {
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("status", [], deps);
  return {
    kind: "panel",
    payload: { title: "/mcp status", items: textToItems(text) },
  };
};

export const handleMcpAdd: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp add <npm-package>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("add", args, deps);
  return { kind: "system-message", text };
};

export const handleMcpRemove: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp remove <server-name>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("remove", args, deps);
  return { kind: "system-message", text };
};

export const handleMcpEnable: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp enable <server-name>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("enable", args, deps);
  return { kind: "system-message", text };
};

export const handleMcpDisable: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp disable <server-name>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("disable", args, deps);
  return { kind: "system-message", text };
};

export const handleMcpTools: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp tools <server-name>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("tools", args, deps);
  return {
    kind: "panel",
    payload: { title: `/mcp tools ${args[0]}`, items: textToItems(text) },
  };
};

export const handleMcpReconnect: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /mcp reconnect <server-name>" };
  const deps = getDeps(ctx);
  if (!deps) return { kind: "error", text: "MCP not configured." };
  const text = await McpCommandRouter.dispatch("reconnect", args, deps);
  return { kind: "system-message", text };
};
