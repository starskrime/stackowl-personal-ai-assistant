/**
 * /config provider <verb> — provider management namespace.
 *
 * list | add | remove | set-key | set-model | set-url | set-default | test
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { applyPatch, maskKey } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigProvider: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.provider: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":    return providerList(ctx);
    case "add":     return providerAdd(ctx, rest);
    case "remove":  return providerRemove(ctx, rest);
    case "set-key": return providerSetKey(ctx, rest);
    case "set-model": return providerSetModel(ctx, rest);
    case "set-url": return providerSetUrl(ctx, rest);
    case "set-default": return providerSetDefault(ctx, rest);
    case "test":    return providerTest(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config provider <list|add|remove|set-key|set-model|set-url|set-default|test>",
      };
  }
};

// ─── list ─────────────────────────────────────────────────────────

async function providerList(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const lines = [`Default: ${cfg.defaultProvider} / ${cfg.defaultModel}`, ""];
  for (const [name, entry] of Object.entries(cfg.providers)) {
    const model = entry.activeModel ?? entry.defaultModel ?? "<unset>";
    const url = entry.baseUrl ?? "<unset>";
    const key = maskKey(entry.apiKey);
    const type = entry.type ?? "—";
    lines.push(`${name}  model=${model}  url=${url}  key=${key}  type=${type}`);
  }
  log.cli.debug("config.provider.list: exit", { count: Object.keys(cfg.providers).length });
  return { kind: "system-message", text: lines.join("\n") };
}

// ─── add ──────────────────────────────────────────────────────────

async function providerAdd(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.add: entry", { args });
  const name = args[0];
  if (!name) return { kind: "error", text: "Usage: /config provider add <name> [--type <t>] [--base-url <url>] [--api-key <key>] [--model <model>]" };

  const flags = parseFlags(args.slice(1));
  const cfg = ctx.getOwlGateway().getConfig();

  if (cfg.providers[name]) {
    return { kind: "error", text: `Provider "${name}" already exists. Use set-key/set-model/set-url to edit.` };
  }

  const entry: Record<string, string> = {};
  if (flags["type"])     entry["type"]        = flags["type"]!;
  if (flags["base-url"]) entry["baseUrl"]     = flags["base-url"]!;
  if (flags["api-key"])  entry["apiKey"]      = flags["api-key"]!;
  if (flags["model"])    entry["activeModel"] = flags["model"]!;

  const patch = { providers: { ...cfg.providers, [name]: entry } };
  log.cli.debug("config.provider.add: step — patching providers", { name });
  const result = await applyPatch(ctx, "providers", patch.providers, { restartRequired: true });
  log.cli.debug("config.provider.add: exit", { name });
  return result;
}

// ─── remove ───────────────────────────────────────────────────────

async function providerRemove(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.remove: entry", { args });
  const [name, confirmFlag] = args;
  if (!name) return { kind: "error", text: "Usage: /config provider remove <name> --confirm" };
  if (confirmFlag !== "--confirm") {
    return { kind: "error", text: `⚠ This removes provider "${name}". Re-run with --confirm to proceed.` };
  }

  const cfg = ctx.getOwlGateway().getConfig();
  if (!cfg.providers[name]) return { kind: "error", text: `Provider "${name}" not found.` };
  if (cfg.defaultProvider === name) return { kind: "error", text: `Cannot remove default provider "${name}". Set a new default first.` };

  const updated = { ...cfg.providers };
  delete updated[name];

  log.cli.debug("config.provider.remove: step — removing provider", { name });
  const result = await applyPatch(ctx, "providers", updated, { restartRequired: true });
  log.cli.debug("config.provider.remove: exit", { name });
  return result;
}

// ─── set-key ──────────────────────────────────────────────────────

async function providerSetKey(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.set-key: entry", { name: args[0] });
  const [name, key] = args;
  if (!name || !key) return { kind: "error", text: "Usage: /config provider set-key <name> <api-key>" };

  const cfg = ctx.getOwlGateway().getConfig();
  if (!cfg.providers[name]) return { kind: "error", text: `Provider "${name}" not found. Run /config provider list.` };

  const result = await applyPatch(ctx, "providers", {
    ...cfg.providers,
    [name]: { ...cfg.providers[name], apiKey: key },
  }, { restartRequired: true });
  log.cli.debug("config.provider.set-key: exit", { name, masked: maskKey(key) });
  return result;
}

// ─── set-model ────────────────────────────────────────────────────

async function providerSetModel(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.set-model: entry", { args });
  const [name, model] = args;
  if (!name || !model) return { kind: "error", text: "Usage: /config provider set-model <name> <model>" };

  const cfg = ctx.getOwlGateway().getConfig();
  if (!cfg.providers[name]) return { kind: "error", text: `Provider "${name}" not found.` };

  const result = await applyPatch(ctx, "providers", {
    ...cfg.providers,
    [name]: { ...cfg.providers[name], activeModel: model },
  }, { restartRequired: true });
  log.cli.debug("config.provider.set-model: exit", { name, model });
  return result;
}

// ─── set-url ──────────────────────────────────────────────────────

async function providerSetUrl(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.set-url: entry", { args });
  const [name, url] = args;
  if (!name || !url) return { kind: "error", text: "Usage: /config provider set-url <name> <url>" };
  if (!url.startsWith("http")) return { kind: "error", text: "URL must start with http:// or https://" };

  const cfg = ctx.getOwlGateway().getConfig();
  if (!cfg.providers[name]) return { kind: "error", text: `Provider "${name}" not found.` };

  const result = await applyPatch(ctx, "providers", {
    ...cfg.providers,
    [name]: { ...cfg.providers[name], baseUrl: url },
  }, { restartRequired: true });
  log.cli.debug("config.provider.set-url: exit", { name, url });
  return result;
}

// ─── set-default ──────────────────────────────────────────────────

async function providerSetDefault(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.set-default: entry", { args });
  const [name] = args;
  if (!name) return { kind: "error", text: "Usage: /config provider set-default <name>" };

  const cfg = ctx.getOwlGateway().getConfig();
  if (!cfg.providers[name]) return { kind: "error", text: `Provider "${name}" not found.` };

  const live = ctx.getOwlGateway().getConfig();
  const basePath = ctx.getOwlGateway().getWorkspacePath();
  const { saveConfig } = await import("../../../../../config/loader.js");

  const prev = live.defaultProvider;
  live.defaultProvider = name;
  try {
    await saveConfig(basePath, live);
    log.cli.debug("config.provider.set-default: exit", { name });
    return { kind: "system-message", text: `✓ Saved. ⚠ Restart StackOwl to apply.` };
  } catch (err) {
    live.defaultProvider = prev;
    log.cli.error("config.provider.set-default: save failed", err as Error);
    return { kind: "error", text: `Failed to save: ${(err as Error).message}` };
  }
}

// ─── test ─────────────────────────────────────────────────────────

async function providerTest(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.provider.test: entry", { args });
  const [name] = args;
  if (!name) return { kind: "error", text: "Usage: /config provider test <name>" };

  const manager = ctx.getOwlGateway().getProviderManager();
  log.cli.debug("config.provider.test: step — calling testProvider", { name });
  const result = await manager.testProvider(name);
  const text = result.ok
    ? `✅ ${name} responded OK in ${result.latencyMs}ms`
    : `❌ ${name} failed in ${result.latencyMs}ms: ${result.error ?? "unknown error"}`;
  log.cli.debug("config.provider.test: exit", { name, ok: result.ok });
  return { kind: "system-message", text };
}

// ─── Flag parser ──────────────────────────────────────────────────

function parseFlags(args: string[]): Record<string, string> {
  const flags: Record<string, string> = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i]!;
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const val = args[i + 1];
      if (val && !val.startsWith("--")) {
        flags[key] = val;
        i++;
      }
    }
  }
  return flags;
}
