/**
 * StackOwl — /owl CLI v2 Handlers
 *
 * Wires the dispatchOwlCommand dispatcher into TUI v2 CommandHandlers.
 * Verbs: list, switch, show, create, from-bmad, delete, pin, unpin
 */

import type { CommandHandler } from "../registry.js";
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
      log.cli.debug("owl.bridgeAdapter.ask: entry", { promptText: prompt.text.slice(0, 60) });
      try {
        const answer = await bridge.prompt(prompt.text, {
          choices: prompt.choices,
          defaultChoice: prompt.defaultChoice,
        });
        log.cli.debug("owl.bridgeAdapter.ask: received answer", { len: answer.length });
        return answer;
      } catch (err) {
        log.cli.error("owl.bridgeAdapter.ask: bridge.prompt threw", err as Error);
        throw err;
      }
    },
  };
}

// ─── /owl list (and bare /owl) ────────────────────────────────────────────────
// Returns an inline system-message — no panel focus theft, no Esc required.

export const handleOwlList: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlList: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlList: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const specs  = owlCtx.registry.listAll();
  const active = ctx.getStore().activeOwlName.toLowerCase();
  log.cli.debug("handleOwlList: exit", { count: specs.length });

  if (specs.length === 0) {
    return { kind: "system-message", text: "No owls registered. Use /owl create to add one." };
  }

  // padEnd(14) aligns columns for typical owl names; longer names will shift the role column right.
  const lines = specs.map((s) => {
    const role   = s.role ?? "";
    const marker = s.name.toLowerCase() === active ? "  ← active" : "";
    const source = s.source ? `  [${s.source}]` : "";
    return `  ${s.emoji} ${s.name.padEnd(14)} ${role}${source}${marker}`;
  });
  const text =
    `🦉 Owls (${specs.length})\n\n` +
    lines.join("\n") +
    `\n\nSwitch with: /owl switch <name>`;

  return { kind: "system-message", text };
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
  log.cli.debug("handleOwlShow: exit", { textLen: text.length });
  return { kind: "system-message", text };
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
    log.cli.error("handleOwlFromBmad: no registry", new Error("registry null"));
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  log.cli.debug("handleOwlFromBmad: calling dispatchOwlCommand from-bmad");
  const adapter = buildBridgeAdapter(ctx.bridge);
  try {
    const text = await dispatchOwlCommand("from-bmad", args, { ...owlCtx, channelAdapter: adapter });
    log.cli.debug("handleOwlFromBmad: exit", { textLen: text.length });
    return { kind: "system-message", text };
  } catch (err) {
    log.cli.error("handleOwlFromBmad: dispatchOwlCommand threw", err as Error);
    return { kind: "error", text: `Owl creation from BMAD failed: ${(err as Error).message}` };
  }
};

// ─── /owl create ─────────────────────────────────────────────────────────────

export const handleOwlCreate: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleOwlCreate: entry");
  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.error("handleOwlCreate: no registry — cannot create owl", new Error("registry null"));
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }
  log.cli.debug("handleOwlCreate: calling dispatchOwlCommand create");
  const adapter = buildBridgeAdapter(ctx.bridge);
  try {
    const text = await dispatchOwlCommand("create", [], { ...owlCtx, channelAdapter: adapter });
    log.cli.debug("handleOwlCreate: exit", { textLen: text.length });
    return { kind: "system-message", text };
  } catch (err) {
    log.cli.error("handleOwlCreate: dispatchOwlCommand threw", err as Error);
    return { kind: "error", text: `Owl creation failed: ${(err as Error).message}` };
  }
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

// ─── /owl switch <name> ───────────────────────────────────────────────────────

export const handleOwlSwitch: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleOwlSwitch: entry", { args });
  const name = args[0];
  if (!name) {
    log.cli.warn("handleOwlSwitch: no name provided");
    return { kind: "error", text: "Usage: /owl switch <name>" };
  }

  const owlCtx = makeOwlCtx(ctx);
  if (!owlCtx) {
    log.cli.warn("handleOwlSwitch: exit — no registry");
    return { kind: "error", text: "Specialized owl registry not initialized." };
  }

  await owlCtx.registry.loadAll(owlCtx.workspacePath);
  const specs = owlCtx.registry.listAll();
  log.cli.debug("handleOwlSwitch: registry loaded", { count: specs.length, lookingFor: name });
  const spec = specs.find(
    (s) => s.name.toLowerCase() === name.toLowerCase(),
  );

  if (!spec) {
    log.cli.warn("handleOwlSwitch: owl not found", { name });
    return { kind: "error", text: `Owl "${name}" not found. Use /owl list to see available owls.` };
  }

  ctx.bridge.changeOwl(spec.name, spec.emoji);
  log.cli.debug("handleOwlSwitch: exit", { name: spec.name, emoji: spec.emoji });
  return { kind: "system-message", text: `Switched to ${spec.emoji} ${spec.name}` };
};
