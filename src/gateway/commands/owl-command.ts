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
  log.gateway.debug("dispatchOwlCommand.cmdList: entry", { userId: ctx.userId });
  const specs = ctx.registry.listAll();
  if (specs.length === 0) {
    log.gateway.debug("dispatchOwlCommand.cmdList: exit", { result: "no owls" });
    return "No owls registered. BMAD agents load at startup; custom owls live in workspace/owls/.";
  }
  log.gateway.debug("dispatchOwlCommand.cmdList: grouping owls", { count: specs.length });
  const grouped: Record<string, SpecializedOwlSpec[]> = { bmad: [], custom: [], builtin: [], other: [] };
  for (const s of specs) {
    const key = s.source ?? "other";
    (grouped[key] ?? grouped.other).push(s);
  }
  const lines: string[] = ["**Owls** — mention with @name\n"];
  const renderGroup = (label: string, items: SpecializedOwlSpec[]) => {
    if (items.length === 0) return;
    lines.push(`**${label}**`);
    for (const s of items) {
      const source = s.source ?? "other";
      lines.push(`  ${s.emoji} **${s.name}** — ${s.role} [${source}]`);
    }
  };
  renderGroup("BMAD Agents", grouped.bmad);
  renderGroup("Custom Owls", grouped.custom);
  renderGroup("Built-in", grouped.builtin);
  renderGroup("Other", grouped.other);
  const result = lines.join("\n");
  log.gateway.debug("dispatchOwlCommand.cmdList: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdShow(name: string, ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdShow: entry", { name, userId: ctx.userId });
  if (!name) return "Usage: /owl show <name>";
  const spec = ctx.registry.get(name);
  if (!spec) {
    log.gateway.debug("dispatchOwlCommand.cmdShow: exit", { result: "not found" });
    return `Owl "${name}" not found. Use /owl list to see available owls.`;
  }
  log.gateway.debug("dispatchOwlCommand.cmdShow: found spec", { name: spec.name, source: spec.source });
  const result = [
    `${spec.emoji} **${spec.name}** (${spec.source ?? "unknown"})`,
    `Role: ${spec.role}`,
    `Expertise: ${spec.expertise.join(", ") || "—"}`,
    `Keywords: ${spec.routingRules.keywords.join(", ") || "—"}`,
    `Challenge: ${spec.personality.challengeLevel}  Verbosity: ${spec.personality.verbosity}`,
    `Model: ${spec.model.provider}/${spec.model.model}`,
    spec.additionalPrompt ? `\nPersona:\n${spec.additionalPrompt.slice(0, 400)}` : "",
  ].filter(Boolean).join("\n");
  log.gateway.debug("dispatchOwlCommand.cmdShow: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdStatus(ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdStatus: entry", { userId: ctx.userId });
  const gateway = ctx.gateway;
  if (!gateway) {
    log.gateway.debug("dispatchOwlCommand.cmdStatus: exit", { result: "no gateway" });
    return "Gateway not available in this context. Use /owl status from Telegram or CLI.";
  }
  const db = gateway.getDb?.();
  if (!db) {
    log.gateway.debug("dispatchOwlCommand.cmdStatus: exit", { result: "no db" });
    return "Database not available.";
  }
  log.gateway.debug("dispatchOwlCommand.cmdStatus: fetching owl state", {});
  const owl = gateway.getOwl();
  const { OwlStateReporter } = await import("../../intelligence/owl-state-reporter.js");
  const reporter = new OwlStateReporter(db);
  const dna = owl.dna.evolvedTraits as Record<string, unknown>;
  const result = reporter.report(ctx.userId, owl.persona.name, dna);
  log.gateway.debug("dispatchOwlCommand.cmdStatus: exit", { result: String(result).slice(0, 50) });
  return result;
}

async function cmdCreate(ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdCreate: entry", { userId: ctx.userId });
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    log.gateway.debug("dispatchOwlCommand.cmdCreate: exit", { result: "no adapter" });
    return [
      "Interactive owl creation requires a channel (Telegram or CLI).",
      "Use: /owl create — then follow the prompts.",
    ].join("\n");
  }
  log.gateway.debug("dispatchOwlCommand.cmdCreate: launching wizard", {});
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — owl-wizard.ts is created in Plan 3 Task 2
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  const result = await runOwlCreationWizard("from-scratch", {}, ctx.workspacePath, ctx.userId, adapter);
  log.gateway.debug("dispatchOwlCommand.cmdCreate: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdFromBmad(agentName: string, ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdFromBmad: entry", { agentName, userId: ctx.userId });
  if (!agentName) {
    log.gateway.debug("dispatchOwlCommand.cmdFromBmad: listing bmad templates", {});
    const specs = ctx.registry.listAll().filter((s) => s.source === "bmad");
    if (specs.length === 0) {
      log.gateway.debug("dispatchOwlCommand.cmdFromBmad: exit", { result: "no bmad agents" });
      return "No BMAD agents loaded. Restart to reload.";
    }
    const names = specs.map((s) => `${s.emoji} ${s.name} (${s.bmadSkillName})`).join("\n  ");
    const result = `Available BMAD templates:\n  ${names}\n\nUsage: /owl from-bmad <name>`;
    log.gateway.debug("dispatchOwlCommand.cmdFromBmad: exit", { result: result.slice(0, 50) });
    return result;
  }
  const spec = ctx.registry.get(agentName);
  if (!spec || spec.source !== "bmad") {
    log.gateway.debug("dispatchOwlCommand.cmdFromBmad: exit", { result: "not found or not bmad" });
    return `BMAD agent "${agentName}" not found. Use /owl from-bmad (no args) to list available templates.`;
  }
  const adapter = ctx.channelAdapter;
  if (!adapter) {
    log.gateway.debug("dispatchOwlCommand.cmdFromBmad: exit", { result: "no adapter" });
    return "Interactive wizard requires a channel (Telegram or CLI).";
  }
  log.gateway.debug("dispatchOwlCommand.cmdFromBmad: launching wizard", { agentName });
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — owl-wizard.ts is created in Plan 3 Task 2
  const { runOwlCreationWizard } = await import("../wizards/owl-wizard.js");
  const result = await runOwlCreationWizard("from-bmad", { template: spec }, ctx.workspacePath, ctx.userId, adapter);
  log.gateway.debug("dispatchOwlCommand.cmdFromBmad: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdEdit(name: string, ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdEdit: entry", { name, userId: ctx.userId });
  if (!name) return "Usage: /owl edit <name>";
  const spec = ctx.registry.get(name);
  if (!spec) {
    log.gateway.debug("dispatchOwlCommand.cmdEdit: exit", { result: "not found" });
    return `Owl "${name}" not found.`;
  }
  if (spec.source === "bmad") {
    log.gateway.debug("dispatchOwlCommand.cmdEdit: exit", { result: "bmad agent — edit blocked" });
    return [
      `${spec.emoji} **${spec.name}** is a BMAD agent and cannot be edited directly.`,
      `To customize it, create a new owl from its template: /owl from-bmad ${spec.name}`,
    ].join("\n");
  }
  const result = [
    `Editing ${spec.emoji} **${spec.name}**`,
    `Spec file: ${spec.folderPath ?? "unknown"}/specialized_owl.md`,
    "",
    "Edit the spec file directly, then restart to pick up changes.",
  ].join("\n");
  log.gateway.debug("dispatchOwlCommand.cmdEdit: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdDelete(name: string, ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdDelete: entry", { name, userId: ctx.userId });
  if (!name) return "Usage: /owl delete <name>";
  const spec = ctx.registry.get(name);
  if (!spec) {
    log.gateway.debug("dispatchOwlCommand.cmdDelete: exit", { result: "not found" });
    return `Owl "${name}" not found.`;
  }
  if (spec.source === "bmad") {
    log.gateway.debug("dispatchOwlCommand.cmdDelete: exit", { result: "bmad agent — delete blocked" });
    return `Cannot delete BMAD agent "${spec.name}". BMAD agents are managed by the bmad-method package. Use /owl from-bmad to create a custom copy.`;
  }
  if (!spec.folderPath || !existsSync(spec.folderPath)) {
    log.gateway.debug("dispatchOwlCommand.cmdDelete: exit", { result: "no folder on disk" });
    return `Owl "${name}" has no folder on disk — nothing to delete.`;
  }
  log.gateway.debug("dispatchOwlCommand.cmdDelete: removing folder", { folder: spec.folderPath });
  try {
    await rm(spec.folderPath, { recursive: true, force: true });
    log.gateway.info("dispatchOwlCommand.cmdDelete: deleted owl folder", { name, folder: spec.folderPath });
    const result = `🗑️ Deleted ${spec.emoji} **${spec.name}**. Restart to fully clear from registry.`;
    log.gateway.debug("dispatchOwlCommand.cmdDelete: exit", { result: result.slice(0, 50) });
    return result;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log.gateway.error("dispatchOwlCommand.cmdDelete: delete failed", err, { name, folder: spec.folderPath });
    return `Failed to delete "${name}": ${msg}`;
  }
}

async function cmdPin(name: string, ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdPin: entry", { name, userId: ctx.userId });
  if (!name) return "Usage: /owl pin <name>";
  const spec = ctx.registry.get(name);
  if (!spec) {
    log.gateway.debug("dispatchOwlCommand.cmdPin: exit", { result: "not found" });
    return `Owl "${name}" not found. Use /owl list to see available owls.`;
  }
  const gw = ctx.gateway as any;
  if (typeof gw?.pinOwl === "function") {
    log.gateway.debug("dispatchOwlCommand.cmdPin: calling gw.pinOwl", { userId: ctx.userId, owlName: spec.name });
    try {
      await gw.pinOwl(ctx.userId, spec.name);
    } catch (err) {
      log.gateway.error("dispatchOwlCommand.cmdPin: pinOwl failed", err, { userId: ctx.userId, owlName: spec.name });
    }
  }
  const result = `📌 Pinned ${spec.emoji} **${spec.name}** for your session. Messages will route to ${spec.name} until unpinned.`;
  log.gateway.debug("dispatchOwlCommand.cmdPin: exit", { result: result.slice(0, 50) });
  return result;
}

async function cmdUnpin(ctx: OwlCommandContext): Promise<string> {
  log.gateway.debug("dispatchOwlCommand.cmdUnpin: entry", { userId: ctx.userId });
  const gw = ctx.gateway as any;
  if (typeof gw?.unpinOwl === "function") {
    log.gateway.debug("dispatchOwlCommand.cmdUnpin: calling gw.unpinOwl", { userId: ctx.userId });
    try {
      await gw.unpinOwl(ctx.userId);
    } catch (err) {
      log.gateway.error("dispatchOwlCommand.cmdUnpin: unpinOwl failed", err, { userId: ctx.userId });
    }
  }
  const result = "📌 Owl unpinned. Noctua (the secretary) will handle routing again.";
  log.gateway.debug("dispatchOwlCommand.cmdUnpin: exit", { result: result.slice(0, 50) });
  return result;
}
