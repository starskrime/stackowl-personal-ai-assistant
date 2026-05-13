/**
 * StackOwl — /owl Command Dispatcher
 *
 * Unified owl management surface for all channels.
 * Verbs: list, show, status, create, from-bmad, edit, delete, pin, unpin
 */

import { rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../../owls/specialized-types.js";
import { log } from "../../logger.js";

export interface OwlCommandContext {
  registry: SpecializedOwlRegistry;
  userId: string;
  workspacePath: string;
  channelAdapter?: {
    ask(userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }): Promise<string>;
  };
  gateway?: any;
}

export async function dispatchOwlCommand(
  verb: string,
  args: string[],
  ctx: OwlCommandContext,
): Promise<string> {
  log.gateway.debug("dispatchOwlCommand: entry", { verb, args: args.slice(0, 3) });

  switch (verb.toLowerCase()) {
    case "list":
      return cmdList(ctx);
    case "show":
      return cmdShow(args[0] ?? "", ctx);
    case "status":
      return cmdStatus(ctx);
    case "create":
      return cmdCreate(ctx);
    case "from-bmad":
      return cmdFromBmad(args[0] ?? "", ctx);
    case "edit":
      return cmdEdit(args[0] ?? "", ctx);
    case "delete":
    case "remove":
      return cmdDelete(args[0] ?? "", ctx);
    case "pin":
      return cmdPin(args[0] ?? "", ctx);
    case "unpin":
      return cmdUnpin(ctx);
    default:
      return [
        "Unknown /owl command. Usage:",
        "  /owl list               — list all owls",
        "  /owl show <name>        — show owl details",
        "  /owl status             — active owl DNA state",
        "  /owl create             — create a custom owl (interactive)",
        "  /owl from-bmad <name>   — create owl from BMAD template",
        "  /owl edit <name>        — edit a custom owl",
        "  /owl delete <name>      — delete a custom owl",
        "  /owl pin <name>         — pin owl for this session",
        "  /owl unpin              — unpin active owl",
      ].join("\n");
  }
}

async function cmdList(ctx: OwlCommandContext): Promise<string> {
  const specs = ctx.registry.listAll();
  if (specs.length === 0) {
    return "No owls registered. BMAD agents load at startup; custom owls live in workspace/owls/.";
  }
  const grouped: Record<string, SpecializedOwlSpec[]> = { bmad: [], custom: [], builtin: [], other: [] };
  for (const s of specs) {
    const key = (s as any).source ?? "other";
    (grouped[key] ?? grouped.other).push(s);
  }
  const lines: string[] = ["**Owls** — mention with @name\n"];
  const renderGroup = (label: string, items: SpecializedOwlSpec[]) => {
    if (items.length === 0) return;
    lines.push(`**${label}**`);
    for (const s of items) {
      const source = (s as any).source ?? "other";
      lines.push(`  ${s.emoji} **${s.name}** — ${s.role} [${source}]`);
    }
  };
  renderGroup("BMAD Agents", grouped.bmad);
  renderGroup("Custom Owls", grouped.custom);
  renderGroup("Built-in", grouped.builtin);
  renderGroup("Other", grouped.other);
  return lines.join("\n");
}

async function cmdShow(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl show <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found. Use /owl list to see available owls.`;
  return [
    `${spec.emoji} **${spec.name}** (${(spec as any).source ?? "unknown"})`,
    `Role: ${spec.role}`,
    `Expertise: ${spec.expertise.join(", ") || "—"}`,
    `Keywords: ${spec.routingRules.keywords.join(", ") || "—"}`,
    `Challenge: ${spec.personality.challengeLevel}  Verbosity: ${spec.personality.verbosity}`,
    `Model: ${spec.model.provider}/${spec.model.model}`,
    spec.additionalPrompt ? `\nPersona:\n${spec.additionalPrompt.slice(0, 400)}` : "",
  ].filter(Boolean).join("\n");
}

async function cmdStatus(ctx: OwlCommandContext): Promise<string> {
  const gateway = ctx.gateway;
  if (!gateway) return "Gateway not available in this context. Use /owl status from Telegram or CLI.";
  const db = gateway.getDb?.();
  if (!db) return "Database not available.";
  const owl = gateway.getOwl();
  const { OwlStateReporter } = await import("../../intelligence/owl-state-reporter.js");
  const reporter = new OwlStateReporter(db);
  const dna = owl.dna.evolvedTraits as Record<string, unknown>;
  return reporter.report(ctx.userId, owl.persona.name, dna);
}

async function cmdCreate(ctx: OwlCommandContext): Promise<string> {
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    return [
      "Interactive owl creation requires a channel (Telegram or CLI).",
      "Use: /owl create — then follow the prompts.",
    ].join("\n");
  }
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — owl-wizard.ts is created in Plan 3 Task 2
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  return runOwlCreationWizard("from-scratch", {}, ctx.workspacePath, ctx.userId, adapter);
}

async function cmdFromBmad(agentName: string, ctx: OwlCommandContext): Promise<string> {
  if (!agentName) {
    const specs = ctx.registry.listAll().filter((s) => (s as any).source === "bmad");
    if (specs.length === 0) return "No BMAD agents loaded. Restart to reload.";
    const names = specs.map((s) => `${s.emoji} ${s.name} (${(s as any).bmadSkillName})`).join("\n  ");
    return `Available BMAD templates:\n  ${names}\n\nUsage: /owl from-bmad <name>`;
  }
  const spec = ctx.registry.get(agentName);
  if (!spec || (spec as any).source !== "bmad") {
    return `BMAD agent "${agentName}" not found. Use /owl from-bmad (no args) to list available templates.`;
  }
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    return "Interactive wizard requires a channel (Telegram or CLI).";
  }
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — owl-wizard.ts is created in Plan 3 Task 2
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  return runOwlCreationWizard("from-bmad", { template: spec }, ctx.workspacePath, ctx.userId, adapter);
}

async function cmdEdit(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl edit <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found.`;
  if ((spec as any).source === "bmad") {
    return [
      `${spec.emoji} **${spec.name}** is a BMAD agent and cannot be edited directly.`,
      `To customize it, create a new owl from its template: /owl from-bmad ${spec.name}`,
    ].join("\n");
  }
  return [
    `Editing ${spec.emoji} **${spec.name}**`,
    `Spec file: ${spec.folderPath ?? "unknown"}/specialized_owl.md`,
    "",
    "Edit the spec file directly, then restart to pick up changes.",
  ].join("\n");
}

async function cmdDelete(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl delete <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found.`;
  if ((spec as any).source === "bmad") {
    return `Cannot delete BMAD agent "${spec.name}". BMAD agents are managed by the bmad-method package. Use /owl from-bmad to create a custom copy.`;
  }
  if (!spec.folderPath || !existsSync(spec.folderPath)) {
    return `Owl "${name}" has no folder on disk — nothing to delete.`;
  }
  try {
    await rm(spec.folderPath, { recursive: true, force: true });
    log.gateway.info("dispatchOwlCommand: deleted owl folder", { name, folder: spec.folderPath });
    return `🗑️ Deleted ${spec.emoji} **${spec.name}**. Restart to fully clear from registry.`;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log.gateway.error("dispatchOwlCommand: delete failed", err, { name, folder: spec.folderPath });
    return `Failed to delete "${name}": ${msg}`;
  }
}

async function cmdPin(name: string, ctx: OwlCommandContext): Promise<string> {
  if (!name) return "Usage: /owl pin <name>";
  const spec = ctx.registry.get(name);
  if (!spec) return `Owl "${name}" not found. Use /owl list to see available owls.`;
  const gw = ctx.gateway as any;
  if (typeof gw?.pinOwl === "function") {
    await gw.pinOwl(ctx.userId, spec.name);
  }
  return `📌 Pinned ${spec.emoji} **${spec.name}** for your session. Messages will route to ${spec.name} until unpinned.`;
}

async function cmdUnpin(ctx: OwlCommandContext): Promise<string> {
  const gw = ctx.gateway as any;
  if (typeof gw?.unpinOwl === "function") {
    await gw.unpinOwl(ctx.userId);
  }
  return "📌 Owl unpinned. Noctua (the secretary) will handle routing again.";
}
