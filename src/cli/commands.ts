/**
 * StackOwl — CLI Command Registry
 *
 * All output goes through ui.printLines() / ui.printInfo() / ui.printError()
 * so it renders inside the split-panel window, never to raw stdout.
 */

import chalk from "chalk";
import type { OwlGateway } from "../gateway/core.js";
import type { TerminalRenderer } from "./renderer.js";
import type { CompletionProvider } from "./completion-engine.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import { McpCommandRouter } from "../gateway/commands/mcp-router.js";
import { dispatchMemoryCommand } from "../gateway/commands/memory-router.js";
import { saveConfig } from "../config/loader.js";

// ─── Types ────────────────────────────────────────────────────────

type CommandFn = (
  args: string,
  ui: TerminalRenderer,
  gateway: OwlGateway,
) => Promise<boolean>;

interface CommandDef {
  description: string;
  fn: CommandFn;
  subcommands?: string[];
}

// ─── Helpers ─────────────────────────────────────────────────────

const YB = chalk.yellow.bold;
const D = chalk.dim;
const C = chalk.cyan;
const W = chalk.white;
const G = chalk.green;
const R = chalk.red;

function sep() {
  return D("─".repeat(40));
}

// ─── Shared Lookup ────────────────────────────────────────────────

export function resolveOwl(
  name: string,
  registry: SpecializedOwlRegistry | undefined,
): SpecializedOwlSpec | null {
  return registry?.get(name) ?? null;
}

// ─── Commands ─────────────────────────────────────────────────────

const cmdHelp: CommandFn = async (_args, ui) => {
  ui.printLines([
    "",
    YB("Commands"),
    sep(),
    C("/help".padEnd(20)) + D("Show this list"),
    C("/status".padEnd(20)) + D("Provider, model, owl info"),
    C("/clear".padEnd(20)) + D("Clear conversation context"),
    C("/capabilities".padEnd(20)) + D("List synthesized tools"),
    C("/skills".padEnd(20)) + D("List or install skills"),
    C("/learning".padEnd(20)) + D("Show learning report"),
    C("/memory".padEnd(20)) + D("Memory CRUD (list/search/stats/...)"),
    C("/owl".padEnd(20)) + D("Manage owls (list/show/create/pin/delete)"),
    C("/onboarding".padEnd(20)) + D("Re-run setup wizard"),
    C("/quit".padEnd(20)) + D("Save session and exit"),
    "",
  ]);
  return true;
};

const cmdStatus: CommandFn = async (_args, ui, gateway) => {
  const owl = gateway.getOwl();
  const config = gateway.getConfig();
  ui.printLines([
    "",
    YB("Status"),
    sep(),
    D("Provider  ") + W(config.defaultProvider),
    D("Model     ") + W(config.defaultModel),
    D("Owl       ") + W(`${owl.persona.emoji} ${owl.persona.name}`),
    D("DNA Gen   ") + W(String(owl.dna.generation)),
    D("Challenge ") + W(String(owl.dna.evolvedTraits.challengeLevel)),
    "",
  ]);
  return true;
};


const cmdClear: CommandFn = async (_args, ui, gateway) => {
  const { makeMessageId, makeSessionId } = await import("../gateway/core.js");
  await gateway.handle({
    id: makeMessageId(),
    channelId: "cli",
    userId: "local",
    sessionId: makeSessionId("cli", "local"),
    text: "/reset",
  });
  ui.printInfo("Context cleared.");
  return true;
};

const cmdCapabilities: CommandFn = async (_args, ui, gateway) => {
  const evolution = gateway.getEvolution();
  if (!evolution) {
    ui.printInfo("Evolution system not available.");
    return true;
  }

  const records = await evolution.listAll();
  if (records.length === 0) {
    ui.printInfo("No synthesized tools yet.");
    return true;
  }

  const lines: string[] = ["", YB("Synthesized Tools"), sep()];
  for (const r of records) {
    const icon =
      r.status === "active" ? G("✓") : r.status === "failed" ? R("✗") : D("○");
    lines.push(icon + " " + W(r.toolName));
    lines.push(D(`   ${r.description}`));
    lines.push(D(`   Used: ${r.timesUsed}x · ${r.status}`));
    lines.push("");
  }
  ui.printLines(lines);
  return true;
};

const cmdLearning: CommandFn = async (_args, ui, gateway) => {
  const orchestrator = gateway.getLearningOrchestrator();
  if (!orchestrator) {
    ui.printInfo("Learning engine not available.");
    return true;
  }

  const report = await orchestrator.getFullReport();
  const lines = ["", YB("Learning Report"), sep(), ...report.split("\n"), ""];
  ui.printLines(lines);
  return true;
};

const cmdQuit: CommandFn = async (_args, ui, _gateway) => {
  ui.emit("quit");
  return true;
};

const cmdOnboarding: CommandFn = async (_args, ui) => {
  ui.emit("onboarding");
  return true;
};

const cmdOwl: CommandFn = async (args, ui, gateway) => {
  const parts = args.trim().split(/\s+/).filter(Boolean);
  const verb = parts[0] || "list";
  const verbArgs = parts.slice(1);
  const registry = gateway.getSpecializedRegistry();
  if (registry) {
    await registry.loadAll(gateway.getWorkspacePath());
  }
  const { dispatchOwlCommand } = await import("../gateway/commands/owl-command.js");
  const adapter = {
    ask: async (_userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }) => {
      const { default: readline } = await import("node:readline");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      const choices = prompt.choices ? `\n${prompt.choices.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}` : "";
      const defaultHint = prompt.defaultChoice ? ` [${prompt.defaultChoice}]` : "";
      return new Promise<string>((resolve) => {
        rl.question(`${prompt.text}${choices}${defaultHint}\n> `, (ans) => {
          rl.close();
          if (!ans && prompt.defaultChoice) return resolve(prompt.defaultChoice);
          if (prompt.choices) {
            const idx = parseInt(ans) - 1;
            return resolve(!isNaN(idx) && prompt.choices[idx] ? prompt.choices[idx] : ans);
          }
          resolve(ans);
        });
      });
    },
  };
  const result = await dispatchOwlCommand(verb, verbArgs, {
    registry: registry as any,
    userId: "local",
    workspacePath: gateway.getWorkspacePath(),
    channelAdapter: adapter,
    gateway: gateway as any,
  });
  ui.printLines(["", ...result.split("\n"), ""]);
  return true;
};

const cmdMcp: CommandFn = async (args, ui, gateway) => {
  const mcpManager = gateway.getMcpManager();
  if (!mcpManager) {
    ui.printInfo("MCP manager not available.");
    return true;
  }
  const parts = args.trim().split(/\s+/).filter(Boolean);
  const verb = parts[0] || "status";
  const verbArgs = parts.slice(1);
  const config = gateway.getConfig();
  const basePath = gateway.getWorkspacePath();
  const result = await McpCommandRouter.dispatch(verb, verbArgs, {
    mcpManager,
    toolRegistry: gateway.getToolRegistry()!,
    config,
    basePath,
    saveConfig,
  });
  ui.printLines(["", ...result.split("\n"), ""]);
  return true;
};

const cmdMemory: CommandFn = async (args, ui, gateway) => {
  const repo = gateway.getMemoryRepo();
  if (!repo) {
    ui.printInfo("Memory repository not available.");
    return true;
  }
  const parts = args.trim().split(/\s+/).filter(Boolean);
  const verb = parts[0] || "list";
  const verbArgs = parts.slice(1);
  const out = await dispatchMemoryCommand(verb, verbArgs, { repo });
  ui.printLines(["", ...out.split("\n"), ""]);
  return true;
};

// ─── Registry ────────────────────────────────────────────────────

const COMMANDS: Record<string, CommandDef> = {
  help: { description: "Show command list", fn: cmdHelp },
  "?": { description: "Show command list", fn: cmdHelp },
  status: { description: "Provider / model / owl info", fn: cmdStatus },
  skills: {
    description: "List or install skills",
    fn: async (_args, _ui, _gateway) => false,
    subcommands: ["list", "install"],
  },
  clear: { description: "Clear context", fn: cmdClear },
  reset: { description: "Clear context", fn: cmdClear },
  capabilities: { description: "List synthesized tools", fn: cmdCapabilities },
  learning: { description: "Learning report", fn: cmdLearning },
  quit: { description: "Save and exit", fn: cmdQuit },
  exit: { description: "Save and exit", fn: cmdQuit },
  bye: { description: "Save and exit", fn: cmdQuit },
  onboarding: { description: "Re-run setup wizard", fn: cmdOnboarding },
  mcp: {
    description: "Manage MCP servers (add/remove/list/status/enable/disable)",
    fn: cmdMcp,
    subcommands: ["list", "status", "add", "remove", "enable", "disable", "tools", "reconnect", "install"],
  },
  memory: {
    description: "Memory CRUD: list/search/stats/history/get/invalidate/export",
    fn: cmdMemory,
    subcommands: ["list", "search", "stats", "history", "get", "invalidate", "export"],
  },
  owl: {
    description: "Manage owls",
    fn: cmdOwl,
    subcommands: ["list", "show", "status", "create", "from-bmad", "edit", "delete", "pin", "unpin"],
  },
};

export class CommandRegistry implements CompletionProvider {
  /** CompletionProvider — top-level command names */
  topLevelNames(): string[] {
    return Object.keys(COMMANDS);
  }

  /** CompletionProvider — subcommands for a given top-level command */
  subcommands(commandName: string): string[] {
    return COMMANDS[commandName]?.subcommands ?? [];
  }

  getDescription(name: string): string {
    return COMMANDS[name]?.description ?? "";
  }

  async handle(
    input: string,
    ui: TerminalRenderer,
    gateway: OwlGateway,
  ): Promise<boolean> {
    if (!input.startsWith("/")) return false;

    // Let /skills fall through to gateway.handle() for wizard routing
    if (input.toLowerCase().startsWith("/skills")) return false;

    const space = input.indexOf(" ");
    const name = (
      space === -1 ? input.slice(1) : input.slice(1, space)
    ).toLowerCase();
    const args = space === -1 ? "" : input.slice(space + 1);

    const def = COMMANDS[name];
    if (!def) {
      ui.printLines([
        R(`Unknown command "/${name}".`) + D("  Type /help for the list."),
        "",
      ]);
      return true;
    }

    return def.fn(args, ui, gateway);
  }

  paletteHint(): string {
    return Object.keys(COMMANDS)
      .filter((k) => !["?", "reset", "exit"].includes(k))
      .map((k) => chalk.cyan(`/${k}`))
      .join("  ");
  }
}

// ─── stackowl backends subcommand ───────────────────────────────
import type { ProbeMap } from "../runtime/availability.js";

export interface BackendsCommandDeps {
  availabilityPath?: string;
  probes?: ProbeMap;
  installer?: {
    camofox?: () => Promise<boolean>;
    scrapling?: () => Promise<boolean>;
    "live-browser"?: () => Promise<boolean>;
  };
  trackerStats?: Record<string, { success: number; total: number }>;
}

export async function backendsCommand(
  argv: string[],
  deps: BackendsCommandDeps = {},
): Promise<string> {
  const { RuntimeAvailability } = await import("../runtime/availability.js");
  const ra = new RuntimeAvailability(deps.availabilityPath, deps.probes);
  const sub = argv[0] ?? "list";

  if (sub === "list") {
    const map = await ra.load();
    return Object.entries(map)
      .map(([k, v]) =>
        `${k.padEnd(14)} installed=${v.installed} ready=${v.ready} ${v.version ? "v" + v.version : ""}`,
      )
      .join("\n");
  }

  if (sub === "repair") {
    const map = await ra.probeAll();
    return `repair complete:\n${Object.entries(map)
      .map(([k, v]) => `  ${k}: ready=${v.ready}`)
      .join("\n")}`;
  }

  if (sub === "stats") {
    const s = deps.trackerStats ?? {};
    const fmt = (name: string, label: string) => {
      const r = s[name];
      if (!r) return `${label}: no data`;
      return `${label}: success rate ${Math.round((r.success / r.total) * 100)}%  (n=${r.success}/${r.total})`;
    };
    return [
      "=== Web fetch stats (last 7 days) ===",
      fmt("http", "Tier 1 (http)       "),
      fmt("camofox", "Tier 2 (camofox)    "),
      fmt("scrapling", "Tier 3 (scrapling)  "),
    ].join("\n");
  }

  if (sub === "install") {
    const which = argv[1];
    if (!which || !(which in (deps.installer ?? {}))) {
      return "usage: stackowl backends install <camofox|scrapling|live-browser>";
    }
    const fn = deps.installer![which as keyof typeof deps.installer] as () => Promise<boolean>;
    const ok = await fn();
    return `install ${which}: ${ok ? "ok" : "failed"}`;
  }

  return "usage: stackowl backends list|install|repair|stats";
}
