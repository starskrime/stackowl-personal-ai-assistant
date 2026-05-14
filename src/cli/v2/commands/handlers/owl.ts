/**
 * StackOwl — /owl CLI v2 Handlers
 *
 * Wires the dispatchOwlCommand dispatcher into TUI v2 CommandHandlers.
 * Verbs: list, show, create, from-bmad, delete, pin, unpin
 */

import type { CommandHandler } from "../registry.js";
import type { PanelItem } from "../../panels/Panel.js";
import { dispatchOwlCommand } from "../../../../gateway/commands/owl-command.js";
import type { OwlCommandContext, GatewayMethods } from "../../../../gateway/commands/owl-command.js";
import type { UiBridge } from "../../events/bridge.js";
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

// ─── Bridge-based channel adapter ────────────────────────────────────────────
// Uses bridge.prompt() so the TUI Composer captures the answer natively.
// This prevents stdin conflicts with Ink's raw-mode input handler.

function buildBridgeAdapter(bridge: UiBridge): NonNullable<OwlCommandContext["channelAdapter"]> {
  log.cli.debug("owl.buildBridgeAdapter: creating bridge adapter");
  return {
    ask: async (
      _userId: string,
      prompt: { text: string; choices?: string[]; defaultChoice?: string },
    ): Promise<string> => {
      log.cli.debug("owl.buildBridgeAdapter.ask: entry", { promptText: prompt.text.slice(0, 80) });
      const answer = await bridge.prompt(prompt.text, {
        choices: prompt.choices,
        defaultChoice: prompt.defaultChoice,
      });
      log.cli.debug("owl.buildBridgeAdapter.ask: received answer", { len: answer.length });
      return answer;
    },
  };
}

// ─── /owl list (and bare /owl) ────────────────────────────────────────────────
// Returns structured panel items so Return switches the active owl.

export const handleOwlList: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlList: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlList: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const specs = owlCtx.registry.listAll();
  const items: PanelItem[] = specs.map((spec) => ({
    id: spec.name,
    label: `${spec.emoji} ${spec.name}`,
    meta: `${spec.role}${spec.source ? ` [${spec.source}]` : ""}`,
    data: { name: spec.name, emoji: spec.emoji },
  }));
  const actions = [
    {
      key: "return",
      label: "switch",
      handler: (item: PanelItem) => {
        const owlData = item.data as { name: string; emoji: string } | undefined;
        if (owlData) ctx.bridge.changeOwl(owlData.name, owlData.emoji);
        else ctx.bridge.changeOwl(item.id, "🦉");
        ctx.bridge.closePanel();
      },
    },
  ];
  log.cli.debug("handleOwlList: exit", { items: items.length });
  return { kind: "panel", payload: { title: "/owl", items, actions, emptyText: "No owls registered." } };
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
  const adapter = buildBridgeAdapter(ctx.bridge);
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
  const adapter = buildBridgeAdapter(ctx.bridge);
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
