/**
 * StackOwl — /provider CLI v2 Handlers
 *
 * TUI command handlers for provider management:
 *   list, test, delete, edit
 */

import type { CommandHandler } from "../registry.js";
import type { ProviderStatus } from "../../../../providers/manager.js";
import { log } from "../../../../logger.js";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function healthDot(health: ProviderStatus["health"]): string {
  switch (health) {
    case "CLOSED":       return "✅";
    case "HALF_OPEN":    return "⚡";
    case "OPEN":         return "❌";
    case "unconfigured": return "○";
  }
}

// ─── /provider list (and bare /provider) ─────────────────────────────────────

export const handleProviderList: CommandHandler = async (ctx, _args) => {
  log.cli.debug("handleProviderList: entry");
  const manager = ctx.getOwlGateway().getProviderManager();
  const statuses = manager.listProviders();
  log.cli.debug("handleProviderList: decision — building panel items", { count: statuses.length });

  const items = statuses.map((s) => ({
    id: s.name,
    label: s.name,
    meta: [
      healthDot(s.health),
      s.isDefault ? "★" : "",
      s.activeModel,
      `[${s.source}]`,
    ]
      .filter(Boolean)
      .join(" "),
    data: s,
  }));

  log.cli.debug("handleProviderList: exit", { itemCount: items.length });
  return {
    kind: "panel",
    payload: {
      title: "/provider",
      items,
      emptyText: "No providers configured.",
    },
  };
};

// ─── /provider test <name> ────────────────────────────────────────────────────

export const handleProviderTest: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleProviderTest: entry", { args });
  const name = args[0];
  if (!name) {
    log.cli.warn("handleProviderTest: no name provided");
    return { kind: "error", text: "Usage: /provider test <name>" };
  }

  log.cli.debug("handleProviderTest: decision — delegating to testProvider", { name });
  const manager = ctx.getOwlGateway().getProviderManager();
  log.cli.debug("handleProviderTest: step — calling testProvider", { name });
  const result = await manager.testProvider(name);

  const text = result.ok
    ? `✅ ${name} responded OK in ${result.latencyMs}ms`
    : `❌ ${name} failed in ${result.latencyMs}ms: ${result.error ?? "unknown error"}`;

  log.cli.debug("handleProviderTest: exit", { name, ok: result.ok, latencyMs: result.latencyMs });
  return { kind: "system-message", text };
};

// ─── /provider delete <name> ──────────────────────────────────────────────────

export const handleProviderDelete: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleProviderDelete: entry", { args });
  const name = args[0];
  if (!name) {
    log.cli.warn("handleProviderDelete: no name provided");
    return { kind: "error", text: "Usage: /provider delete <name>" };
  }

  log.cli.debug("handleProviderDelete: decision — delegating to deleteProvider", { name });
  const manager = ctx.getOwlGateway().getProviderManager();
  log.cli.debug("handleProviderDelete: step — calling deleteProvider", { name });
  try {
    await manager.deleteProvider(name);
    log.cli.debug("handleProviderDelete: exit", { name });
    return { kind: "system-message", text: `Provider "${name}" removed.` };
  } catch (err) {
    log.cli.error("handleProviderDelete: deleteProvider threw", err as Error, { name });
    return { kind: "error", text: (err as Error).message };
  }
};

// ─── /provider edit <name> <key|model|url> <value> ───────────────────────────

export const handleProviderEdit: CommandHandler = async (ctx, args) => {
  log.cli.debug("handleProviderEdit: entry", { args });
  const [name, field, ...rest] = args;
  const value = rest.join(" ");

  if (!name || !field || !value) {
    log.cli.warn("handleProviderEdit: missing arguments", { name, field, value });
    return {
      kind: "error",
      text: "Usage: /provider edit <name> <key|model|url> <value>",
    };
  }

  const updates: Record<string, string> = {};
  if (field === "key")            updates.apiKey      = value;
  else if (field === "model")     updates.activeModel = value;
  else if (field === "url")       updates.baseUrl     = value;
  else {
    log.cli.warn("handleProviderEdit: unknown field", { field });
    return { kind: "error", text: "Field must be one of: key, model, url" };
  }

  log.cli.debug("handleProviderEdit: decision — applying field update", { name, field });
  const manager = ctx.getOwlGateway().getProviderManager();
  log.cli.debug("handleProviderEdit: step — calling editProvider", { name, field });
  try {
    await manager.editProvider(name, updates);
    log.cli.debug("handleProviderEdit: exit", { name, field });
    return { kind: "system-message", text: `${name}.${field} updated.` };
  } catch (err) {
    log.cli.error("handleProviderEdit: editProvider threw", err as Error, { name, field });
    return { kind: "error", text: (err as Error).message };
  }
};
