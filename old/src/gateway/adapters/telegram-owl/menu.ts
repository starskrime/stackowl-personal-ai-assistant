/**
 * TelegramOwlMenu
 *
 * Interactive /owl menu for Telegram.  Shows the active owl, lists all
 * available owls, and lets the user dispatch no-arg subcommands (list,
 * status, unpin) or switch owls via inline buttons.
 *
 * Callback prefix: "owl:" — see CALLBACK_PREFIX.OWL in constants.ts.
 */

import { InlineKeyboard } from "grammy";
import type { Context } from "grammy";
import { log } from "../../../logger.js";
import type { OwlGateway } from "../../core.js";

const PREFIX = "owl:";

// Max owl-name bytes in switch callback: 64 − len("owl:sw_") = 57 → use 54 for safety
const MAX_OWL_NAME_BYTES = 54;

// Truncate result text to stay within Telegram message limits
const MAX_RESULT_LEN = 3800;

// ─── TelegramOwlMenu ─────────────────────────────────────────────────────────

export class TelegramOwlMenu {
  constructor(
    private readonly gateway: OwlGateway,
    private readonly dispatchCommand: (command: string) => Promise<string>,
  ) {
    log.telegram.debug("TelegramOwlMenu: constructed");
  }

  // ─── handleCommand ──────────────────────────────────────────────────────────

  /** Called when user sends /owl with no subcommand arguments. */
  async handleCommand(ctx: Context): Promise<void> {
    log.telegram.debug("TelegramOwlMenu.handleCommand: entry");
    const { text, keyboard } = this.buildMain();
    await ctx.reply(text, { parse_mode: "HTML", reply_markup: keyboard });
    log.telegram.debug("TelegramOwlMenu.handleCommand: exit");
  }

  // ─── handleCallback ─────────────────────────────────────────────────────────

  /** Routes owl:* callback_query:data to the appropriate screen or action. */
  async handleCallback(ctx: Context, data: string): Promise<void> {
    const cmd = data.slice(PREFIX.length);
    log.telegram.debug("TelegramOwlMenu.handleCallback: entry", { cmd: cmd.slice(0, 40) });

    try {
      if (cmd === "~" || cmd === "main") {
        await this.showMain(ctx);
      } else if (cmd === "ls") {
        await this.dispatchAndShow(ctx, "/owl list", "📋 Owl List");
      } else if (cmd === "st") {
        await this.dispatchAndShow(ctx, "/owl status", "📊 Owl Status");
      } else if (cmd === "up") {
        await this.dispatchAndShow(ctx, "/owl unpin", "📤 Unpin");
      } else if (cmd === "sw") {
        await this.showSwitchScreen(ctx);
      } else if (cmd.startsWith("sw_")) {
        const name = cmd.slice(3);
        await this.dispatchAndShow(ctx, `/owl switch ${name}`, `🔀 Switch → ${name}`);
      } else if (cmd === "cl") {
        await ctx.editMessageText("🦉 Owl menu closed.").catch(() => {});
        await ctx.answerCallbackQuery().catch(() => {});
      } else {
        log.telegram.warn("TelegramOwlMenu.handleCallback: unknown cmd", { cmd: cmd.slice(0, 20) });
        await ctx.answerCallbackQuery().catch(() => {});
      }
    } catch (err) {
      log.telegram.error("TelegramOwlMenu.handleCallback: error", err as Error, { cmd: cmd.slice(0, 40) });
      await ctx.answerCallbackQuery("Something went wrong").catch(() => {});
    }

    log.telegram.debug("TelegramOwlMenu.handleCallback: exit", { cmd: cmd.slice(0, 40) });
  }

  // ─── Private: screens ────────────────────────────────────────────────────────

  private buildMain(): { text: string; keyboard: InlineKeyboard } {
    const owl      = this.gateway.getOwl();
    const registry = this.gateway.getOwlRegistry();
    const count    = registry ? registry.listOwls().length : "—";

    const text =
      `🦉 <b>Owl Management</b>\n\n` +
      `Active: ${owl.persona.emoji} <b>${owl.persona.name}</b>\n` +
      `Available: ${count} owls`;

    const keyboard = new InlineKeyboard()
      .text("📋 List",   `${PREFIX}ls`).text("📊 Status", `${PREFIX}st`).row()
      .text("🔀 Switch Owl…", `${PREFIX}sw`).row()
      .text("📤 Unpin",  `${PREFIX}up`).row()
      .text("❌ Close",  `${PREFIX}cl`);

    return { text, keyboard };
  }

  private async showMain(ctx: Context): Promise<void> {
    log.telegram.debug("TelegramOwlMenu.showMain: entry");
    const { text, keyboard } = this.buildMain();
    await ctx.editMessageText(text, { parse_mode: "HTML", reply_markup: keyboard }).catch(() => {});
    await ctx.answerCallbackQuery().catch(() => {});
    log.telegram.debug("TelegramOwlMenu.showMain: exit");
  }

  private async showSwitchScreen(ctx: Context): Promise<void> {
    log.telegram.debug("TelegramOwlMenu.showSwitchScreen: entry");

    const registry  = this.gateway.getOwlRegistry();
    const activeOwl = this.gateway.getOwl();

    if (!registry) {
      const errKb = new InlineKeyboard().text("← Back", `${PREFIX}~`);
      await ctx.editMessageText("❌ Owl registry not available.", { reply_markup: errKb }).catch(() => {});
      await ctx.answerCallbackQuery().catch(() => {});
      log.telegram.warn("TelegramOwlMenu.showSwitchScreen: no registry");
      return;
    }

    const owls     = registry.listOwls();
    const keyboard = new InlineKeyboard();

    for (const instance of owls) {
      const { name, emoji } = instance.persona;
      const isActive = name.toLowerCase() === activeOwl.persona.name.toLowerCase();
      const label    = isActive ? `${emoji} ${name} ✓` : `${emoji} ${name}`;
      const safeName = name.slice(0, MAX_OWL_NAME_BYTES);
      keyboard.text(label, `${PREFIX}sw_${safeName}`).row();
    }
    keyboard.text("← Back", `${PREFIX}~`);

    const text =
      `🔀 <b>Switch Owl</b>\n\n` +
      `<i>Active: ${activeOwl.persona.emoji} ${activeOwl.persona.name}</i>\n\n` +
      `Tap an owl to switch:`;

    await ctx.editMessageText(text, { parse_mode: "HTML", reply_markup: keyboard }).catch(() => {});
    await ctx.answerCallbackQuery().catch(() => {});
    log.telegram.debug("TelegramOwlMenu.showSwitchScreen: exit", { count: owls.length });
  }

  private async dispatchAndShow(ctx: Context, command: string, title: string): Promise<void> {
    log.telegram.debug("TelegramOwlMenu.dispatchAndShow: entry", { command });
    await ctx.answerCallbackQuery().catch(() => {});

    let output: string;
    try {
      output = await this.dispatchCommand(command);
    } catch (err) {
      log.telegram.error("TelegramOwlMenu.dispatchAndShow: dispatch failed", err as Error, { command });
      output = `Command failed. Check logs.`;
    }

    const body     = output.length > MAX_RESULT_LEN
      ? output.slice(0, MAX_RESULT_LEN) + "\n…(truncated)"
      : output;
    const text     = `<b>${title}</b>\n\n<pre>${body}</pre>`;
    const keyboard = new InlineKeyboard().text("🦉 Back to Owls", `${PREFIX}~`);

    await ctx.editMessageText(text, { parse_mode: "HTML", reply_markup: keyboard }).catch(() => {});
    log.telegram.debug("TelegramOwlMenu.dispatchAndShow: exit", { command, outputLen: output.length });
  }
}
