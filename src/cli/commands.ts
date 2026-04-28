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
import { SpecializationCreateWizard } from "./specialization-wizard.js";
import type { MemoryDatabase, SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";

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

// ─── Shared Lookup ────────────────────────────────────────────────

export type ResolvedOwl =
  | { source: "db"; owl: SpecializedOwl }
  | { source: "folder"; spec: SpecializedOwlSpec };

export function resolveOwl(
  inputName: string,
  ownerId: string,
  db: MemoryDatabase,
  registry: SpecializedOwlRegistry | undefined,
): ResolvedOwl | null {
  const lower = inputName.toLowerCase();
  const dbOwl = db.owls.getByOwner(ownerId).find((o) => o.name.toLowerCase() === lower);
  if (dbOwl) return { source: "db", owl: dbOwl };
  const spec = registry?.get(inputName);
  if (spec) return { source: "folder", spec };
  return null;
}

// ─── Wizard State ─────────────────────────────────────────────────

let activeWizard: SpecializationCreateWizard | null = null;

// ─── Commands ─────────────────────────────────────────────────────

const cmdSpecialization: CommandFn = async (args, ui, gateway) => {
  const db = gateway.getDb();
  if (!db) {
    ui.printInfo("Database not available.");
    return true;
  }

  const ownerId = "local";
  const parts = args.trim().toLowerCase().split(/\s+/);
  const subcmd = parts[0] || "list";

  if (subcmd === "list") {
    const dbOwls = db.owls.getByOwner(ownerId);
    const registry = gateway.getSpecializedRegistry();
    const folderOwls = registry ? registry.listAll() : [];

    // De-duplicate: skip folder owls whose name also appears in DB owls
    const dbNames = new Set(dbOwls.map((o) => o.name.toLowerCase()));
    const uniqueFolderOwls = folderOwls.filter(
      (s) => !dbNames.has(s.name.toLowerCase()),
    );

    const total = dbOwls.length + uniqueFolderOwls.length;
    if (total === 0) {
      ui.printLines([
        "",
        YB("Specialized Owls"),
        sep(),
        D("No specialized owls yet. Create one with /specialization create"),
        "",
      ]);
      return true;
    }

    const lines: string[] = ["", YB("Specialized Owls"), sep()];
    for (const owl of dbOwls) {
      const mainTag = owl.isMainOwl ? Y(" [Main]") : "";
      lines.push(
        Y("🦉 ") +
        W(owl.name.padEnd(16)) +
        D(owl.specialization) +
        mainTag,
      );
    }
    for (const spec of uniqueFolderOwls) {
      lines.push(
        Y(`${spec.emoji || "🦉"} `) +
        W(spec.name.padEnd(16)) +
        D(spec.role) +
        C(" [folder]"),
      );
    }
    lines.push(D(`\n${total} owl(s) total`));
    lines.push("");
    ui.printLines(lines);
    return true;
  }

  if (subcmd === "show") {
    const name = parts.slice(1).join(" ");
    if (!name) {
      ui.printInfo("Usage: /specialization show <name>");
      return true;
    }
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }

    if (result.source === "db") {
      const owl = result.owl;
      const dna = owl.dna;
      ui.printLines([
        "",
        YB(`Owl: ${owl.name}`),
        sep(),
        D("Specialization  ") + W(owl.specialization),
        D("Main Owl       ") + W(owl.isMainOwl ? "Yes" : "No"),
        D("Created        ") + W(owl.createdAt.slice(0, 10)),
        "",
        YB("DNA / Evolution"),
        sep(),
        D("Challenge Level ") + W(String(dna.challengeLevel)),
        D("Verbosity       ") + W(String(dna.verbosity)),
        D("Expertise       ") + W(dna.expertiseDomains.join(", ") || "(none)"),
        D("Routing Quality ") + W(String(dna.routingQuality)),
        D("Evolution Speed ") + W(String(dna.evolutionSpeed)),
        "",
        YB("Personality Prompt"),
        sep(),
        ...owl.personalityPrompt.split("\n").map((l: string) => D("  " + l)),
        "",
        YB("Routing Rules"),
        sep(),
        ...(owl.routingRules.length > 0
          ? owl.routingRules.map((r: string) => D("  • " + r))
          : [D("  (none)")]),
        "",
      ]);
    } else {
      const spec = result.spec;
      const folderPath = join(gateway.getWorkspacePath(), "owls", spec.name);
      ui.printLines([
        "",
        YB(`${spec.emoji || "🦉"} ${spec.name}`) + C(" [folder]"),
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
    }
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
    const nameParts = confirmed ? parts.slice(1, -1) : parts.slice(1);
    const name = nameParts.join(" ");

    if (!name) {
      ui.printInfo("Usage: /specialization delete <name>");
      return true;
    }

    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }

    const displayName = result.source === "db" ? result.owl.name : result.spec.name;

    if (confirmed) {
      if (result.source === "db") {
        db.owls.delete(result.owl.id);
      } else {
        const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
        await rm(folderPath, { recursive: true, force: true });
        await gateway.reloadSpecializedRegistry();
      }
      ui.printLines(["", G(`✓ Deleted owl: ${displayName}`), ""]);
      return true;
    }

    ui.printLines([
      "",
      R(`⚠️  Delete "${displayName}"?`),
      sep(),
      D("This action cannot be undone."),
      D(""),
      D("Confirm: /specialization delete " + displayName + " yes"),
      "",
    ]);
    return true;
  }

  if (subcmd === "update" && parts.length > 2) {
    const name = parts[1];
    const newSpecialization = parts.slice(2).join(" ");
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }
    if (result.source === "folder") {
      const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
      ui.printLines([
        "",
        YB(`${result.spec.emoji || "🦉"} ${result.spec.name}`) + C(" [folder]"),
        sep(),
        D("Folder owls are configured via their spec file."),
        D("Edit it directly to update role, expertise, and routing rules:"),
        "",
        C("  " + folderPath + "/specialized_owl.md"),
        "",
      ]);
      return true;
    }
    if (!newSpecialization || newSpecialization.length < 5) {
      ui.printInfo("Please provide a new specialization (at least 5 characters).");
      return true;
    }
    db.owls.update(result.owl.id, { specialization: newSpecialization });
    ui.printLines([
      "",
      G(`✓ Updated owl: ${result.owl.name}`),
      sep(),
      D("New specialization: ") + W(newSpecialization),
      "",
    ]);
    return true;
  }

  if (subcmd === "update") {
    const name = parts.slice(1).join(" ");
    if (!name) {
      ui.printInfo("Usage: /specialization update <name>");
      return true;
    }
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }
    if (result.source === "folder") {
      const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
      ui.printLines([
        "",
        YB(`${result.spec.emoji || "🦉"} ${result.spec.name}`) + C(" [folder]"),
        sep(),
        D("Folder owls are configured via their spec file."),
        D("Edit it directly to update role, expertise, and routing rules:"),
        "",
        C("  " + folderPath + "/specialized_owl.md"),
        "",
      ]);
      return true;
    }
    const owl = result.owl;
    ui.printLines([
      "",
      YB(`Update Owl: ${owl.name}`),
      sep(),
      D("Specialization: ") + W(owl.specialization),
      D(""),
      D("To update specialization:"),
      D("  /specialization update " + owl.name + " <new specialization>"),
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
    C("/specialization".padEnd(20)) + D("Manage specialized owls"),
    C("/clear".padEnd(20)) + D("Clear conversation context"),
    C("/capabilities".padEnd(20)) + D("List synthesized tools"),
    C("/skills".padEnd(20)) + D("List or install skills"),
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
  specialization: { description: "Manage specialized owls", fn: cmdSpecialization },
  clear: { description: "Clear context", fn: cmdClear },
  reset: { description: "Clear context", fn: cmdClear },
  capabilities: { description: "List synthesized tools", fn: cmdCapabilities },
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
    // Route to active wizard FIRST - works for any input including empty Enter presses
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
