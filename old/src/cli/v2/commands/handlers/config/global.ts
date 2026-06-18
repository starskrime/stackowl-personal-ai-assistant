/**
 * /config <global-verb> — cross-cutting config operations.
 *
 * validate | show | diff | reload | export [path] | import <path>
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { log } from "../../../../../logger.js";
import { configReloadBus } from "../../../../../config/reload-bus.js";
import type { StackOwlConfig } from "../../../../../config/loader.js";

export const handleConfigGlobal: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.global: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "validate": return configValidate(ctx);
    case "show":     return configShow(ctx);
    case "diff":     return configDiff(ctx);
    case "reload":   return configReload(ctx);
    case "export":   return configExport(ctx, rest);
    case "import":   return configImport(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config <validate|show|diff|reload|export|import>",
      };
  }
};

// ─── validate ─────────────────────────────────────────────────────

async function configValidate(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.validate: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const errors: string[] = [];

  if (!cfg.defaultProvider) errors.push("defaultProvider is required");
  if (!cfg.defaultModel)    errors.push("defaultModel is required");
  if (!cfg.providers || Object.keys(cfg.providers).length === 0) {
    errors.push("at least one provider is required");
  } else if (cfg.defaultProvider && !cfg.providers[cfg.defaultProvider]) {
    errors.push(`defaultProvider "${cfg.defaultProvider}" not found in providers`);
  }
  if (cfg.gateway.port < 1 || cfg.gateway.port > 65535) {
    errors.push(`gateway.port ${cfg.gateway.port} is out of range`);
  }

  log.cli.debug("config.validate: exit", { errors: errors.length });

  if (errors.length > 0) {
    return { kind: "error", text: `Config validation failed:\n${errors.map((e) => `  • ${e}`).join("\n")}` };
  }
  return { kind: "system-message", text: "✅ Config is valid." };
}

// ─── show ─────────────────────────────────────────────────────────

async function configShow(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.show: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const sanitized = sanitizeConfig(cfg);
  log.cli.debug("config.show: exit");
  return { kind: "system-message", text: JSON.stringify(sanitized, null, 2) };
}

// ─── diff ─────────────────────────────────────────────────────────

async function configDiff(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.diff: entry");
  const basePath = ctx.getOwlGateway().getWorkspacePath();

  try {
    const { loadConfig } = await import("../../../../../config/loader.js");
    const ondisk = await loadConfig(basePath);
    const live = ctx.getOwlGateway().getConfig();
    const diff = buildDiff(ondisk, live);

    if (diff.length === 0) {
      return { kind: "system-message", text: "Live config matches on-disk config." };
    }

    log.cli.debug("config.diff: exit", { changes: diff.length });
    return { kind: "system-message", text: ["Unsaved differences:", ...diff].join("\n") };
  } catch (err) {
    log.cli.error("config.diff: failed", err as Error);
    return { kind: "error", text: `Failed to read on-disk config: ${(err as Error).message}` };
  }
}

// ─── reload ───────────────────────────────────────────────────────

async function configReload(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.reload: entry");
  const basePath = ctx.getOwlGateway().getWorkspacePath();

  try {
    const { loadConfig } = await import("../../../../../config/loader.js");
    const fresh = await loadConfig(basePath);
    const live = ctx.getOwlGateway().getConfig();

    const hotSections: (keyof StackOwlConfig)[] = [
      "engine", "heartbeat", "logging", "research", "cognition",
      "parliament", "costs", "rateLimiting", "tools", "perches", "browser",
    ];
    let reloaded = 0;

    for (const section of hotSections) {
      const next = fresh[section];
      const prev = live[section];
      if (JSON.stringify(next) !== JSON.stringify(prev)) {
        (live[section] as typeof next) = next;
        await configReloadBus.emit(section, next as never, prev as never);
        reloaded++;
      }
    }

    log.cli.debug("config.reload: exit", { reloaded });
    return {
      kind: "system-message",
      text: reloaded > 0
        ? `✓ Reloaded ${reloaded} section(s) from disk.`
        : "No hot-reloadable changes found on disk.",
    };
  } catch (err) {
    log.cli.error("config.reload: failed", err as Error);
    return { kind: "error", text: `Reload failed: ${(err as Error).message}` };
  }
}

// ─── export ───────────────────────────────────────────────────────

async function configExport(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.export: entry", { args });
  const { join } = await import("node:path");
  const { writeFile } = await import("node:fs/promises");

  const basePath = ctx.getOwlGateway().getWorkspacePath();
  const outPath = args[0] ?? join(basePath, "stackowl.config.export.json");
  const cfg = ctx.getOwlGateway().getConfig();
  const sanitized = sanitizeConfig(cfg);

  try {
    await writeFile(outPath, JSON.stringify(sanitized, null, 2), "utf8");
    log.cli.debug("config.export: exit", { outPath });
    return { kind: "system-message", text: `✓ Config exported to ${outPath}` };
  } catch (err) {
    log.cli.error("config.export: write failed", err as Error);
    return { kind: "error", text: `Export failed: ${(err as Error).message}` };
  }
}

// ─── import ───────────────────────────────────────────────────────

async function configImport(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.import: entry", { args });
  const filePath = args[0];
  if (!filePath) return { kind: "error", text: "Usage: /config import <path>" };

  try {
    const { readFile } = await import("node:fs/promises");
    const raw = await readFile(filePath, "utf8");
    const parsed = JSON.parse(raw) as Partial<StackOwlConfig>;

    const { saveConfig } = await import("../../../../../config/loader.js");
    const basePath = ctx.getOwlGateway().getWorkspacePath();
    const live = ctx.getOwlGateway().getConfig();

    Object.assign(live, parsed);
    await saveConfig(basePath, live);

    log.cli.debug("config.import: exit", { filePath });
    return { kind: "system-message", text: `✓ Config imported from ${filePath}. ⚠ Restart StackOwl to apply all changes.` };
  } catch (err) {
    log.cli.error("config.import: failed", err as Error);
    return { kind: "error", text: `Import failed: ${(err as Error).message}` };
  }
}

// ─── Helpers ──────────────────────────────────────────────────────

function sanitizeConfig(cfg: StackOwlConfig): unknown {
  const clone = JSON.parse(JSON.stringify(cfg)) as Record<string, unknown>;

  const providers = clone["providers"] as Record<string, Record<string, unknown>> | undefined;
  if (providers) {
    for (const entry of Object.values(providers)) {
      if (entry["apiKey"]) entry["apiKey"] = "***";
    }
  }

  const telegram = clone["telegram"] as Record<string, unknown> | undefined;
  if (telegram?.["botToken"]) telegram["botToken"] = "***";

  const slack = clone["slack"] as Record<string, unknown> | undefined;
  if (slack?.["botToken"])  slack["botToken"]  = "***";
  if (slack?.["appToken"])  slack["appToken"]  = "***";

  const discord = clone["discord"] as Record<string, unknown> | undefined;
  if (discord?.["botToken"]) discord["botToken"] = "***";

  return clone;
}

function buildDiff(a: unknown, b: unknown, path = ""): string[] {
  const lines: string[] = [];
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    if (JSON.stringify(a) !== JSON.stringify(b)) {
      lines.push(`  ${path}: ${JSON.stringify(a)} → ${JSON.stringify(b)}`);
    }
    return lines;
  }
  const allKeys = new Set([...Object.keys(a as object), ...Object.keys(b as object)]);
  for (const key of allKeys) {
    lines.push(...buildDiff(
      (a as Record<string, unknown>)[key],
      (b as Record<string, unknown>)[key],
      path ? `${path}.${key}` : key,
    ));
  }
  return lines;
}
