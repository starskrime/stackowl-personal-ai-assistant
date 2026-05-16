/**
 * /config pellets <verb> — knowledge pellet store configuration.
 *
 * list | set-embedding-model <model> | set-cache-size <n>
 * | dedup <enable|disable> | set-dedup-threshold <n>
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigPellets: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.pellets: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":                return pelletsList(ctx);
    case "set-embedding-model": return pelletsSetModel(ctx, rest);
    case "set-cache-size":      return pelletsSetCacheSize(ctx, rest);
    case "dedup":               return pelletsDedup(ctx, rest);
    case "set-dedup-threshold": return pelletsSetDedupThreshold(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config pellets <list|set-embedding-model|set-cache-size|dedup|set-dedup-threshold>",
      };
  }
};

async function pelletsList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.pellets.list: entry");
  const p = ctx.getOwlGateway().getConfig().pellets ?? {};
  const lines = [
    `embedding-model        ${p.embeddingModel ?? "nomic-embed-text"}`,
    `embedding-cache-size   ${p.embeddingCacheSize ?? 1000}`,
    `dedup-enabled          ${p.dedup?.enabled ?? false}`,
    `dedup-similarity       ${p.dedup?.similarityThreshold ?? 0.65}`,
    `dedup-skip-threshold   ${p.dedup?.skipThreshold ?? 0.85}`,
  ];
  log.cli.debug("config.pellets.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function pelletsSetModel(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.pellets.set-embedding-model: entry", { args });
  const model = args[0];
  if (!model) return { kind: "error", text: "Usage: /config pellets set-embedding-model <model>" };
  const p = ctx.getOwlGateway().getConfig().pellets ?? {};
  const result = await applyPatch(ctx, "pellets", { ...p, embeddingModel: model });
  log.cli.debug("config.pellets.set-embedding-model: exit", { model });
  return result;
}

async function pelletsSetCacheSize(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.pellets.set-cache-size: entry", { args });
  const parsed = parseInt(args[0] ?? "", 10);
  if (isNaN(parsed) || parsed < 1) {
    return { kind: "error", text: "Cache size must be a positive integer." };
  }
  const p = ctx.getOwlGateway().getConfig().pellets ?? {};
  const result = await applyPatch(ctx, "pellets", { ...p, embeddingCacheSize: parsed });
  log.cli.debug("config.pellets.set-cache-size: exit", { parsed });
  return result;
}

async function pelletsDedup(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.pellets.dedup: entry", { args });
  const state = args[0];
  if (state !== "enable" && state !== "disable") {
    return { kind: "error", text: "Usage: /config pellets dedup <enable|disable>" };
  }
  const p = ctx.getOwlGateway().getConfig().pellets ?? {};
  const result = await applyPatch(ctx, "pellets", { ...p, dedup: { ...p.dedup, enabled: state === "enable" } });
  log.cli.debug("config.pellets.dedup: exit", { state });
  return result;
}

async function pelletsSetDedupThreshold(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.pellets.set-dedup-threshold: entry", { args });
  const parsed = parseFloat(args[0] ?? "");
  if (isNaN(parsed) || parsed < 0 || parsed > 1) {
    return { kind: "error", text: "Threshold must be a float between 0 and 1." };
  }
  const p = ctx.getOwlGateway().getConfig().pellets ?? {};
  const result = await applyPatch(ctx, "pellets", { ...p, dedup: { ...p.dedup, similarityThreshold: parsed } });
  log.cli.debug("config.pellets.set-dedup-threshold: exit", { parsed });
  return result;
}
