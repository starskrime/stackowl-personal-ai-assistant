/**
 * /config research <verb> — deep-research behavior namespace.
 *
 * list | set <key> <value> | enable-auto-deep | disable-auto-deep
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

type ResearchIntKey = "selfCheckInterval" | "maxIterations" | "cloudFallbackAfter";
type ResearchFloatKey = "similarityThreshold";

const INT_KEYS: ResearchIntKey[] = ["selfCheckInterval", "maxIterations", "cloudFallbackAfter"];
const FLOAT_KEYS: ResearchFloatKey[] = ["similarityThreshold"];
const ALL_KEYS = [...INT_KEYS, ...FLOAT_KEYS] as const;

export const handleConfigResearch: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.research: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":               return researchList(ctx);
    case "set":                return researchSet(ctx, rest);
    case "enable-auto-deep":   return researchToggleAuto(ctx, true);
    case "disable-auto-deep":  return researchToggleAuto(ctx, false);
    case "enable-diminishing":  return researchToggleDiminishing(ctx, true);
    case "disable-diminishing": return researchToggleDiminishing(ctx, false);
    default:
      return {
        kind: "error",
        text: "Usage: /config research <list|set|enable-auto-deep|disable-auto-deep|enable-diminishing|disable-diminishing>",
      };
  }
};

async function researchList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.research.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const r = cfg.research ?? {};
  const lines = [
    `auto-deep                ${r.autoDeep ?? true}`,
    `self-check-interval      ${r.selfCheckInterval ?? 5}`,
    `max-iterations           ${r.maxIterations ?? 40}`,
    `enable-diminishing       ${r.enableDiminishingReturns ?? true}`,
    `similarity-threshold     ${r.similarityThreshold ?? 0.7}`,
    `cloud-fallback-after     ${r.cloudFallbackAfter ?? 2}`,
  ];
  log.cli.debug("config.research.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function researchSet(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.research.set: entry", { args });
  const [keyArg, valueArg] = args;

  if (!keyArg || !valueArg) {
    return {
      kind: "error",
      text: `Usage: /config research set <key> <value>\nKeys: ${ALL_KEYS.join(", ")}`,
    };
  }

  if (!ALL_KEYS.includes(keyArg as (typeof ALL_KEYS)[number])) {
    return {
      kind: "error",
      text: `Unknown research key "${keyArg}". Valid keys: ${ALL_KEYS.join(", ")}`,
    };
  }

  const isFloat = (FLOAT_KEYS as string[]).includes(keyArg);
  const parsed = isFloat ? parseFloat(valueArg) : parseInt(valueArg, 10);
  if (isNaN(parsed) || parsed < 0) {
    return { kind: "error", text: `Value must be a non-negative number. Got: "${valueArg}"` };
  }

  const result = await applyPatch(ctx, "research", { [keyArg]: parsed });
  log.cli.debug("config.research.set: exit", { key: keyArg, parsed });
  return result;
}

async function researchToggleAuto(
  ctx: Parameters<CommandHandler>[0],
  autoDeep: boolean,
): Promise<CommandResult> {
  log.cli.debug("config.research.toggle-auto: entry", { autoDeep });
  const result = await applyPatch(ctx, "research", { autoDeep });
  log.cli.debug("config.research.toggle-auto: exit", { autoDeep });
  return result;
}

async function researchToggleDiminishing(
  ctx: Parameters<CommandHandler>[0],
  enabled: boolean,
): Promise<CommandResult> {
  log.cli.debug("config.research.toggle-diminishing: entry", { enabled });
  const result = await applyPatch(ctx, "research", { enableDiminishingReturns: enabled });
  log.cli.debug("config.research.toggle-diminishing: exit", { enabled });
  return result;
}
