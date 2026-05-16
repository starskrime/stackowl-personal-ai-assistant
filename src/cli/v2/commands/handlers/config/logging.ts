/**
 * /config logging <verb> — structured logging configuration.
 *
 * list | set-level <debug|info|warn|error> | set-retention <days>
 * | set-buffer-size <n> | sink <file|ring-buffer|pretty-console> <on|off>
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

const VALID_LEVELS = ["debug", "info", "warn", "error"] as const;
type LogLevel = (typeof VALID_LEVELS)[number];

const VALID_SINKS = ["file", "ring-buffer", "pretty-console"] as const;
type SinkName = (typeof VALID_SINKS)[number];

const SINK_FIELD_MAP: Record<SinkName, "file" | "ringBuffer" | "prettyConsole"> = {
  "file": "file",
  "ring-buffer": "ringBuffer",
  "pretty-console": "prettyConsole",
};

export const handleConfigLogging: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.logging: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":           return loggingList(ctx);
    case "set-level":      return loggingSetLevel(ctx, rest);
    case "set-retention":  return loggingSetInt(ctx, "retentionDays", rest, 1);
    case "set-buffer-size": return loggingSetInt(ctx, "ringBufferSize", rest, 100);
    case "sink":           return loggingSink(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config logging <list|set-level|set-retention|set-buffer-size|sink>",
      };
  }
};

async function loggingList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.logging.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const l = cfg.logging ?? {};
  const lines = [
    `level            ${l.level ?? "info"}`,
    `retention-days   ${l.retentionDays ?? 7}`,
    `ring-buffer-size ${l.ringBufferSize ?? 5000}`,
    `sinks:`,
    `  file           ${l.sinks?.file ?? true}`,
    `  ring-buffer    ${l.sinks?.ringBuffer ?? true}`,
    `  pretty-console ${l.sinks?.prettyConsole ?? false}`,
  ];
  log.cli.debug("config.logging.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function loggingSetLevel(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.logging.set-level: entry", { args });
  const level = args[0];
  if (!level || !VALID_LEVELS.includes(level as LogLevel)) {
    return { kind: "error", text: `Level must be one of: ${VALID_LEVELS.join(", ")}` };
  }
  const cfg = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "logging", { ...cfg.logging, level: level as LogLevel });
  log.cli.debug("config.logging.set-level: exit", { level });
  return result;
}

async function loggingSetInt(
  ctx: Parameters<CommandHandler>[0],
  field: "retentionDays" | "ringBufferSize",
  args: string[],
  min: number,
): Promise<CommandResult> {
  log.cli.debug(`config.logging.${field}: entry`, { args });
  const parsed = parseInt(args[0] ?? "", 10);
  if (isNaN(parsed) || parsed < min) {
    return { kind: "error", text: `Value must be an integer ≥ ${min}. Got: "${args[0] ?? ""}"` };
  }
  const cfg = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "logging", { ...cfg.logging, [field]: parsed });
  log.cli.debug(`config.logging.${field}: exit`, { parsed });
  return result;
}

async function loggingSink(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.logging.sink: entry", { args });
  const [sinkArg, stateArg] = args;

  if (!sinkArg || !VALID_SINKS.includes(sinkArg as SinkName)) {
    return { kind: "error", text: `Sink must be one of: ${VALID_SINKS.join(", ")}` };
  }
  if (stateArg !== "on" && stateArg !== "off") {
    return { kind: "error", text: "State must be 'on' or 'off'." };
  }

  const field = SINK_FIELD_MAP[sinkArg as SinkName];
  const cfg = ctx.getOwlGateway().getConfig();
  const sinks = { ...cfg.logging?.sinks, [field]: stateArg === "on" };
  const result = await applyPatch(ctx, "logging", { ...cfg.logging, sinks });
  log.cli.debug("config.logging.sink: exit", { sink: sinkArg, state: stateArg });
  return result;
}
