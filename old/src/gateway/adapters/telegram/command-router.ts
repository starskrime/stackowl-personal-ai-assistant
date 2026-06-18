/**
 * TelegramCommandRouter
 *
 * Registers every non-special-case command from the universal REGISTRY on a
 * grammY Bot instance, and drives `bot.api.setMyCommands()` from the same
 * source of truth.  Special-case commands (/config, /voice, /menu …) are
 * registered by callers via `specialCaseHandlers`; this class only loops the
 * entries that have `telegramSpecialCase !== true`.
 *
 * Pattern follows the existing telegram.ts `dispatchRegistryCommand` method —
 * dynamic imports keep grammy & core-dispatcher out of cold-path modules.
 */

import { log } from "../../../logger.js";
import { REGISTRY } from "../../../cli/v2/commands/registry.js";
import type { CommandSpec } from "../../../cli/v2/commands/registry.js";
import type { OwlGateway } from "../../core.js";
import type { Bot, Context } from "grammy";
import type { ChannelCommandRouter } from "../channel-command-router.js";

// ─── Exported interfaces ───────────────────────────────────────────────────────

/**
 * Map of special-case command names (without leading slash) to their handlers.
 * These are registered before the registry loop and skip the generic dispatch.
 * Example: `{ config: (ctx) => configMenu.handleCommand(ctx) }`
 */
export interface SpecialCaseHandlers {
  [commandName: string]: (ctx: Context) => Promise<void>;
}

export interface TelegramCommandRouterOptions {
  /** OwlGateway instance — provides `buildCoreCtx` for command dispatch. */
  gateway: OwlGateway;
  /** Command registry to iterate. Defaults to the global REGISTRY. */
  registry?: CommandSpec[];
  /** Optional per-command special-case overrides registered before the loop. */
  specialCaseHandlers?: SpecialCaseHandlers;
  /**
   * Extra commands to include in setMyCommands that are not in the registry
   * (e.g. Telegram-specific commands like /voice, /menu, /reset).
   */
  additionalMenuCommands?: Array<{ command: string; description: string }>;
}

// ─── TelegramCommandRouter ────────────────────────────────────────────────────

export class TelegramCommandRouter implements ChannelCommandRouter {
  private readonly gateway: OwlGateway;
  private readonly registry: CommandSpec[];
  private readonly specialCaseHandlers: SpecialCaseHandlers;
  private readonly additionalMenuCommands: Array<{ command: string; description: string }>;

  constructor(options: TelegramCommandRouterOptions) {
    this.gateway = options.gateway;
    this.registry = options.registry ?? REGISTRY;
    this.specialCaseHandlers = options.specialCaseHandlers ?? {};
    this.additionalMenuCommands = options.additionalMenuCommands ?? [];
    log.telegram.debug("TelegramCommandRouter: constructed", {
      registrySize: this.registry.length,
      specialCases: Object.keys(this.specialCaseHandlers).length,
    });
  }

  // ─── register ───────────────────────────────────────────────────────────────

  /**
   * Register all commands on the bot.
   *
   * Order:
   *   1. Special-case handlers (custom Telegram UI: /config menu, /voice, /menu)
   *   2. reset/clear aliases (always registered, no registry entry)
   *   3. All registry entries where `telegramSpecialCase !== true`
   */
  register(bot: Bot): void {
    log.telegram.debug("TelegramCommandRouter.register: entry", {
      specialCases: Object.keys(this.specialCaseHandlers),
      registrySize: this.registry.length,
    });

    // ── Step 1: Special-case handlers ─────────────────────────────────────────
    for (const [name, handler] of Object.entries(this.specialCaseHandlers)) {
      log.telegram.debug("TelegramCommandRouter.register: registering special-case", { name });
      bot.command(name, async (ctx) => {
        try {
          await handler(ctx);
        } catch (err) {
          log.telegram.error(`TelegramCommandRouter: special-case handler "${name}" failed`, err as Error, { name });
          await ctx.reply("❌ Command failed\\. Check logs\\.").catch(() => {});
        }
      });
    }

    // ── Step 2: reset / clear ─────────────────────────────────────────────────
    log.telegram.debug("TelegramCommandRouter.register: registering reset/clear");
    bot.command(["reset", "clear"], async (ctx) => {
      log.telegram.debug("TelegramCommandRouter.register: reset/clear handler invoked");
      await ctx.reply("🔄 Context reset\\. Starting fresh\\.").catch(() => {});
    });

    // ── Step 3: Loop over registry, skip special cases ────────────────────────
    let registered = 0;
    let skipped = 0;

    for (const spec of this.registry) {
      if (spec.telegramSpecialCase) {
        log.telegram.debug("TelegramCommandRouter.register: skipping special-case entry", { name: spec.name });
        skipped++;
        continue;
      }

      // Strip leading slash for grammY (e.g. "/mcp" → "mcp")
      const cmdName = spec.name.replace(/^\//, "");

      log.telegram.debug("TelegramCommandRouter.register: registering registry entry", { name: spec.name, cmdName });

      bot.command(cmdName, async (ctx) => {
        const rawArgs = ctx.match?.trim() ?? "";
        const fullCommand = rawArgs ? `${spec.name} ${rawArgs}` : spec.name;
        log.telegram.debug("TelegramCommandRouter: dispatching registry command", { cmdName, rawArgs });
        await this.dispatchRegistryCommand(ctx, fullCommand);
      });

      registered++;
    }

    log.telegram.debug("TelegramCommandRouter.register: exit", { registered, skipped });
  }

  // ─── updateBotMenu ──────────────────────────────────────────────────────────

  /**
   * Push the visible command list to Telegram's bot menu via setMyCommands.
   *
   * Filters to commands where:
   *   - `telegramVisible !== false`  (defaults to visible)
   *   - `telegramSpecialCase !== true`
   *
   * Descriptions are taken from `telegramDescription` if set, falling back to
   * `description`.  Descriptions over 253 chars are truncated to 253 + "..."
   * to stay within Telegram's 256-char limit.
   *
   * Failures are logged as warnings and do NOT throw — a stale menu is better
   * than a broken start-up.
   */
  async updateBotMenu(bot: Bot): Promise<void> {
    log.telegram.debug("TelegramCommandRouter.updateBotMenu: entry");

    const visible = this.registry.filter(
      (spec) => spec.telegramVisible !== false,
    );

    const registryCommands = visible.map((spec) => {
      const rawDesc = spec.telegramDescription ?? spec.description;
      const description = rawDesc.length > 253 ? `${rawDesc.slice(0, 253)}...` : rawDesc;
      const command = spec.name.replace(/^\//, "");
      return { command, description };
    });

    const commands = [...registryCommands, ...this.additionalMenuCommands];

    log.telegram.debug("TelegramCommandRouter.updateBotMenu: calling setMyCommands", {
      commandCount: commands.length,
    });

    try {
      await bot.api.setMyCommands(commands);
      log.telegram.debug("TelegramCommandRouter.updateBotMenu: exit", { commandCount: commands.length });
    } catch (err) {
      log.telegram.warn("TelegramCommandRouter.updateBotMenu: setMyCommands failed (non-fatal)", err);
    }
  }

  /** ChannelCommandRouter alias — delegates to updateBotMenu. */
  async updateMenu(bot: unknown): Promise<void> {
    return this.updateBotMenu(bot as Bot);
  }

  // ─── Private: dispatchRegistryCommand ────────────────────────────────────────

  /**
   * Route a slash-command string through the universal registry dispatcher.
   * Mirrors the pattern in TelegramAdapter.dispatchRegistryCommand.
   *
   * On panel fallback (TUI-only commands), replies with an informational message.
   * On error, replies with a generic failure notice and logs the error.
   */
  private async dispatchRegistryCommand(ctx: Context, command: string): Promise<void> {
    log.telegram.debug("TelegramCommandRouter.dispatchRegistryCommand: entry", { command });

    const { dispatchCoreCommand, buildCoreCtx } = await import("../../commands/core-dispatcher.js");
    const { renderForTelegram } = await import("../../commands/channel-renderer.js");

    try {
      log.telegram.debug("TelegramCommandRouter.dispatchRegistryCommand: invoking dispatchCoreCommand", { command });
      const { result } = await dispatchCoreCommand(command, buildCoreCtx(this.gateway));

      const text = renderForTelegram(result);
      log.telegram.debug("TelegramCommandRouter.dispatchRegistryCommand: exit", {
        command,
        hasText: !!text,
        resultKind: result.kind,
      });

      if (text) {
        await ctx.reply(text, { parse_mode: "MarkdownV2" }).catch(() => ctx.reply(text));
      }
    } catch (err) {
      log.telegram.error(`TelegramCommandRouter.dispatchRegistryCommand: dispatch failed for "${command}"`, err as Error, { command });
      await ctx.reply("❌ Command failed\\. Check logs\\.").catch(() => {});
    }
  }
}
