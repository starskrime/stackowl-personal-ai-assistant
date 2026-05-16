import type { UiBridge } from "../events/bridge.js";
import type { UiState } from "../state/store.js";
import type { PanelItem } from "../panels/Panel.js";
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
import { handleConfigList, handleConfigTiers, handleConfigSetTier } from "./handlers/config.js";
import { handleConfigProvider } from "./handlers/config/provider.js";
import { handleConfigTier }     from "./handlers/config/tier.js";
import { handleConfigEngine }   from "./handlers/config/engine.js";
import { handleConfigCost }     from "./handlers/config/cost.js";
import { handleConfigChannel }  from "./handlers/config/channel.js";
import { handleConfigGateway }  from "./handlers/config/gateway.js";
import { handleConfigParliament } from "./handlers/config/parliament.js";
import { handleConfigHeartbeat }  from "./handlers/config/heartbeat.js";
import { handleConfigLogging }    from "./handlers/config/logging.js";
import { handleConfigResearch }   from "./handlers/config/research.js";
import { handleConfigPellets }    from "./handlers/config/pellets.js";
import { handleConfigBrowser }    from "./handlers/config/browser.js";
import { handleConfigMcp }        from "./handlers/config/mcp.js";
import { handleConfigGlobal }     from "./handlers/config/global.js";
import {
  handleProviderList,
  handleProviderTest,
  handleProviderDelete,
  handleProviderEdit,
} from "./handlers/provider.js";
import {
  handleOwlList,
  handleOwlShow,
  handleOwlCreate,
  handleOwlFromBmad,
  handleOwlDelete,
  handleOwlPin,
  handleOwlUnpin,
  handleOwlSwitch,
} from "./handlers/owl.js";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PanelPayload {
  title: string;
  color?: string;
  items: PanelItem[];
  actions?: Array<{
    key: string;
    label: string;
    handler: (item: PanelItem) => void | Promise<void>;
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
      const actions = [
        {
          key: "return",
          label: "resume",
          handler: (item: PanelItem) => {
            ctx.bridge.emit({ kind: "session.changed" as const, sessionId: item.id, title: item.label });
            ctx.bridge.closePanel();
          },
        },
        {
          key: "d",
          label: "delete",
          confirm: "Type 'yes' to confirm deletion",
          handler: async (item: { id: string; label: string; meta?: string; data?: unknown }) => {
            const sessionStore = ctx.getOwlGateway().getSessionStore();
            if (sessionStore && typeof (sessionStore as unknown as Record<string, unknown>).deleteSession === "function") {
              await (sessionStore as unknown as { deleteSession: (id: string) => Promise<void> }).deleteSession(item.id);
            }
            ctx.bridge.closePanel();
          },
        },
      ];
      return {
        kind: "panel",
        payload: { title: "/sessions", items, actions, emptyText: "No sessions yet." },
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
    name: "/owl",
    description: "Manage owls — switch, list, show, create, pin, delete",
    subcommands: [
      { name: "list",      description: "List all owls (BMAD + custom + builtin)", handler: handleOwlList },
      { name: "switch",    description: "Switch to an owl by name", args: [{ name: "<name>" }], handler: handleOwlSwitch },
      { name: "show",      description: "Show owl details",      args: [{ name: "<name>" }], handler: handleOwlShow },
      { name: "status",    description: "Active owl DNA state",  handler: handleOwlStatus },
      { name: "create",    description: "Create a custom owl (interactive)", handler: handleOwlCreate },
      { name: "from-bmad", description: "Create owl from BMAD template", args: [{ name: "[name]" }], handler: handleOwlFromBmad },
      { name: "delete",    description: "Delete a custom owl",   args: [{ name: "<name>" }], handler: handleOwlDelete },
      { name: "pin",       description: "Pin owl for session",   args: [{ name: "<name>" }], handler: handleOwlPin },
      { name: "unpin",     description: "Unpin active owl",      handler: handleOwlUnpin },
    ],
    handler: handleOwlList,
  },
  {
    name: "/provider",
    description: "Manage AI providers — list, edit, delete, test",
    subcommands: [
      { name: "list",   description: "List all configured providers",                                                                                           handler: handleProviderList },
      { name: "test",   description: "Test provider connection",         args: [{ name: "<name>" }],                                                            handler: handleProviderTest },
      { name: "delete", description: "Delete a provider",                args: [{ name: "<name>" }],                                                            handler: handleProviderDelete },
      { name: "edit",   description: "Edit provider field",              args: [{ name: "<name>" }, { name: "<key|model|url>" }, { name: "<value>" }],          handler: handleProviderEdit },
    ],
    handler: handleProviderList,
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
    name: "/config",
    description: "View and edit runtime config — /config <namespace> <verb> [args]",
    handler: async (ctx, args) => {
      if (args.length === 0) return handleConfigList(ctx, args);
      return handleConfigGlobal(ctx, args);
    },
    subcommands: [
      // ── Legacy subcommands (kept for backward compatibility) ──────────────────
      {
        name: "tiers",
        description: "Browse model tiers (low / mid / high)",
        handler: handleConfigTiers,
      },
      {
        name: "set-tier",
        description: "Set provider and model for a tier",
        args: [
          { name: "tier",     description: "low | mid | high" },
          { name: "provider", description: "provider name (e.g. anthropic, minimax)" },
          { name: "model",    description: "model name (e.g. claude-haiku-4-5-20251001)" },
        ],
        handler: handleConfigSetTier,
      },
      // ── Namespace subcommands ─────────────────────────────────────────────────
      {
        name: "provider",
        description: "Manage AI providers — list, add, remove, set-key, set-model, set-url, test",
        handler: handleConfigProvider,
      },
      {
        name: "tier",
        description: "Manage model tiers — list, set, set-default, reset",
        handler: handleConfigTier,
      },
      {
        name: "engine",
        description: "Tune the ReAct engine — list, set <key> <value>, reset",
        handler: handleConfigEngine,
      },
      {
        name: "cost",
        description: "Cost tracking and budget — list, enable, disable, set-budget, reset",
        handler: handleConfigCost,
      },
      {
        name: "channel",
        description: "Communication channels — list, telegram, slack, discord, whatsapp",
        handler: handleConfigChannel,
      },
      {
        name: "gateway",
        description: "HTTP gateway settings — list, set-port, set-host, set-output-mode, rate-limit",
        handler: handleConfigGateway,
      },
      {
        name: "parliament",
        description: "Multi-owl debate settings — list, set-rounds, set-owls",
        handler: handleConfigParliament,
      },
      {
        name: "heartbeat",
        description: "Proactive notifications — list, enable, disable, set-interval, set-cooldown",
        handler: handleConfigHeartbeat,
      },
      {
        name: "logging",
        description: "Structured logging — list, set-level, set-retention, sink",
        handler: handleConfigLogging,
      },
      {
        name: "research",
        description: "Deep-research behavior — list, set, enable-auto-deep, disable-auto-deep",
        handler: handleConfigResearch,
      },
      {
        name: "pellets",
        description: "Knowledge pellet store — list, set-embedding-model, set-cache-size, dedup",
        handler: handleConfigPellets,
      },
      {
        name: "browser",
        description: "Browser pool — list, enable, disable, set-pool-size, set-proxy, stealth",
        handler: handleConfigBrowser,
      },
      {
        name: "mcp",
        description: "MCP server connections — list, add, remove, enable, disable",
        handler: handleConfigMcp,
      },
      // ── Global operations ─────────────────────────────────────────────────────
      {
        name: "validate",
        description: "Validate current config for consistency errors",
        handler: async (ctx) => handleConfigGlobal(ctx, ["validate"]),
      },
      {
        name: "show",
        description: "Print full config (secrets masked)",
        handler: async (ctx) => handleConfigGlobal(ctx, ["show"]),
      },
      {
        name: "diff",
        description: "Show differences between live config and on-disk config",
        handler: async (ctx) => handleConfigGlobal(ctx, ["diff"]),
      },
      {
        name: "reload",
        description: "Hot-reload all reloadable sections from disk",
        handler: async (ctx) => handleConfigGlobal(ctx, ["reload"]),
      },
      {
        name: "export",
        description: "Export config to a JSON file",
        handler: async (ctx, args) => handleConfigGlobal(ctx, ["export", ...args]),
      },
      {
        name: "import",
        description: "Import config from a JSON file",
        args: [{ name: "<path>", description: "Path to JSON config file" }],
        handler: async (ctx, args) => handleConfigGlobal(ctx, ["import", ...args]),
      },
    ],
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
    aliases: ["/exit"],
    description: "Save session and exit",
    handler: async (_ctx) => {
      return { kind: "action" };
    },
  },
];
