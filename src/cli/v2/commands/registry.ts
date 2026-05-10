import type { UiBridge } from "../events/bridge.js";
import type { UiState } from "../state/store.js";
import { handleStatus } from "./handlers/status.js";
import { handleClear }  from "./handlers/clear.js";
import {
  handleCapabilities,
  handleLearning,
  handleOwlStatus,
  handleHelp,
} from "./handlers/misc.js";
import {
  handleMemoryList,
  handleMemorySearch,
  handleMemoryGet,
  handleMemoryInvalidate,
  handleMemoryStats,
  handleMemoryHistory,
  handleMemoryExport,
  completeMemoryKeys,
} from "./handlers/memory.js";
import {
  handleMcpList,
  handleMcpStatus,
  handleMcpAdd,
  handleMcpRemove,
  handleMcpEnable,
  handleMcpDisable,
  handleMcpTools,
  handleMcpReconnect,
  completeMcpServers,
} from "./handlers/mcp.js";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PanelPayload {
  title: string;
  color?: string;
  items: Array<{ id: string; label: string; meta?: string; data?: unknown }>;
  actions?: Array<{
    key: string;
    label: string;
    handler: (item: { id: string; label: string; meta?: string; data?: unknown }) => void | Promise<void>;
    confirm?: string;
  }>;
  emptyText?: string;
}

export type CommandResult =
  | { kind: "panel"; payload: PanelPayload }
  | { kind: "system-message"; text: string }
  | { kind: "action" }
  | { kind: "error"; text: string };

export interface CommandContext {
  getMemoryRepo: () => import("../../../memory/repository.js").MemoryRepository;
  getMcpManager: () => import("../../../tools/mcp/manager.js").MCPManager;
  getOwlGateway: () => import("../../../gateway/core.js").OwlGateway;
  bridge: UiBridge;
  getStore: () => UiState;
}

export type CommandHandler = (ctx: CommandContext, args: string[]) => Promise<CommandResult>;

export interface ArgSpec {
  name: string;
  description?: string;
}

export interface SubcommandSpec {
  name: string;
  description: string;
  args?: ArgSpec[];
  complete?: (ctx: CommandContext, partial: string) => Promise<string[]>;
  handler: CommandHandler;
}

export interface CommandSpec {
  name: string;
  aliases?: string[];
  description: string;
  subcommands?: SubcommandSpec[];
  handler?: CommandHandler;
}

// ─── Resolve helper ───────────────────────────────────────────────────────────

export interface ResolvedCommand {
  spec: CommandSpec;
  subcommand?: SubcommandSpec;
  args: string[];
}

export function resolveCommand(input: string): ResolvedCommand | null {
  const parts = input.trim().split(/\s+/);
  const cmdName = parts[0] ?? "";

  const spec = REGISTRY.find(
    (s) => s.name === cmdName || (s.aliases ?? []).includes(cmdName),
  );
  if (!spec) return null;

  if (spec.subcommands && parts[1]) {
    const sub = spec.subcommands.find((s) => s.name === parts[1]);
    if (sub) return { spec, subcommand: sub, args: parts.slice(2) };
    return { spec, args: parts.slice(1) };
  }

  return { spec, args: parts.slice(1) };
}

// ─── Placeholder handlers ─────────────────────────────────────────────────────

async function notImplemented(_ctx: CommandContext, _args: string[]): Promise<CommandResult> {
  return { kind: "error", text: "Command not yet implemented in v2." };
}

// ─── Registry ─────────────────────────────────────────────────────────────────

export const REGISTRY: CommandSpec[] = [
  {
    name: "/help",
    aliases: ["/?"],
    description: "Show available commands",
    handler: handleHelp,
  },
  {
    name: "/sessions",
    description: "Browse and resume sessions",
    handler: async (ctx) => {
      const { recentSessions } = ctx.getStore();
      const items = recentSessions.map((s) => ({
        id: s.sessionId,
        label: s.title || s.sessionId.slice(0, 24),
        meta: new Date(s.lastActiveAt).toLocaleDateString(),
        data: s,
      }));
      return {
        kind: "panel",
        payload: { title: "/sessions", items, emptyText: "No sessions yet." },
      };
    },
  },
  {
    name: "/owls",
    description: "Browse and switch owl personas",
    handler: async (ctx) => {
      const { availableOwls } = ctx.getStore();
      const items = availableOwls.map((o) => ({
        id: o.name,
        label: `${o.emoji} ${o.name}`,
        meta: o.isActive ? "active" : o.description.slice(0, 40),
        data: o,
      }));
      return {
        kind: "panel",
        payload: { title: "/owls", items, emptyText: "No owls loaded." },
      };
    },
  },
  {
    name: "/skills",
    description: "List installed skills",
    handler: async (ctx) => {
      const { installedSkills } = ctx.getStore();
      const items = installedSkills.map((s) => ({
        id: s.name,
        label: s.name,
        meta: s.enabled ? "✓ enabled" : "✗ disabled",
        data: s,
      }));
      return {
        kind: "panel",
        payload: { title: "/skills", items, emptyText: "No skills loaded." },
      };
    },
  },
  {
    name: "/mcp",
    description: "Manage MCP servers",
    subcommands: [
      { name: "list",      description: "List all configured MCP servers",                               handler: handleMcpList },
      { name: "status",    description: "Full status report",                                            handler: handleMcpStatus },
      { name: "add",       description: "Install + connect a server",      args: [{ name: "<package>" }], handler: handleMcpAdd },
      { name: "install",   description: "Alias for add",                   args: [{ name: "<package>" }], handler: handleMcpAdd },
      { name: "remove",    description: "Remove a server",                 args: [{ name: "<name>" }],    handler: handleMcpRemove,    complete: completeMcpServers },
      { name: "enable",    description: "Enable a server",                 args: [{ name: "<name>" }],    handler: handleMcpEnable,    complete: completeMcpServers },
      { name: "disable",   description: "Disable a server",                args: [{ name: "<name>" }],    handler: handleMcpDisable,   complete: completeMcpServers },
      { name: "tools",     description: "List tools for a server",         args: [{ name: "<name>" }],    handler: handleMcpTools,     complete: completeMcpServers },
      { name: "reconnect", description: "Reconnect a server",              args: [{ name: "<name>" }],    handler: handleMcpReconnect, complete: completeMcpServers },
    ],
    handler: handleMcpList,
  },
  {
    name: "/memory",
    aliases: ["/mem"],
    description: "View and manage memory",
    subcommands: [
      { name: "list",       description: "List all memory entries",              handler: handleMemoryList },
      { name: "search",     description: "Search memory",  args: [{ name: "<query>" }], handler: handleMemorySearch },
      { name: "get",        description: "Show one entry", args: [{ name: "<key>" }],   handler: handleMemoryGet,        complete: completeMemoryKeys },
      { name: "invalidate", description: "Delete an entry",args: [{ name: "<key>" }],   handler: handleMemoryInvalidate, complete: completeMemoryKeys },
      { name: "stats",      description: "Memory statistics",                    handler: handleMemoryStats },
      { name: "history",    description: "View invalidation history", args: [{ name: "<id>" }], handler: handleMemoryHistory, complete: completeMemoryKeys },
      { name: "export",     description: "JSON dump of all valid memories",      handler: handleMemoryExport },
    ],
    handler: handleMemoryList,
  },
  {
    name: "/helper",
    description: "Manage helper owl personas",
    subcommands: [
      { name: "list",         description: "List all helpers",             handler: notImplemented },
      { name: "show",         description: "Show helper details", args: [{ name: "<name>" }], handler: notImplemented },
      { name: "create",       description: "Create a new helper",          handler: notImplemented },
      { name: "rename",       description: "Rename a helper",  args: [{ name: "<old>" }, { name: "<new>" }], handler: notImplemented },
      { name: "delete",       description: "Delete a helper",  args: [{ name: "<name>" }], handler: notImplemented },
      { name: "design",       description: "Redesign a helper",args: [{ name: "<name>" }], handler: notImplemented },
      { name: "capabilities", description: "List helper capabilities",     handler: notImplemented },
    ],
    handler: notImplemented,
  },
  {
    name: "/owl",
    description: "Show current owl status",
    subcommands: [
      { name: "status", description: "Show owl state + memory stats", handler: handleOwlStatus },
    ],
    handler: handleOwlStatus,
  },
  {
    name: "/status",
    description: "Show provider, model, and owl info",
    handler: handleStatus,
  },
  {
    name: "/clear",
    aliases: ["/reset"],
    description: "Clear conversation context",
    handler: handleClear,
  },
  {
    name: "/capabilities",
    description: "List synthesized capabilities",
    handler: handleCapabilities,
  },
  {
    name: "/learning",
    description: "Show learning report",
    handler: handleLearning,
  },
  {
    name: "/onboarding",
    description: "Re-run setup wizard",
    handler: async (ctx) => {
      ctx.bridge.requestOnboardingView();
      return { kind: "action" };
    },
  },
  {
    name: "/quit",
    aliases: ["/exit", "/bye"],
    description: "Save session and exit",
    handler: async (_ctx) => {
      return { kind: "action" };
    },
  },
];
