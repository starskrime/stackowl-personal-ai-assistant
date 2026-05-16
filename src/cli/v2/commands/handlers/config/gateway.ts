/**
 * /config gateway <verb> — HTTP gateway namespace.
 *
 * list | set-port <n> | set-host <host> | set-output-mode <normal|debug>
 * | rate-limit <max-per-minute> <max-per-hour>
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigGateway: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.gateway: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":           return gatewayList(ctx);
    case "set-port":       return gatewaySetPort(ctx, rest);
    case "set-host":       return gatewaySetHost(ctx, rest);
    case "set-output-mode": return gatewaySetOutputMode(ctx, rest);
    case "rate-limit":     return gatewayRateLimit(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config gateway <list|set-port|set-host|set-output-mode|rate-limit>",
      };
  }
};

async function gatewayList(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.gateway.list: entry");
  const { gateway } = ctx.getOwlGateway().getConfig();
  const lines = [
    `port           ${gateway.port}`,
    `host           ${gateway.host}`,
    `output-mode    ${gateway.outputMode ?? "normal"}`,
    `rate-limit     ${gateway.rateLimit ? `${gateway.rateLimit.maxPerMinute}/min  ${gateway.rateLimit.maxPerHour}/hr` : "(none)"}`,
  ];
  log.cli.debug("config.gateway.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function gatewaySetPort(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.gateway.set-port: entry", { args });
  const port = parseInt(args[0] ?? "", 10);
  if (isNaN(port) || port < 1 || port > 65535) {
    return { kind: "error", text: "Port must be an integer between 1 and 65535." };
  }
  const { gateway } = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "gateway", { ...gateway, port }, { restartRequired: true });
  log.cli.debug("config.gateway.set-port: exit", { port });
  return result;
}

async function gatewaySetHost(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.gateway.set-host: entry", { args });
  const host = args[0];
  if (!host) return { kind: "error", text: "Usage: /config gateway set-host <host>" };
  const { gateway } = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "gateway", { ...gateway, host }, { restartRequired: true });
  log.cli.debug("config.gateway.set-host: exit", { host });
  return result;
}

async function gatewaySetOutputMode(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.gateway.set-output-mode: entry", { args });
  const mode = args[0];
  if (mode !== "normal" && mode !== "debug") {
    return { kind: "error", text: "Output mode must be 'normal' or 'debug'." };
  }
  const { gateway } = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "gateway", { ...gateway, outputMode: mode });
  log.cli.debug("config.gateway.set-output-mode: exit", { mode });
  return result;
}

async function gatewayRateLimit(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.gateway.rate-limit: entry", { args });
  const [perMin, perHour] = args;
  const maxPerMinute = parseInt(perMin ?? "", 10);
  const maxPerHour = parseInt(perHour ?? "", 10);
  if (isNaN(maxPerMinute) || maxPerMinute < 1) {
    return { kind: "error", text: "Usage: /config gateway rate-limit <max-per-minute> [max-per-hour]" };
  }
  const { gateway } = ctx.getOwlGateway().getConfig();
  const rateLimit = isNaN(maxPerHour)
    ? { maxPerMinute }
    : { maxPerMinute, maxPerHour };
  const result = await applyPatch(ctx, "gateway", { ...gateway, rateLimit });
  log.cli.debug("config.gateway.rate-limit: exit", { maxPerMinute, maxPerHour });
  return result;
}
