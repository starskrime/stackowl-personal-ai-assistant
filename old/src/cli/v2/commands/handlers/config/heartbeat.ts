/**
 * /config heartbeat <verb> — proactive notification configuration.
 *
 * list | enable | disable | set-interval <minutes> | set-cooldown <minutes>
 * | set-max-unanswered <n>
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigHeartbeat: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.heartbeat: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":              return heartbeatList(ctx);
    case "enable":            return heartbeatToggle(ctx, true);
    case "disable":           return heartbeatToggle(ctx, false);
    case "set-interval":      return heartbeatSetInt(ctx, "intervalMinutes", rest, 1);
    case "set-cooldown":      return heartbeatSetInt(ctx, "minPingCooldownMinutes", rest, 0);
    case "set-max-unanswered": return heartbeatSetInt(ctx, "maxUnansweredPings", rest, 1);
    default:
      return {
        kind: "error",
        text: "Usage: /config heartbeat <list|enable|disable|set-interval|set-cooldown|set-max-unanswered>",
      };
  }
};

async function heartbeatList(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.heartbeat.list: entry");
  const { heartbeat } = ctx.getOwlGateway().getConfig();
  const lines = [
    `enabled              ${heartbeat.enabled}`,
    `interval-minutes     ${heartbeat.intervalMinutes}`,
    `min-ping-cooldown    ${heartbeat.minPingCooldownMinutes ?? 60}`,
    `max-unanswered       ${heartbeat.maxUnansweredPings ?? 1}`,
  ];
  log.cli.debug("config.heartbeat.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function heartbeatToggle(
  ctx: Parameters<CoreCommandHandler>[0],
  enabled: boolean,
): Promise<CoreCommandResult> {
  log.cli.debug("config.heartbeat.toggle: entry", { enabled });
  const { heartbeat } = ctx.getOwlGateway().getConfig();
  const result = await applyPatch(ctx, "heartbeat", { ...heartbeat, enabled });
  log.cli.debug("config.heartbeat.toggle: exit", { enabled });
  return result;
}

async function heartbeatSetInt(
  ctx: Parameters<CoreCommandHandler>[0],
  field: "intervalMinutes" | "minPingCooldownMinutes" | "maxUnansweredPings",
  args: string[],
  min: number,
): Promise<CoreCommandResult> {
  log.cli.debug(`config.heartbeat.${field}: entry`, { args });
  const parsed = parseInt(args[0] ?? "", 10);
  if (isNaN(parsed) || parsed < min) {
    return { kind: "error", text: `Value must be an integer ≥ ${min}. Got: "${args[0] ?? ""}"` };
  }
  const result = await applyPatch(ctx, "heartbeat", { [field]: parsed });
  log.cli.debug(`config.heartbeat.${field}: exit`, { parsed });
  return result;
}
