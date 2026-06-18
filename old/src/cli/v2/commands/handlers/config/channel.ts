/**
 * /config channel <verb> — communication channel namespace.
 *
 * list | telegram <subverb> [...] | slack <subverb> [...] | discord <subverb> [...]
 * | whatsapp <subverb> [...]
 *
 * All channel mutations require restart.
 */

import type { CoreCommandHandler, CoreCommandResult } from "../../registry.js";
import { applyPatch, maskKey } from "./shared.js";
import { log } from "../../../../../logger.js";

export const handleConfigChannel: CoreCommandHandler = async (ctx, args) => {
  log.cli.debug("config.channel: entry", { args });
  const [verb, ...rest] = args;

  switch (verb) {
    case "list":     return channelList(ctx);
    case "telegram": return channelTelegram(ctx, rest);
    case "slack":    return channelSlack(ctx, rest);
    case "discord":  return channelDiscord(ctx, rest);
    case "whatsapp": return channelWhatsapp(ctx, rest);
    default:
      return {
        kind: "error",
        text: "Usage: /config channel <list|telegram|slack|discord|whatsapp>",
      };
  }
};

// ─── list ─────────────────────────────────────────────────────────

async function channelList(ctx: Parameters<CoreCommandHandler>[0]): Promise<CoreCommandResult> {
  log.cli.debug("config.channel.list: entry");
  const cfg = ctx.getOwlGateway().getConfig();
  const lines: string[] = ["Configured channels:"];

  lines.push(`  telegram   ${cfg.telegram ? `token=${maskKey(cfg.telegram.botToken)}  users=${(cfg.telegram.allowedUserIds ?? []).join(",") || "(all)"}` : "(not configured)"}`);

  if (cfg.slack) {
    lines.push(`  slack      bot=${maskKey(cfg.slack.botToken)}  app=${maskKey(cfg.slack.appToken)}`);
  } else {
    lines.push("  slack      (not configured)");
  }

  if (cfg.discord) {
    lines.push(`  discord    token=${maskKey(cfg.discord.botToken)}  dm-policy=${cfg.discord.dmPolicy ?? "pairing"}`);
  } else {
    lines.push("  discord    (not configured)");
  }

  if (cfg.whatsapp) {
    lines.push(`  whatsapp   enabled=${cfg.whatsapp.enabled ?? false}  dm-policy=${cfg.whatsapp.dmPolicy ?? "pairing"}`);
  } else {
    lines.push("  whatsapp   (not configured)");
  }

  log.cli.debug("config.channel.list: exit");
  return { kind: "system-message", text: lines.join("\n") };
}

// ─── telegram ─────────────────────────────────────────────────────

async function channelTelegram(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.channel.telegram: entry", { args });
  const [subverb, ...rest] = args;

  switch (subverb) {
    case "set-token": {
      const [token] = rest;
      if (!token) return { kind: "error", text: "Usage: /config channel telegram set-token <token>" };
      log.cli.debug("config.channel.telegram.set-token: step — patching botToken");
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "telegram", {
        ...cfg.telegram,
        botToken: token,
      } as { botToken: string }, { restartRequired: true });
      log.cli.debug("config.channel.telegram.set-token: exit");
      return result;
    }
    case "add-user": {
      const [idStr] = rest;
      const id = parseInt(idStr ?? "", 10);
      if (isNaN(id)) return { kind: "error", text: "Usage: /config channel telegram add-user <numeric-id>" };
      const cfg = ctx.getOwlGateway().getConfig();
      const existing = cfg.telegram?.allowedUserIds ?? [];
      if (existing.includes(id)) return { kind: "error", text: `User ${id} already in allowed list.` };
      const result = await applyPatch(ctx, "telegram", {
        ...cfg.telegram,
        allowedUserIds: [...existing, id],
      } as { botToken: string; allowedUserIds: number[] }, { restartRequired: true });
      log.cli.debug("config.channel.telegram.add-user: exit", { id });
      return result;
    }
    case "remove-user": {
      const [idStr] = rest;
      const id = parseInt(idStr ?? "", 10);
      if (isNaN(id)) return { kind: "error", text: "Usage: /config channel telegram remove-user <numeric-id>" };
      const cfg = ctx.getOwlGateway().getConfig();
      const existing = cfg.telegram?.allowedUserIds ?? [];
      if (!existing.includes(id)) return { kind: "error", text: `User ${id} not in allowed list.` };
      const result = await applyPatch(ctx, "telegram", {
        ...cfg.telegram,
        allowedUserIds: existing.filter((u) => u !== id),
      } as { botToken: string; allowedUserIds: number[] }, { restartRequired: true });
      log.cli.debug("config.channel.telegram.remove-user: exit", { id });
      return result;
    }
    default:
      return {
        kind: "error",
        text: "Usage: /config channel telegram <set-token|add-user|remove-user>",
      };
  }
}

// ─── slack ────────────────────────────────────────────────────────

async function channelSlack(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.channel.slack: entry", { args });
  const [subverb, ...rest] = args;

  switch (subverb) {
    case "set-bot-token": {
      const [token] = rest;
      if (!token) return { kind: "error", text: "Usage: /config channel slack set-bot-token <xoxb-...>" };
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "slack", {
        ...cfg.slack,
        botToken: token,
        appToken: cfg.slack?.appToken ?? "",
      }, { restartRequired: true });
      log.cli.debug("config.channel.slack.set-bot-token: exit");
      return result;
    }
    case "set-app-token": {
      const [token] = rest;
      if (!token) return { kind: "error", text: "Usage: /config channel slack set-app-token <xapp-...>" };
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "slack", {
        ...cfg.slack,
        botToken: cfg.slack?.botToken ?? "",
        appToken: token,
      }, { restartRequired: true });
      log.cli.debug("config.channel.slack.set-app-token: exit");
      return result;
    }
    default:
      return {
        kind: "error",
        text: "Usage: /config channel slack <set-bot-token|set-app-token>",
      };
  }
}

// ─── discord ──────────────────────────────────────────────────────

async function channelDiscord(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.channel.discord: entry", { args });
  const [subverb, ...rest] = args;

  switch (subverb) {
    case "set-token": {
      const [token] = rest;
      if (!token) return { kind: "error", text: "Usage: /config channel discord set-token <token>" };
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "discord", {
        ...cfg.discord,
        botToken: token,
      }, { restartRequired: true });
      log.cli.debug("config.channel.discord.set-token: exit");
      return result;
    }
    case "set-dm-policy": {
      const [policy] = rest;
      if (policy !== "open" && policy !== "pairing") {
        return { kind: "error", text: "DM policy must be 'open' or 'pairing'." };
      }
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "discord", {
        ...cfg.discord,
        botToken: cfg.discord?.botToken ?? "",
        dmPolicy: policy,
      }, { restartRequired: true });
      log.cli.debug("config.channel.discord.set-dm-policy: exit", { policy });
      return result;
    }
    default:
      return {
        kind: "error",
        text: "Usage: /config channel discord <set-token|set-dm-policy>",
      };
  }
}

// ─── whatsapp ─────────────────────────────────────────────────────

async function channelWhatsapp(
  ctx: Parameters<CoreCommandHandler>[0],
  args: string[],
): Promise<CoreCommandResult> {
  log.cli.debug("config.channel.whatsapp: entry", { args });
  const [subverb, ...rest] = args;

  switch (subverb) {
    case "enable": {
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "whatsapp", { ...cfg.whatsapp, enabled: true }, { restartRequired: true });
      log.cli.debug("config.channel.whatsapp.enable: exit");
      return result;
    }
    case "disable": {
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "whatsapp", { ...cfg.whatsapp, enabled: false }, { restartRequired: true });
      log.cli.debug("config.channel.whatsapp.disable: exit");
      return result;
    }
    case "set-dm-policy": {
      const [policy] = rest;
      if (policy !== "open" && policy !== "pairing") {
        return { kind: "error", text: "DM policy must be 'open' or 'pairing'." };
      }
      const cfg = ctx.getOwlGateway().getConfig();
      const result = await applyPatch(ctx, "whatsapp", { ...cfg.whatsapp, dmPolicy: policy }, { restartRequired: true });
      log.cli.debug("config.channel.whatsapp.set-dm-policy: exit", { policy });
      return result;
    }
    default:
      return {
        kind: "error",
        text: "Usage: /config channel whatsapp <enable|disable|set-dm-policy>",
      };
  }
}
