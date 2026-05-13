/**
 * StackOwl — /owl CLI v2 Handlers
 *
 * Wires the dispatchOwlCommand dispatcher into TUI v2 CommandHandlers.
 * Verbs: list, show, create, from-bmad, delete, pin, unpin
 */

import type { CommandHandler } from "../registry.js";
import { dispatchOwlCommand } from "../../../../gateway/commands/owl-command.js";
import type { OwlCommandContext, GatewayMethods } from "../../../../gateway/commands/owl-command.js";
import { log } from "../../../../logger.js";

// ─── Context builder ──────────────────────────────────────────────────────────

function makeOwlCtx(ctx: Parameters<CommandHandler>[0]): OwlCommandContext | null {
  log.cli.debug("owl.makeOwlCtx: entry");
  const gateway = ctx.getOwlGateway();
  const registry = gateway.getSpecializedRegistry();
  if (!registry) {
    log.cli.warn("owl.makeOwlCtx: specialized registry not available");
    return null;
  }
  // OwlGateway.getDb() returns MemoryDatabase | undefined; GatewayMethods.getDb()
  // types as Database | null (better-sqlite3). Cast via unknown — the dispatcher
  // only passes it to OwlStateReporter which accepts the same underlying rawDb.
  const gatewayMethods: GatewayMethods = {
    getDb: () => (gateway.getDb() ?? null) as unknown as import("better-sqlite3").Database | null,
    getOwl: () => gateway.getOwl() ?? null,
  };
  const owlCtx: OwlCommandContext = {
    registry,
    userId: "local",
    workspacePath: gateway.getWorkspacePath(),
    gateway: gatewayMethods,
  };
  log.cli.debug("owl.makeOwlCtx: exit", { workspacePath: owlCtx.workspacePath });
  return owlCtx;
}

// ─── Text → PanelItems helper ─────────────────────────────────────────────────

function textToItems(text: string) {
  return text
    .split("\n")
    .filter((l) => l.trim())
    .map((line, i) => ({ id: `owl-${i}`, label: line }));
}

// ─── CLI channel adapter (readline-based) ────────────────────────────────────

async function buildCliAdapter(): Promise<NonNullable<OwlCommandContext["channelAdapter"]>> {
  log.cli.debug("owl.buildCliAdapter: creating readline adapter");
  return {
    ask: async (
      _userId: string,
      prompt: { text: string; choices?: string[]; defaultChoice?: string },
    ): Promise<string> => {
      log.cli.debug("owl.buildCliAdapter.ask: entry", { promptText: prompt.text.slice(0, 80) });
      const readline = await import("node:readline");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      const choices = prompt.choices
        ? `\n${prompt.choices.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}`
        : "";
      const defaultHint = prompt.defaultChoice ? ` [${prompt.defaultChoice}]` : "";
      return new Promise<string>((resolve) => {
        rl.question(`${prompt.text}${choices}${defaultHint}\n> `, (ans) => {
          rl.close();
          log.cli.debug("owl.buildCliAdapter.ask: received answer", { len: ans.length });
          if (!ans && prompt.defaultChoice) return resolve(prompt.defaultChoice);
          if (prompt.choices) {
            const idx = parseInt(ans, 10) - 1;
            return resolve(!isNaN(idx) && prompt.choices[idx] !== undefined ? prompt.choices[idx] : ans);
          }
          resolve(ans);
        });
      });
    },
  };
}

// ─── /owl list ────────────────────────────────────────────────────────────────

export const handleOwlList: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlList: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlList: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const text = await dispatchOwlCommand("list", [], owlCtx);
  const items = textToItems(text);
  log.cli.debug("handleOwlList: exit", { items: items.length });
  return { kind: "panel", payload: { title: "/owl list", items, emptyText: "No owls registered." } };
};

// ─── /owl show <name> ─────────────────────────────────────────────────────────

export const handleOwlShow: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlShow: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlShow: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const text = await dispatchOwlCommand("show", args, owlCtx);
  const items = textToItems(text);
  log.cli.debug("handleOwlShow: exit", { items: items.length });
  return {
    kind: "panel",
    payload: { title: `/owl show ${args[0] ?? ""}`, items, emptyText: "No details available." },
  };
};

// ─── /owl delete <name> ───────────────────────────────────────────────────────

export const handleOwlDelete: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlDelete: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlDelete: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const text = await dispatchOwlCommand("delete", args, owlCtx);
  log.cli.debug("handleOwlDelete: exit", { textLen: text.length });
  return { kind: "system-message", text };
};

// ─── /owl from-bmad [<name>] ──────────────────────────────────────────────────

export const handleOwlFromBmad: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlFromBmad: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlFromBmad: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const adapter = await buildCliAdapter();
  const text = await dispatchOwlCommand("from-bmad", args, { ...owlCtx, channelAdapter: adapter });
  log.cli.debug("handleOwlFromBmad: exit", { textLen: text.length });
  return { kind: "system-message", text };
};

// ─── /owl create ─────────────────────────────────────────────────────────────

export const handleOwlCreate: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlCreate: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlCreate: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const adapter = await buildCliAdapter();
  const text = await dispatchOwlCommand("create", [], { ...owlCtx, channelAdapter: adapter });
  log.cli.debug("handleOwlCreate: exit", { textLen: text.length });
  return { kind: "system-message", text };
};

// ─── /owl pin <name> ──────────────────────────────────────────────────────────

export const handleOwlPin: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlPin: entry", { args });
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlPin: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const text = await dispatchOwlCommand("pin", args, owlCtx);
  log.cli.debug("handleOwlPin: exit", { textLen: text.length });
  return { kind: "system-message", text };
};

// ─── /owl unpin ───────────────────────────────────────────────────────────────

export const handleOwlUnpin: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlUnpin: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlUnpin: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  const text = await dispatchOwlCommand("unpin", [], owlCtx);
  log.cli.debug("handleOwlUnpin: exit", { textLen: text.length });
  return { kind: "system-message", text };
};
