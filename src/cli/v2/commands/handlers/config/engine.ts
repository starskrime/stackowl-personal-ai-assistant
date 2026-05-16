/**
 * /config engine <verb> — ReAct loop tuning namespace.
 *
 * list | set <key> <value> | reset
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

type EngineKey =
  | "maxToolIterations"
  | "deepMaxToolIterations"
  | "maxContextTokens"
  | "maxToolResultLength"
  | "contextKeepRecent"
  | "maxRetries"
  | "maxToolFailStreak"
  | "baseRetryDelayMs"
  | "contextWindowThreshold"
  | "contextCompressionBatch"
  | "toolWindowSize"
  | "dnaBaseTemp"
  | "synthesizeEarlyThreshold";

const DEFAULTS: Record<EngineKey, number> = {
  maxToolIterations: 15,
  deepMaxToolIterations: 50,
  maxContextTokens: 8000,
  maxToolResultLength: 6000,
  contextKeepRecent: 10,
  maxRetries: 3,
  maxToolFailStreak: 50,
  baseRetryDelayMs: 1500,
  contextWindowThreshold: 20,
  contextCompressionBatch: 10,
  toolWindowSize: 12,
  dnaBaseTemp: 0.7,
  synthesizeEarlyThreshold: 0.3,
};

const FLOAT_KEYS = new Set<EngineKey>(["dnaBaseTemp", "synthesizeEarlyThreshold"]);

export const handleConfigEngine: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.engine: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":  return engineList(ctx);
    case "set":   return engineSet(ctx, rest);
    case "reset": return engineReset(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config engine <list|set|reset>",
      };
  }
};

// ─── list ─────────────────────────────────────────────────────────

async function engineList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.engine.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const eng = cfg.engine ?? {};
  const lines = ["Engine settings (current → default):"];

  for (const key of Object.keys(DEFAULTS) as EngineKey[]) {
    const current = (eng as Record<string, unknown>)[key];
    const def = DEFAULTS[key];
    const marker = current === undefined ? " (default)" : "";
    lines.push(`  ${key.padEnd(28)} ${(current ?? def)}${marker}`);
  }

  log.cli.debug("config.engine.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

// ─── set ──────────────────────────────────────────────────────────

async function engineSet(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.engine.set: entry", { args });
  const [keyArg, valueArg] = args;

  if (!keyArg || !valueArg) {
    return {
      kind: "error",
      text: `Usage: /config engine set <key> <value>\nKeys: ${Object.keys(DEFAULTS).join(", ")}`,
    };
  }

  if (!(keyArg in DEFAULTS)) {
    return {
      kind: "error",
      text: `Unknown engine key "${keyArg}". Valid keys: ${Object.keys(DEFAULTS).join(", ")}`,
    };
  }

  const key = keyArg as EngineKey;
  const parsed = FLOAT_KEYS.has(key) ? parseFloat(valueArg) : parseInt(valueArg, 10);
  if (isNaN(parsed)) {
    return { kind: "error", text: `Value must be a number. Got: "${valueArg}"` };
  }

  log.cli.debug("config.engine.set: step — patching engine", { key, parsed });
  const result = await applyPatch(ctx, "engine", { [key]: parsed });
  log.cli.debug("config.engine.set: exit", { key, parsed });
  return result;
}

// ─── reset ────────────────────────────────────────────────────────

async function engineReset(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.engine.reset: entry", { args });
  if (!args.includes("--confirm")) {
    return {
      kind: "error",
      text: "⚠ This resets all engine settings to defaults. Re-run with --confirm to proceed.",
    };
  }

  log.cli.debug("config.engine.reset: step — clearing engine section");
  const result = await applyPatch(ctx, "engine", DEFAULTS);
  log.cli.debug("config.engine.reset: exit");
  return result;
}
