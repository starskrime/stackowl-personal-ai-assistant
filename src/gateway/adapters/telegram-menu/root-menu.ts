/**
 * StackOwl — Telegram Unified Nav: Root Menu Controller
 *
 * Owns all nav:* callback dispatch and the persistent keyboard
 * button text interception ("🎛 Menu", "📊 Status", etc.).
 *
 * Delegates to existing menus for cfg/vc flows (they manage their own
 * message lifecycle — not edited in-place by this controller).
 */

import type { Context } from "grammy";
import type { OwlGateway } from "../../core.js";
import type { TelegramConfigMenu } from "../telegram-config/menu.js";
import type { TelegramVoiceMenu } from "../telegram-config/voice-menu.js";
import { NavStateManager } from "./nav-state.js";
import { log } from "../../../logger.js";
import {
  renderRoot,
  renderStatus,
  renderMcpList,
  renderOwlList,
  renderMemoryInfo,
  renderSkillsList,
  type ScreenContent,
} from "./screens.js";
import { McpCommandRouter } from "../../commands/mcp-router.js";
import { dispatchMemoryCommand } from "../../commands/memory-router.js";
import { saveConfig } from "../../../config/loader.js";

// ─── Keyboard button texts (must match persistent keyboard in telegram.ts) ────

const KEYBOARD_BUTTON_MAP: Record<string, string> = {
  "🎛 Menu":      "root",
  "📊 Status":    "status",
  "🦉 Owls":     "owl",
  "⚙️ Settings":  "cfg",
};

// ─── Controller ───────────────────────────────────────────────────

export class TelegramRootMenu {
  private navState = new NavStateManager();

  constructor(
    private gateway: OwlGateway,
    private configMenu: TelegramConfigMenu,
    private voiceMenu: TelegramVoiceMenu,
  ) {}

  // ─── Entry points ─────────────────────────────────────────────

  /** Handle /menu command — send a new nav message at root */
  async handleCommand(ctx: Context): Promise<void> {
    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return;

    log.telegram.debug("nav.handleCommand: entry", { userId });
    const content = renderRoot();
    try {
      const sent = await ctx.reply(content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      this.navState.open(userId, chatId, sent.message_id);
      log.telegram.debug("nav.handleCommand: exit", { messageId: sent.message_id });
    } catch (err) {
      log.telegram.error("nav.handleCommand: failed", err as Error);
    }
  }

  /**
   * Handle a nav:* callback_query.
   * Returns true if consumed.
   */
  async handleCallback(ctx: Context, data: string): Promise<boolean> {
    const userId = ctx.from?.id;
    if (!userId) return false;

    log.telegram.debug("nav.handleCallback: entry", { userId, data });

    // Ensure nav session exists (tap on stale nav message after restart)
    const chatId = ctx.callbackQuery?.message?.chat.id ?? ctx.chat?.id;
    const msgId = ctx.callbackQuery?.message?.message_id;
    if (!this.navState.get(userId) && chatId && msgId) {
      this.navState.open(userId, chatId, msgId);
    }

    try { await ctx.answerCallbackQuery(); } catch { /* expired — harmless */ }

    // ── Delegation: AI Config ──────────────────────────────────
    if (data === "nav:cfg") {
      log.telegram.debug("nav: delegating to configMenu");
      await this.configMenu.handleCommand(ctx);
      return true;
    }

    // ── Delegation: Voice ──────────────────────────────────────
    if (data === "nav:vc") {
      log.telegram.debug("nav: delegating to voiceMenu");
      await this.voiceMenu.handleCommand(ctx);
      return true;
    }

    // ── Back ───────────────────────────────────────────────────
    if (data === "nav:bk") {
      this.navState.pop(userId);
      const screen = this.navState.current(userId) ?? "root";
      await this.renderScreen(ctx, userId, screen);
      return true;
    }

    // ── Root ───────────────────────────────────────────────────
    if (data === "nav:r" || data === "nav:root") {
      // Reset stack to root
      const s = this.navState.get(userId);
      if (s) s.stack = ["root"];
      await this.renderScreen(ctx, userId, "root");
      return true;
    }

    // ── Status ─────────────────────────────────────────────────
    if (data === "nav:st") {
      this.navState.push(userId, "status");
      await this.renderScreen(ctx, userId, "status");
      return true;
    }

    // ── MCP list ───────────────────────────────────────────────
    if (data === "nav:mcp") {
      this.navState.push(userId, "mcp");
      await this.renderScreen(ctx, userId, "mcp");
      return true;
    }

    // ── MCP enable/disable/reconnect ───────────────────────────
    if (data.startsWith("nav:mcp:")) {
      await this.handleMcpAction(ctx, userId, data);
      return true;
    }

    // ── Owls list ──────────────────────────────────────────────
    if (data === "nav:owl") {
      this.navState.push(userId, "owl");
      await this.renderScreen(ctx, userId, "owl");
      return true;
    }

    // ── Owl switch ─────────────────────────────────────────────
    if (data.startsWith("nav:owl:sw:")) {
      await this.handleOwlSwitch(ctx, userId, data.slice("nav:owl:sw:".length));
      return true;
    }

    // ── Memory ─────────────────────────────────────────────────
    if (data === "nav:mem") {
      this.navState.push(userId, "memory");
      await this.renderScreen(ctx, userId, "memory");
      return true;
    }

    // ── Skills list ────────────────────────────────────────────
    if (data === "nav:sk") {
      this.navState.push(userId, "skills");
      await this.renderScreen(ctx, userId, "skills");
      return true;
    }

    // ── Skill enable/disable ───────────────────────────────────
    if (data.startsWith("nav:sk:")) {
      await this.handleSkillToggle(ctx, userId, data);
      return true;
    }

    return false;
  }

  /**
   * Intercept persistent keyboard button texts before they reach the gateway.
   * Returns true if the text was consumed.
   */
  async handleTextInput(ctx: Context, text: string): Promise<boolean> {
    const target = KEYBOARD_BUTTON_MAP[text];
    if (!target) return false;

    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return false;

    log.telegram.debug("nav.handleTextInput: keyboard button", { text, target });

    if (target === "cfg") {
      await this.configMenu.handleCommand(ctx);
      return true;
    }

    // Send a new nav panel message and navigate to target if needed
    const content = renderRoot();
    try {
      const sent = await ctx.reply(content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      this.navState.open(userId, chatId, sent.message_id);

      if (target !== "root") {
        // Simulate navigating to the target screen
        const navData = target === "status" ? "nav:st" :
                        target === "owl"    ? "nav:owl" :
                        target === "memory" ? "nav:mem" :
                        target === "skills" ? "nav:sk"  : `nav:${target}`;
        const fakeCtx = {
          ...ctx,
          callbackQuery: { message: { chat: { id: chatId }, message_id: sent.message_id } },
          answerCallbackQuery: async () => {},
          api: ctx.api,
        } as any;
        await this.handleCallback(fakeCtx, navData);
      }
    } catch (err) {
      log.telegram.error("nav.handleTextInput: failed", err as Error);
    }
    return true;
  }

  // ─── Screen renderer ──────────────────────────────────────────

  private async renderScreen(ctx: Context, userId: number, screen: string): Promise<void> {
    const content = await this.buildScreen(screen);
    const state = this.navState.get(userId);
    if (!state) return;

    try {
      await ctx.api.editMessageText(state.chatId, state.messageId, content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      log.telegram.debug("nav.renderScreen: exit", { screen, chatId: state.chatId, msgId: state.messageId });
    } catch (err) {
      const msg = (err as Error).message ?? "";
      if (!msg.includes("message is not modified")) {
        log.telegram.warn("nav.renderScreen: edit failed", err as Error);
      }
    }
  }

  private async buildScreen(screen: string): Promise<ScreenContent> {
    switch (screen) {
      case "root":
        return renderRoot();

      case "status": {
        const config = this.gateway.getConfig();
        const owl = this.gateway.getOwl();
        const sessionStore = (this.gateway as any).getSessionStore?.();
        let sessionCount = 0;
        try {
          const sessions = await sessionStore?.listAll?.();
          sessionCount = Array.isArray(sessions) ? sessions.length : 0;
        } catch { /* session count is informational — ignore errors */ }
        return renderStatus(config.defaultModel, owl.persona.emoji, owl.persona.name, sessionCount);
      }

      case "mcp": {
        const mgr = this.gateway.getMcpManager();
        const servers = mgr ? mgr.listServers().map((s: any) => ({
          name: s.name,
          connected: s.connected,
          toolCount: s.toolCount ?? 0,
        })) : [];
        return renderMcpList(servers);
      }

      case "owl": {
        const registry = (this.gateway as any).getSpecializedRegistry?.();
        if (registry) {
          const wp = (this.gateway as any).getWorkspacePath?.() ?? process.cwd();
          await registry.loadAll(wp).catch(() => {});
        }
        const owls: { name: string; emoji: string; isPinned: boolean }[] =
          registry?.list?.() ?? [];
        const currentOwl = this.gateway.getOwl().persona.name;
        return renderOwlList(owls, currentOwl);
      }

      case "memory": {
        const repo = (this.gateway as any).getMemoryRepo?.();
        let statsText = "Memory repository unavailable.";
        if (repo) {
          try {
            statsText = await dispatchMemoryCommand("stats", [], { repo });
          } catch (err) {
            log.telegram.warn("nav.buildScreen memory stats failed", err as Error);
          }
        }
        return renderMemoryInfo(statsText);
      }

      case "skills": {
        const loader = (this.gateway as any).getSkillsLoader?.();
        const registry = loader?.getRegistry?.();
        const skills = registry ? registry.listEnabled().map((s: any) => ({
          name: s.name,
          enabled: s.enabled !== false,
        })) : [];
        return renderSkillsList(skills);
      }

      default:
        return renderRoot();
    }
  }

  // ─── Action handlers ──────────────────────────────────────────

  private async handleMcpAction(ctx: Context, userId: number, data: string): Promise<void> {
    // data format: nav:mcp:{verb}:{serverName}
    const parts = data.split(":");
    const verb = parts[2];
    const serverName = parts.slice(3).join(":");

    const mcpManager = this.gateway.getMcpManager();
    const toolRegistry = this.gateway.getToolRegistry();
    if (!mcpManager || !toolRegistry) {
      try { await ctx.answerCallbackQuery({ text: "⚠️ MCP not available" }); } catch { /* expired */ }
      return;
    }

    const mcpVerb = verb === "en" ? "enable" : verb === "dis" ? "disable" : "reconnect";
    try {
      await McpCommandRouter.dispatch(mcpVerb, [serverName], {
        mcpManager,
        toolRegistry,
        config: this.gateway.getConfig(),
        basePath: (this.gateway as any).getWorkspacePath?.() ?? process.cwd(),
        saveConfig,
      });
      try { await ctx.answerCallbackQuery({ text: `✅ ${mcpVerb}: ${serverName}` }); } catch { /* expired */ }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.telegram.warn("nav.handleMcpAction: dispatch failed", err as Error);
      try { await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` }); } catch { /* expired */ }
    }

    // Refresh MCP list
    await this.renderScreen(ctx, userId, "mcp");
  }

  private async handleOwlSwitch(ctx: Context, userId: number, owlName: string): Promise<void> {
    try {
      const { dispatchOwlCommand } = await import("../../commands/owl-command.js");
      const registry = (this.gateway as any).getSpecializedRegistry?.();
      await dispatchOwlCommand("pin", [owlName], {
        registry,
        userId: String(userId),
        workspacePath: (this.gateway as any).getWorkspacePath?.() ?? process.cwd(),
        gateway: this.gateway as any,
      });
      try { await ctx.answerCallbackQuery({ text: `🦉 Switched to ${owlName}` }); } catch { /* expired */ }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.telegram.warn("nav.handleOwlSwitch: failed", err as Error);
      try { await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` }); } catch { /* expired */ }
    }

    await this.renderScreen(ctx, userId, "owl");
  }

  private async handleSkillToggle(ctx: Context, userId: number, data: string): Promise<void> {
    // data: nav:sk:en:{name} or nav:sk:dis:{name}
    const parts = data.split(":");
    const action = parts[2]; // "en" or "dis"
    const skillName = parts.slice(3).join(":");
    const enable = action === "en";

    const loader = (this.gateway as any).getSkillsLoader?.();
    const registry = loader?.getRegistry?.();
    if (!registry) {
      try { await ctx.answerCallbackQuery({ text: "⚠️ Skills registry unavailable" }); } catch { /* expired */ }
      return;
    }

    try {
      if (enable) {
        registry.enable?.(skillName);
      } else {
        registry.disable?.(skillName);
      }
      try {
        await ctx.answerCallbackQuery({ text: `${enable ? "✅ Enabled" : "⬜ Disabled"}: ${skillName}` });
      } catch { /* expired */ }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.telegram.warn("nav.handleSkillToggle: failed", err as Error);
      try { await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` }); } catch { /* expired */ }
    }

    await this.renderScreen(ctx, userId, "skills");
  }
}
