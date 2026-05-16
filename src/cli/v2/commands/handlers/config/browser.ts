/**
 * /config browser <verb> — browser pool configuration.
 *
 * list | enable | disable | set-pool-size <n> | set-proxy <url|off>
 * | stealth <on|off>
 */

import type { CommandHandler, CommandResult } from "../../registry.js";
import { applyPatch } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigBrowser: CommandHandler = async (ctx, args) => {
  log.cli.debug("config.browser: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":          return browserList(ctx);
    case "enable":        return browserToggle(ctx, true);
    case "disable":       return browserToggle(ctx, false);
    case "set-pool-size": return browserSetPoolSize(ctx, rest);
    case "set-proxy":     return browserSetProxy(ctx, rest);
    case "stealth":       return browserStealth(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config browser <list|enable|disable|set-pool-size|set-proxy|stealth>",
      };
  }
};

async function browserList(ctx: Parameters<CommandHandler>[0]): Promise<CommandResult> {
  log.cli.debug("config.browser.list: entry");
  const b = ctx.getOwlGateway().getConfig().browser ?? {};
  const lines = [
    `enabled      ${b.enabled ?? true}`,
    `pool-size    ${b.poolSize ?? 2}`,
    `stealth      ${b.stealthMode ?? true}`,
    `headless     ${b.headless ?? true}`,
    `proxy        ${b.proxy ?? "(none)"}`,
    `warm-up      ${b.warmUp ?? true}`,
  ];
  log.cli.debug("config.browser.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

async function browserToggle(
  ctx: Parameters<CommandHandler>[0],
  enabled: boolean,
): Promise<CommandResult> {
  log.cli.debug("config.browser.toggle: entry", { enabled });
  const b = ctx.getOwlGateway().getConfig().browser ?? {};
  const result = await applyPatch(ctx, "browser", { ...b, enabled });
  log.cli.debug("config.browser.toggle: exit", { enabled });
  return result;
}

async function browserSetPoolSize(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.browser.set-pool-size: entry", { args });
  const parsed = parseInt(args[0] ?? "", 10);
  if (isNaN(parsed) || parsed < 1) {
    return { kind: "error", text: "Pool size must be a positive integer." };
  }
  const b = ctx.getOwlGateway().getConfig().browser ?? {};
  const result = await applyPatch(ctx, "browser", { ...b, poolSize: parsed });
  log.cli.debug("config.browser.set-pool-size: exit", { parsed });
  return result;
}

async function browserSetProxy(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.browser.set-proxy: entry", { args });
  const proxyArg = args[0];
  if (!proxyArg) return { kind: "error", text: "Usage: /config browser set-proxy <url|off>" };
  if (proxyArg !== "off" && !proxyArg.startsWith("http")) {
    return { kind: "error", text: "Proxy URL must start with http:// or https:// (or use 'off')." };
  }
  const b = ctx.getOwlGateway().getConfig().browser ?? {};
  const proxy = proxyArg === "off" ? undefined : proxyArg;
  const result = await applyPatch(ctx, "browser", { ...b, proxy });
  log.cli.debug("config.browser.set-proxy: exit", { proxy });
  return result;
}

async function browserStealth(
  ctx: Parameters<CommandHandler>[0],
  args: string[],
): Promise<CommandResult> {
  log.cli.debug("config.browser.stealth: entry", { args });
  const state = args[0];
  if (state !== "on" && state !== "off") {
    return { kind: "error", text: "Usage: /config browser stealth <on|off>" };
  }
  const b = ctx.getOwlGateway().getConfig().browser ?? {};
  const result = await applyPatch(ctx, "browser", { ...b, stealthMode: state === "on" });
  log.cli.debug("config.browser.stealth: exit", { state });
  return result;
}
