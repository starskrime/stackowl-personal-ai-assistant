/**
 * /config tier <verb> — intelligence tier management namespace.
 *
 * list | set <low|mid|high> <provider> <model> | set-default <task> <tier> | reset
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";
import type { IntelligenceConfig } from "../../../../../intelligence/router.js";

const VALID_TIERS = ["low", "mid", "high"] as const;
type Tier = (typeof VALID_TIERS)[number];

const VALID_TASKS = [
  "conversation", "parliament", "evolution", "extraction",
  "episodic", "classification", "synthesis", "summarization", "clarification",
] as const;
type TaskType = (typeof VALID_TASKS)[number];

export const handleConfigTier: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.tier: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":        return tierList(ctx);
    case "set":         return tierSet(ctx, rest);
    case "set-default": return tierSetDefault(ctx, rest);
    case "reset":       return tierReset(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config tier <list|set|set-default|reset>",
      };
  }
};

// ─── list ─────────────────────────────────────────────────────────

async function tierList(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.tier.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const intel = cfg.intelligence;
  const lines: string[] = ["Tiers:"];

  if (intel?.tiers) {
    for (const t of VALID_TIERS) {
      const tier = intel.tiers[t];
      lines.push(`  ${t.padEnd(4)} → ${tier?.provider ?? "—"} / ${tier?.model ?? "—"}`);
    }
  } else {
    lines.push("  (none configured — all tasks use defaultProvider/defaultModel)");
  }

  lines.push("", "Task defaults:");
  if (intel?.defaults) {
    for (const task of VALID_TASKS) {
      lines.push(`  ${task.padEnd(16)} → ${intel.defaults[task] ?? "—"}`);
    }
  } else {
    lines.push("  (none configured — all tasks use mid)");
  }

  log.cli.debug("config.tier.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

// ─── set ──────────────────────────────────────────────────────────

async function tierSet(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.tier.set: entry", { args });
  const [tierArg, provider, model] = args;

  if (!tierArg || !VALID_TIERS.includes(tierArg as Tier)) {
    return { kind: "error", text: `Tier must be one of: ${VALID_TIERS.join(", ")}` };
  }
  if (!provider || !model) {
    return { kind: "error", text: `Usage: /config tier set <low|mid|high> <provider> <model>` };
  }

  const tier = tierArg as Tier;
  const cfg = ctx.getOwlGateway().getConfig();
  const existing = cfg.intelligence ?? buildDefaultIntelligence(cfg.defaultProvider, cfg.defaultModel);
  const updated: IntelligenceConfig = {
    ...existing,
    tiers: { ...existing.tiers, [tier]: { provider, model } },
  };

  log.cli.debug("config.tier.set: step — patching intelligence.tiers", { tier, provider, model });
  const result = await applyPatch(ctx, "intelligence", updated);
  log.cli.debug("config.tier.set: exit", { tier });
  return result;
}

// ─── set-default ──────────────────────────────────────────────────

async function tierSetDefault(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.tier.set-default: entry", { args });
  const [taskArg, tierArg] = args;

  if (!taskArg || !VALID_TASKS.includes(taskArg as TaskType)) {
    return { kind: "error", text: `Task must be one of: ${VALID_TASKS.join(", ")}` };
  }
  if (!tierArg || !VALID_TIERS.includes(tierArg as Tier)) {
    return { kind: "error", text: `Tier must be one of: ${VALID_TIERS.join(", ")}` };
  }

  const task = taskArg as TaskType;
  const tier = tierArg as Tier;
  const cfg = ctx.getOwlGateway().getConfig();
  const existing = cfg.intelligence ?? buildDefaultIntelligence(cfg.defaultProvider, cfg.defaultModel);
  const updated: IntelligenceConfig = {
    ...existing,
    defaults: { ...existing.defaults, [task]: tier },
  };

  log.cli.debug("config.tier.set-default: step — patching intelligence.defaults", { task, tier });
  const result = await applyPatch(ctx, "intelligence", updated);
  log.cli.debug("config.tier.set-default: exit", { task, tier });
  return result;
}

// ─── reset ────────────────────────────────────────────────────────

async function tierReset(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.tier.reset: entry", { args });
  if (!args.includes("--confirm")) {
    return { kind: "error", text: "⚠ This resets all tiers and defaults. Re-run with --confirm to proceed." };
  }

  const cfg = ctx.getOwlGateway().getConfig();
  const reset = buildDefaultIntelligence(cfg.defaultProvider, cfg.defaultModel);
  const result = await applyPatch(ctx, "intelligence", reset);
  log.cli.debug("config.tier.reset: exit");
  return result;
}

// ─── Helpers ──────────────────────────────────────────────────────

function buildDefaultIntelligence(provider: string, model: string): IntelligenceConfig {
  const tier = { provider, model };
  return {
    tiers: { low: tier, mid: tier, high: tier },
    defaults: {
      conversation:   "low",
      parliament:     "low",
      evolution:      "low",
      extraction:     "low",
      episodic:       "low",
      classification: "low",
      synthesis:      "low",
      summarization:  "low",
      clarification:  "low",
    },
  };
}
