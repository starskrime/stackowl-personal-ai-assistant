/**
 * /config cost <verb> — cost tracking and budget enforcement namespace.
 *
 * list | enable | disable | set-budget <key> <value> | reset
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

type BudgetKey = "maxDailyUsd" | "maxMonthlyUsd" | "maxPerRequestTokens" | "warnAtPercent";

const BUDGET_KEYS = new Set<BudgetKey>([
  "maxDailyUsd",
  "maxMonthlyUsd",
  "maxPerRequestTokens",
  "warnAtPercent",
]);

export const handleConfigCost: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.cost: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":       return costList(ctx);
    case "enable":     return costEnable(ctx, true);
    case "disable":    return costEnable(ctx, false);
    case "set-budget": return costSetBudget(ctx, rest);
    case "reset":      return costReset(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config cost <list|enable|disable|set-budget|reset>",
      };
  }
};

// ─── list ─────────────────────────────────────────────────────────

async function costList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.cost.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const costs = cfg.costs;
  const lines = [
    `Cost tracking: ${costs?.enabled ? "enabled" : "disabled"}`,
    "",
    "Budget limits:",
  ];

  for (const key of BUDGET_KEYS) {
    const val = costs?.budget?.[key as keyof typeof costs.budget];
    lines.push(`  ${key.padEnd(24)} ${val ?? "(unset)"}`);
  }

  log.cli.debug("config.cost.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

// ─── enable / disable ─────────────────────────────────────────────

async function costEnable(
  ctx: Parameters<CommandHandler>[0],
  enabled: boolean,
): Promise<CommandResult> {
  log.cli.debug("config.cost.enable: entry", { enabled });
  const cfg = ctx.getOwlGateway().getConfig();
  const patch = { enabled, budget: cfg.costs?.budget };
  const result = await applyPatch(ctx, "costs", patch);
  log.cli.debug("config.cost.enable: exit", { enabled });
  return result;
}

// ─── set-budget ───────────────────────────────────────────────────

async function costSetBudget(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.cost.set-budget: entry", { args });
  const [keyArg, valueArg] = args;

  if (!keyArg || !valueArg) {
    return {
      kind: "error",
      text: `Usage: /config cost set-budget <key> <value>\nKeys: ${[...BUDGET_KEYS].join(", ")}`,
    };
  }

  if (!BUDGET_KEYS.has(keyArg as BudgetKey)) {
    return {
      kind: "error",
      text: `Unknown budget key "${keyArg}". Valid keys: ${[...BUDGET_KEYS].join(", ")}`,
    };
  }

  const parsed = parseFloat(valueArg);
  if (isNaN(parsed) || parsed < 0) {
    return { kind: "error", text: `Value must be a non-negative number. Got: "${valueArg}"` };
  }

  const cfg = ctx.getOwlGateway().getConfig();
  const updatedBudget = { ...cfg.costs?.budget, [keyArg]: parsed };
  const result = await applyPatch(ctx, "costs", { enabled: cfg.costs?.enabled ?? true, budget: updatedBudget });
  log.cli.debug("config.cost.set-budget: exit", { key: keyArg, parsed });
  return result;
}

// ─── reset ────────────────────────────────────────────────────────

async function costReset(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.cost.reset: entry", { args });
  if (!args.includes("--confirm")) {
    return {
      kind: "error",
      text: "⚠ This resets all cost/budget settings. Re-run with --confirm to proceed.",
    };
  }

  log.cli.debug("config.cost.reset: step — clearing costs section");
  const result = await applyPatch(ctx, "costs", { enabled: false });
  log.cli.debug("config.cost.reset: exit");
  return result;
}
