import type { CommandHandler, CommandContext } from "../registry.js";
import { dispatchOwlCommand } from "../../../../gateway/commands/owl-router.js";

function getRegistry(ctx: CommandContext) {
  return ctx.getOwlGateway().getSpecializedRegistry();
}

// Dynamic completer for helper names
export async function completeHelperNames(ctx: CommandContext, partial: string): Promise<string[]> {
  const registry = getRegistry(ctx);
  if (!registry) return [];
  return registry.listAll().map((s) => s.name).filter((n) => n.startsWith(partial));
}

// Build OwlRouterDeps (without real wizard — create is not supported in TUI)
function buildDeps(ctx: CommandContext) {
  const gateway = ctx.getOwlGateway();
  const registry = getRegistry(ctx);
  const workspacePath = gateway.getWorkspacePath();
  const noopWizard = {
    start: async () => "Use /helper create in the terminal — TUI wizard not yet supported.",
    isActive: () => false,
    cancel: () => {},
  };
  return { registry: registry!, wizard: noopWizard, userId: "local", channelAdapter: null, workspacePath };
}

export const handleHelperList: CommandHandler = async (ctx, _args) => {
  const registry = getRegistry(ctx);
  if (!registry) return { kind: "error", text: "Helper registry not available." };
  const helpers = registry.listAll();
  const items = helpers.map((h, i) => ({
    id: `helper-${i}`,
    label: `${h.emoji ?? "🦉"} ${h.name}`,
    meta: h.role,
  }));
  return { kind: "panel", payload: { title: "/helper list", items, emptyText: "No helpers yet. Use /helper create." } };
};

export const handleHelperShow: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /helper show <name>" };
  const deps = buildDeps(ctx);
  if (!deps.registry) return { kind: "error", text: "Helper registry not available." };
  const text = await dispatchOwlCommand("show", args, deps);
  const items = text.split("\n").filter((l) => l.trim()).map((line, i) => ({ id: `sh-${i}`, label: line }));
  return { kind: "panel", payload: { title: `/helper show ${args[0]}`, items } };
};

export const handleHelperCreate: CommandHandler = async (_ctx, _args) => {
  return { kind: "error", text: "Use /helper create in the terminal (v1 TUI) — wizard not yet supported in v2." };
};

export const handleHelperRename: CommandHandler = async (ctx, args) => {
  if (!args[0] || !args[1]) return { kind: "error", text: "Usage: /helper rename <old> <new>" };
  const deps = buildDeps(ctx);
  if (!deps.registry) return { kind: "error", text: "Helper registry not available." };
  const text = await dispatchOwlCommand("rename", args, deps);
  return { kind: "system-message", text };
};

export const handleHelperDelete: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /helper delete <name>" };
  const deps = buildDeps(ctx);
  if (!deps.registry) return { kind: "error", text: "Helper registry not available." };
  // dispatchOwlCommand("delete") requires second arg "yes" — pass it through from args
  const text = await dispatchOwlCommand("delete", args, deps);
  return { kind: "system-message", text };
};

export const handleHelperDesign: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /helper design <name>" };
  const deps = buildDeps(ctx);
  if (!deps.registry) return { kind: "error", text: "Helper registry not available." };
  const text = await dispatchOwlCommand("design", args, deps);
  return { kind: "system-message", text };
};

export const handleHelperCapabilities: CommandHandler = async (ctx, args) => {
  if (!args[0]) return { kind: "error", text: "Usage: /helper capabilities <name>" };
  const deps = buildDeps(ctx);
  if (!deps.registry) return { kind: "error", text: "Helper registry not available." };
  const text = await dispatchOwlCommand("capabilities", args, deps);
  return { kind: "system-message", text };
};
