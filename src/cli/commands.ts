/**
 * StackOwl — CLI Command Registry
 *
 * All output goes through ui.printLines() / ui.printInfo() / ui.printError()
 * so it renders inside the split-panel window, never to raw stdout.
 */

import chalk from "chalk";
import type { OwlGateway } from "../gateway/core.js";
import type { TerminalRenderer } from "./renderer.js";

// ─── Types ────────────────────────────────────────────────────────

type CommandFn = (
  args: string,
  ui: TerminalRenderer,
  gateway: OwlGateway,
) => Promise<boolean>;

interface CommandDef {
  description: string;
  fn: CommandFn;
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

// ─── Commands ─────────────────────────────────────────────────────

const cmdHelp: CommandFn = async (_args, ui) => {
  ui.printLines([
    "",
    YB("Commands"),
    sep(),
    C("/help".padEnd(20)) + D("Show this list"),
    C("/status".padEnd(20)) + D("Provider, model, owl info"),
    C("/owls".padEnd(20)) + D("List owl personas"),
    C("/clear".padEnd(20)) + D("Clear conversation context"),
    C("/capabilities".padEnd(20)) + D("List synthesized tools"),
    C("/skills".padEnd(20)) + D("List loaded skills"),
    C("/skill <name>".padEnd(20)) + D("Run a specific skill"),
    C("/learning".padEnd(20)) + D("Show learning report"),
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

const cmdSkills: CommandFn = async (_args, ui, gateway) => {
  const loader = gateway.getSkillsLoader?.();
  if (!loader) {
    ui.printInfo("Skills not loaded.");
    return true;
  }

  const skills = loader.getRegistry().listEnabled();
  if (skills.length === 0) {
    ui.printInfo("No skills loaded.");
    return true;
  }

  const lines: string[] = ["", YB("Skills"), sep()];
  for (const s of skills) {
    const emoji = s.metadata.openclaw?.emoji ?? "◈";
    const always = s.metadata.openclaw?.always ? C(" [always]") : "";
    lines.push(Y(emoji) + " " + W(s.name) + always);
    lines.push(D(`   ${s.description}`));
    lines.push("");
  }
  ui.printLines(lines);
  return true;
};

const cmdSkill: CommandFn = async (args, ui, gateway) => {
  const name = args.trim();
  if (!name) {
    ui.printInfo("Usage: /skill <name>");
    return true;
  }

  const loader = gateway.getSkillsLoader?.();
  if (!loader) {
    ui.printInfo("Skills not loaded.");
    return true;
  }

  const skill = loader.getRegistry().get(name);
  if (!skill) {
    ui.printError(`Skill "${name}" not found.`);
    return true;
  }

  // Forward as a regular chat message with the skill's name
  return false;
};

const cmdLearning: CommandFn = async (_args, ui, gateway) => {
  const learning = gateway.getLearningEngine();
  if (!learning) {
    ui.printInfo("Learning engine not available.");
    return true;
  }

  const report = await learning.getLearningReport();
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

// ─── Registry ────────────────────────────────────────────────────

const COMMANDS: Record<string, CommandDef> = {
  help: { description: "Show command list", fn: cmdHelp },
  "?": { description: "Show command list", fn: cmdHelp },
  status: { description: "Provider / model / owl info", fn: cmdStatus },
  owls: { description: "List owl personas", fn: cmdOwls },
  clear: { description: "Clear context", fn: cmdClear },
  reset: { description: "Clear context", fn: cmdClear },
  capabilities: { description: "List synthesized tools", fn: cmdCapabilities },
  skills: { description: "List loaded skills", fn: cmdSkills },
  skill: { description: "Run a specific skill", fn: cmdSkill },
  learning: { description: "Learning report", fn: cmdLearning },
  quit: { description: "Save and exit", fn: cmdQuit },
  exit: { description: "Save and exit", fn: cmdQuit },
  bye: { description: "Save and exit", fn: cmdQuit },
  onboarding: { description: "Re-run setup wizard", fn: cmdOnboarding },
};

export class CommandRegistry {
  listNames(): string[] {
    return Object.keys(COMMANDS);
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
