/**
 * /config parliament <verb> — multi-owl debate configuration.
 *
 * list | set-rounds <n> | set-owls <n>
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigParliament: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.parliament: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":       return parliamentList(ctx);
    case "set-rounds": return parliamentSetField(ctx, "maxRounds", rest);
    case "set-owls":   return parliamentSetField(ctx, "maxOwls", rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config parliament <list|set-rounds|set-owls>",
      };
  }
};

async function parliamentList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.parliament.list: entry");
  const { parliament } = ctx.getOwlGateway().getConfig();
  const lines = [
    `max-rounds    ${parliament.maxRounds}`,
    `max-owls      ${parliament.maxOwls}`,
  ];
  log.cli.debug("config.parliament.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function parliamentSetField(
  ctx: Parameters<CommandHandler>[0],
  field: "maxRounds" | "maxOwls",
  args: string[],
): Promise<CommandResult> {
  log.cli.debug(`config.parliament.${field}: entry`, { args });
  const parsed = parseInt(args[0] ?? "", 10);
  if (isNaN(parsed) || parsed < 1) {
    return { kind: "error", text: `Value must be a positive integer. Got: "${args[0] ?? ""}"` };
  }
  const result = await applyPatch(ctx, "parliament", { [field]: parsed });
  log.cli.debug(`config.parliament.${field}: exit`, { parsed });
  return result;
}
