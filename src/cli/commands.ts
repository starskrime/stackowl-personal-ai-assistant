/**
 * StackOwl — CLI Command Registry
 *
 * All output goes through ui.printLines() / ui.printInfo() / ui.printError()
 * so it renders inside the split-panel window, never to raw stdout.
 */

import chalk from "chalk";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import type { OwlGateway } from "../gateway/core.js";
import type { TerminalRenderer } from "./renderer.js";
import type { CompletionProvider } from "./completion-engine.js";
import { SpecializationCreateWizard } from "./specialization-wizard.js";
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

const Y = chalk.yellow;
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

// ─── Wizard State ─────────────────────────────────────────────────

let activeWizard: SpecializationCreateWizard | null = null;

// ─── Commands ─────────────────────────────────────────────────────

const cmdSpecialization: CommandFn = async (args, ui, gateway) => {
  const parts = args.trim().toLowerCase().split(/\s+/);
  const subcmd = parts[0] || "list";
  const registry = gateway.getSpecializedRegistry();

  if (subcmd === "list") {
    const owls = registry?.listAll() ?? [];
    if (owls.length === 0) {
      ui.printLines([
        "",
        YB("Helpers"),
        sep(),
        D("No helpers yet. Create one with /specialization create"),
        "",
      ]);
      return true;
    }
    const lines: string[] = ["", YB("Helpers"), sep()];
    for (const spec of owls) {
      lines.push(Y(`${spec.emoji || "🦉"} `) + W(spec.name.padEnd(16)) + D(spec.role));
    }
    lines.push(D(`\n${owls.length} owl(s) total`));
    lines.push("");
    ui.printLines(lines);
    return true;
  }

  if (subcmd === "show") {
    const name = parts.slice(1).join(" ");
    if (!name) { ui.printInfo("Usage: /specialization show <name>"); return true; }
    const spec = resolveOwl(name, registry);
    if (!spec) { ui.printInfo(`Owl "${name}" not found.`); return true; }
    const folderPath = join(gateway.getWorkspacePath(), "owls", spec.name);
    ui.printLines([
      "",
      YB(`${spec.emoji || "🦉"} ${spec.name}`),
      sep(),
      D("Role           ") + W(spec.role),
      D("Expertise      ") + W(spec.expertise.join(", ") || "(none)"),
      D("Challenge      ") + W(spec.personality.challengeLevel),
      D("Verbosity      ") + W(spec.personality.verbosity),
      D("Tone           ") + W(spec.personality.tone),
      "",
      YB("Routing Keywords"),
      sep(),
      ...(spec.routingRules.keywords.length > 0
        ? spec.routingRules.keywords.map((k) => D("  • " + k))
        : [D("  (none)")]),
      "",
      YB("Permissions"),
      sep(),
      D("Allowed Tools  ") + W(spec.permissions.allowedTools.join(", ") || "all"),
      D("Denied Tools   ") + W(spec.permissions.deniedTools.join(", ") || "none"),
      ...(spec.permissions.capabilityConstraints.length > 0
        ? [D("Constraints    ") + W(spec.permissions.capabilityConstraints.join("; "))]
        : []),
      "",
      YB("Config File"),
      sep(),
      D("  " + folderPath + "/specialized_owl.md"),
      "",
    ]);
    return true;
  }

  if (subcmd === "create") {
    activeWizard = new SpecializationCreateWizard(gateway.getWorkspacePath());
    activeWizard.start(ui);
    ui.setAllowEmptyInput(true);
    return true;
  }

  if (subcmd === "delete") {
    const lastPart = parts[parts.length - 1];
    const confirmed = (lastPart === "yes" || lastPart === "y") && parts.length > 2;
    const name = (confirmed ? parts.slice(1, -1) : parts.slice(1)).join(" ");
    if (!name) { ui.printInfo("Usage: /specialization delete <name>"); return true; }
    const spec = resolveOwl(name, registry);
    if (!spec) { ui.printInfo(`Owl "${name}" not found.`); return true; }
    if (confirmed) {
      const folderPath = join(gateway.getWorkspacePath(), "owls", spec.name);
      await rm(folderPath, { recursive: true, force: true });
      await gateway.reloadSpecializedRegistry();
      ui.printLines(["", G(`✓ Deleted owl: ${spec.name}`), ""]);
      return true;
    }
    ui.printLines([
      "",
      R(`⚠️  Delete "${spec.name}"?`),
      sep(),
      D("This action cannot be undone."),
      D("Confirm: /specialization delete " + spec.name + " yes"),
      "",
    ]);
    return true;
  }

  if (subcmd === "update") {
    const name = parts.slice(1).join(" ");
    if (!name) { ui.printInfo("Usage: /specialization update <name>"); return true; }
    const spec = resolveOwl(name, registry);
    if (!spec) { ui.printInfo(`Owl "${name}" not found.`); return true; }
    const folderPath = join(gateway.getWorkspacePath(), "owls", spec.name);
    ui.printLines([
      "",
      YB(`${spec.emoji || "🦉"} ${spec.name}`),
      sep(),
      D("Edit the spec file directly to update this owl:"),
      "",
      C("  " + folderPath + "/specialized_owl.md"),
      "",
    ]);
    return true;
  }

  ui.printLines([
    "",
    YB("Specialization Commands"),
    sep(),
    C("/specialization list".padEnd(25)) + D("List all your owls"),
    C("/specialization show <name>".padEnd(25)) + D("Show owl details"),
    C("/specialization create <desc>".padEnd(25)) + D("Create new owl"),
    C("/specialization delete <name>".padEnd(25)) + D("Delete owl (confirm)"),
    C("/specialization update <name>".padEnd(25)) + D("Update owl"),
    "",
  ]);
  return true;
};

const cmdHelp: CommandFn = async (_args, ui) => {
  ui.printLines([
    "",
    YB("Commands"),
    sep(),
    C("/help".padEnd(20)) + D("Show this list"),
    C("/status".padEnd(20)) + D("Provider, model, owl info"),
    C("/owls".padEnd(20)) + D("List owl personas"),
    C("/specialization".padEnd(20)) + D("Manage helpers"),
    C("/clear".padEnd(20)) + D("Clear conversation context"),
    C("/capabilities".padEnd(20)) + D("List synthesized tools"),
    C("/skills".padEnd(20)) + D("List or install skills"),
    C("/learning".padEnd(20)) + D("Show learning report"),
    C("/memory".padEnd(20)) + D("Memory CRUD (list/search/stats/...)"),
    C("/owl".padEnd(20)) + D("Show owl state and memory"),
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

const cmdOwls: CommandFn = async (_args, ui, gateway) => {
  const registry = gateway.getOwlRegistry();
  const owls = registry.listOwls();
  const lines: string[] = ["", YB("Owls"), sep()];
  for (const o of owls) {
    lines.push(
      Y(`${o.persona.emoji} `) +
      W(o.persona.name.padEnd(16)) +
      D(
        `gen ${o.dna.generation}  challenge ${o.dna.evolvedTraits.challengeLevel}`,
      ),
    );
  }
  lines.push("");
  ui.printLines(lines);
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

const cmdOwl: CommandFn = async (_args, ui, gateway) => {
  const db = gateway.getDb();
  const owl = gateway.getOwl();
  if (!db) {
    ui.printInfo("Database not available.");
    return true;
  }
  const { OwlStateReporter } = await import("../intelligence/owl-state-reporter.js");
  const reporter = new OwlStateReporter(db);
  const dna = owl.dna.evolvedTraits as Record<string, unknown>;
  const report = await reporter.report("local", owl.persona.name, dna);
  ui.printLines(["", ...report.split("\n"), ""]);
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
  owls: { description: "List owl personas", fn: cmdOwls },
  specialization: {
    description: "Manage helpers",
    fn: cmdSpecialization,
    subcommands: ["list", "show", "create", "delete", "update"],
  },
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
  owl: { description: "Show owl state", fn: cmdOwl, subcommands: ["status"] },
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
    if (activeWizard) {
      const done = await activeWizard.step(input, ui);
      if (done) {
        activeWizard = null;
        await gateway.reloadSpecializedRegistry();
        ui.setAllowEmptyInput(false);
      }
      return true;
    }

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
    const fn = deps.installer![which as keyof typeof deps.installer]!;
    const ok = await fn();
    return `install ${which}: ${ok ? "ok" : "failed"}`;
  }

  return "usage: stackowl backends list|install|repair|stats";
}
