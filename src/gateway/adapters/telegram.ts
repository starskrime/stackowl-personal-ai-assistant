/**
 * StackOwl — Telegram Channel Adapter
 *
 * Transport layer only. All business logic lives in OwlGateway.
 * This adapter's responsibilities:
 *   - Connect to Telegram via grammY
 *   - Normalize incoming messages to GatewayMessage
 *   - Provide GatewayCallbacks (progress, file sending, dep install prompts)
 *   - Format GatewayResponse for Telegram (MarkdownV2, chunking, photos)
 *   - Deliver proactive messages from the gateway
 *   - Run the ProactivePinger
 */

import { Bot, InputFile, type Context } from "grammy";
import { autoRetry } from "@grammyjs/auto-retry";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, extname } from "node:path";
import { ProactivePinger } from "../../heartbeat/proactive.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessageId, OwlGateway } from "../core.js";
import type { StreamEvent } from "../../providers/base.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";
import { convertTables } from "../formatters/table-converter.js";
import { TelegramConfigMenu } from "./telegram-config/menu.js";
import { TelegramVoiceMenu } from "./telegram-config/voice-menu.js";
import { saveConfig } from "../../config/loader.js";
import { OggConverter } from "../../voice/ogg-converter.js";
import { WhisperSTT } from "../../voice/stt.js";

// ─── Config ──────────────────────────────────────────────────────

export interface TelegramAdapterConfig {
  botToken: string;
  allowedUserIds?: number[];
  /** Path to persist known chat IDs across restarts */
  chatIdsPath?: string;
}

// ─── Pending state per user ───────────────────────────────────────

interface UserState {
  /** Resolver waiting for y/n on npm install */
  pendingInstallResolve?: (approved: boolean) => void;
}

// ─── Adapter ─────────────────────────────────────────────────────

export class TelegramAdapter implements ChannelAdapter {
  readonly id = "telegram";
  readonly name = "Telegram";

  private bot: Bot;
  private pinger: ProactivePinger | null = null;
  private _backgroundWorker: import("../../agent/background-worker.js").BackgroundWorker | null = null;
  private activeChatIds: Set<number> = new Set();
  private userState: Map<number, UserState> = new Map();
  private chatIdsPath: string;
  private processedUpdates = new Map<string, number>();
  private updateCleanupInterval: ReturnType<typeof setInterval> | null = null;
  private userToChatId: Map<string, number> = new Map();
  /** Interactive /config menu controller (public for web form token delegation) */
  public configMenu: TelegramConfigMenu;
  /** Interactive /voice menu controller */
  private voiceMenu: TelegramVoiceMenu;
  /** Whisper STT engine for voice message transcription */
  private stt: WhisperSTT;

  constructor(
    private gateway: OwlGateway,
    private config: TelegramAdapterConfig,
  ) {
    if (!config.botToken?.trim()) {
      throw new Error("[TelegramAdapter] Bot token is required.");
    }
    this.bot = new Bot(config.botToken);
    this.bot.api.config.use(
      autoRetry({ maxRetryAttempts: 3, maxDelaySeconds: 300 }),
    );
    this.chatIdsPath =
      config.chatIdsPath ??
      join(process.cwd(), "workspace", "known_chat_ids.json");

    // ── Config menu (interactive /config command) ────────────────
    const gwConfig = gateway.getConfig();
    this.configMenu = new TelegramConfigMenu(
      () => gateway.getConfig(),
      async (updated) => {
        await saveConfig(process.cwd(), updated);
        // Notify gateway of the config change so runtime picks it up
        if (typeof (gateway as any).reloadConfig === "function") {
          await (gateway as any).reloadConfig(updated);
        }
      },
      gwConfig.gateway?.port ?? 3077,
      {
        get: (name: string) => {
          try { return (gateway as any).ctx?.providerRegistry?.get(name) ?? gateway.getProvider(); }
          catch { return gateway.getProvider(); }
        },
        listProviders: () => {
          try { return (gateway as any).ctx?.providerRegistry?.listProviders() ?? [gwConfig.defaultProvider]; }
          catch { return [gwConfig.defaultProvider]; }
        },
      },
    );

    // ── Voice menu (interactive /voice command) ──────────────────
    this.voiceMenu = new TelegramVoiceMenu(
      () => gateway.getConfig(),
      async (updated) => {
        await saveConfig(process.cwd(), updated);
        if (typeof (gateway as any).reloadConfig === "function") {
          await (gateway as any).reloadConfig(updated);
        }
      },
    );

    // ── STT engine for incoming Telegram voice messages ──────────
    const voiceCfg = gateway.getConfig().voice ?? {};
    this.stt = new WhisperSTT({
      model: (voiceCfg.model as any) ?? "base.en",
      language: "en",
      removeWavAfter: true,
    });

    this.setupHandlers();
  }

  // ─── ChannelAdapter interface ─────────────────────────────────

  async sendToUser(userId: string, response: GatewayResponse): Promise<void> {
    const chatId = Number(userId);
    if (!chatId) return;
    const text = this.formatResponse(response);
    try {
      await this.sendChunked(chatId, text);
    } catch (err) {
      log.telegram.warn(
        `sendToUser failed for ${userId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  async broadcast(response: GatewayResponse): Promise<void> {
    const text = this.formatResponse(response);
    for (const chatId of this.activeChatIds) {
      try {
        await this.sendChunked(chatId, text);
      } catch (err) {
        log.telegram.error(
          `Broadcast failed for ${chatId}: ${err instanceof Error ? err.message : err}`,
        );
        this.activeChatIds.delete(chatId);
      }
    }
  }

  async start(): Promise<void> {
    log.telegram.info("Starting Telegram adapter...");
    await this.loadChatIds();

    const me = await this.bot.api.getMe();
    log.telegram.info(`Connected as @${me.username}`);
    log.telegram.info(
      `Owl: ${this.gateway.getOwl().persona.emoji} ${this.gateway.getOwl().persona.name}`,
    );

    this.updateCleanupInterval = setInterval(() => {
      const now = Date.now();
      const EXPIRY_MS = 60_000;
      for (const [id, timestamp] of this.processedUpdates) {
        if (now - timestamp > EXPIRY_MS) {
          this.processedUpdates.delete(id);
        }
      }
    }, 30_000);

    const self = this;
    await this.bot.start({
      onStart: () => {
        log.telegram.info("Bot is running. Send /start in Telegram.");
        this.startPinger(self);
      },
    });
  }

  stop(): void {
    this.pinger?.stop();
    this.bot.stop();
    if (this.updateCleanupInterval) {
      clearInterval(this.updateCleanupInterval);
    }
    log.telegram.info("Telegram adapter stopped.");
  }

  async deliverFile(
    userId: string,
    filePath: string,
    caption?: string,
  ): Promise<void> {
    const chatId = this.userToChatId.get(userId) ?? [...this.activeChatIds][0];
    if (!chatId) {
      log.telegram.warn(`deliverFile: no chatId found for user ${userId}`);
      return;
    }
    const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp"]);
    const isUrl =
      filePath.startsWith("http://") || filePath.startsWith("https://");
    const ext = extname(
      isUrl ? new URL(filePath).pathname : filePath,
    ).toLowerCase();
    const payload = isUrl ? filePath : new InputFile(filePath);
    try {
      if (IMAGE_EXTS.has(ext)) {
        await this.bot.api.sendPhoto(
          chatId,
          payload,
          caption ? { caption } : {},
        );
      } else {
        await this.bot.api.sendDocument(
          chatId,
          payload,
          caption ? { caption } : {},
        );
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("401") || msg.toLowerCase().includes("unauthorized")) {
        throw new Error(
          `Telegram bot token is invalid or expired (401). Update 'telegram.botToken' in stackowl.config.json.`,
        );
      }
      throw new Error(`Telegram file delivery failed: ${msg}`);
    }
  }

  // ─── Bot handlers ─────────────────────────────────────────────

  private setupHandlers(): void {
    const owl = this.gateway.getOwl();

    this.bot.command("start", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      await ctx.reply(
        `${owl.persona.emoji} *${this.esc(owl.persona.name)}* reporting for duty\\!\n\n` +
          `I'm your personal AI assistant\\. Talk to me naturally — I'll handle the rest\\. 🦉`,
        { parse_mode: "MarkdownV2" },
      );
    });

    const resetHandler = async (ctx: any) => {
      if (!this.isAllowed(ctx)) return;
      // endSession will handle consolidation; just clear the in-memory session
      const userId = String(ctx.from?.id ?? ctx.chat.id);
      const sessionId = makeSessionId(this.id, userId);
      await this.gateway.endSession(sessionId).catch(() => {});
      await ctx.reply("🔄 Context reset. Starting fresh.");
    };

    this.bot.command("reset", resetHandler);
    this.bot.command("clear", resetHandler);

    // ── /config — Interactive provider & model configuration ────
    this.bot.command("config", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      await this.configMenu.handleCommand(ctx);
    });

    // ── /voice — Interactive voice settings ─────────────────────
    this.bot.command("voice", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      await this.voiceMenu.handleCommand(ctx);
    });

    this.bot.command("status", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const config = this.gateway.getConfig();
      const msg =
        `🦉 *StackOwl Status*\n\n` +
        `Model: ${this.esc(config.defaultModel)}\n` +
        `Owl: ${owl.persona.emoji} ${this.esc(owl.persona.name)}\n` +
        `Channel: Telegram`;
      await ctx.reply(msg, { parse_mode: "MarkdownV2" });
    });

    this.bot.command("owls", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const registry = this.gateway.getOwlRegistry();
      let msg = `🦉 *Available Owls*\n\n`;
      for (const o of registry.listOwls()) {
        msg += `${o.persona.emoji} *${this.esc(o.persona.name)}* — ${this.esc(o.persona.type)}\n`;
      }
      await ctx.reply(msg, { parse_mode: "MarkdownV2" });
    });

    // ── /skills install — start the skill install wizard ────────
    this.bot.command("skills", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      const userId = ctx.from?.id;
      if (!userId) return;
      try {
        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: String(userId),
            sessionId: makeSessionId(this.id, String(userId)),
            text: "/skills install",
          },
          { onProgress: async () => {}, askInstall: async () => false },
        );
        await this.sendWizardResponse(ctx.chat.id, response);
      } catch (err) {
        log.telegram.error(`/skills wizard error: ${err instanceof Error ? err.message : err}`);
        await ctx.reply("Failed to start the install wizard. Try again.").catch(() => {});
      }
    });

    // ── /mcp — MCP server management ─────────────────────────────
    // Usage:
    //   /mcp                          → show status of all servers
    //   /mcp status                   → same
    //   /mcp connect <npm-package>    → dynamically connect npx-published server
    //   /mcp reconnect <server-name>  → re-establish a dropped connection
    //   /mcp disconnect <server-name> → remove server and unregister its tools
    this.bot.command("mcp", async (ctx) => {
      if (!this.isAllowed(ctx)) return;

      const mcpManager = this.gateway.getMcpManager();
      const toolRegistry = this.gateway.getToolRegistry();

      if (!mcpManager) {
        await ctx.reply(
          "⚠️ MCP manager is not available. Restart the bot to reinitialise.",
        );
        return;
      }

      const rawArgs = ctx.match?.trim() ?? "";
      const [sub, ...rest] = rawArgs.split(/\s+/);
      const arg = rest.join(" ").trim();

      // ── /mcp or /mcp status ───────────────────────────────────
      if (!sub || sub === "status") {
        const text = mcpManager.formatStatus();
        await ctx.reply(text, { parse_mode: "HTML" });
        return;
      }

      // ── /mcp connect <npm-package> [args…] ────────────────────
      if (sub === "connect") {
        if (!arg) {
          await ctx.reply(
            "Usage: <code>/mcp connect &lt;npm-package&gt; [arg1 arg2 …]</code>\n" +
              "Example: <code>/mcp connect @modelcontextprotocol/server-filesystem ~/Desktop</code>",
            { parse_mode: "HTML" },
          );
          return;
        }
        const [pkg, ...pkgArgs] = arg.split(/\s+/);
        const statusMsg = await ctx.reply(
          `🔌 Connecting to <code>${pkg}</code>…`,
          { parse_mode: "HTML" },
        );
        try {
          if (!toolRegistry) throw new Error("Tool registry not available.");
          const count = await mcpManager.connectNpx(pkg!, toolRegistry, pkgArgs);
          await ctx.api
            .editMessageText(
              ctx.chat.id,
              statusMsg.message_id,
              `✅ Connected <b>${pkg}</b> — ${count} tool(s) registered.`,
              { parse_mode: "HTML" },
            )
            .catch(() => {});
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await ctx.api
            .editMessageText(
              ctx.chat.id,
              statusMsg.message_id,
              `❌ Failed to connect <code>${pkg}</code>:\n<code>${msg}</code>`,
              { parse_mode: "HTML" },
            )
            .catch(() => {});
        }
        return;
      }

      // ── /mcp reconnect <server-name> ──────────────────────────
      if (sub === "reconnect") {
        if (!arg) {
          await ctx.reply(
            "Usage: <code>/mcp reconnect &lt;server-name&gt;</code>",
            { parse_mode: "HTML" },
          );
          return;
        }
        const statusMsg = await ctx.reply(
          `🔄 Reconnecting <code>${arg}</code>…`,
          { parse_mode: "HTML" },
        );
        try {
          if (!toolRegistry) throw new Error("Tool registry not available.");
          const count = await mcpManager.reconnect(arg, toolRegistry);
          await ctx.api
            .editMessageText(
              ctx.chat.id,
              statusMsg.message_id,
              `✅ Reconnected <b>${arg}</b> — ${count} tool(s) available.`,
              { parse_mode: "HTML" },
            )
            .catch(() => {});
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await ctx.api
            .editMessageText(
              ctx.chat.id,
              statusMsg.message_id,
              `❌ Reconnect failed for <code>${arg}</code>:\n<code>${msg}</code>`,
              { parse_mode: "HTML" },
            )
            .catch(() => {});
        }
        return;
      }

      // ── /mcp disconnect <server-name> ─────────────────────────
      if (sub === "disconnect") {
        if (!arg) {
          await ctx.reply(
            "Usage: <code>/mcp disconnect &lt;server-name&gt;</code>",
            { parse_mode: "HTML" },
          );
          return;
        }
        try {
          if (!toolRegistry) throw new Error("Tool registry not available.");
          mcpManager.disconnect(arg, toolRegistry);
          await ctx.reply(
            `🔌 <b>${arg}</b> disconnected and its tools unregistered.`,
            { parse_mode: "HTML" },
          );
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await ctx.reply(
            `❌ Disconnect failed:\n<code>${msg}</code>`,
            { parse_mode: "HTML" },
          );
        }
        return;
      }

      // Unknown sub-command
      await ctx.reply(
        `❓ Unknown sub-command <code>${sub}</code>.\n\n` +
          `Available: <code>status</code> · <code>connect</code> · <code>reconnect</code> · <code>disconnect</code>`,
        { parse_mode: "HTML" },
      );
    });

    this.bot.on("message:text", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const userId = ctx.from?.id;
      if (!userId) return;

      const text = ctx.message.text;
      if (!text || text.startsWith("/")) return;

      this.trackChat(ctx.chat.id);
      this.userToChatId.set(String(userId), ctx.chat.id);

      // ─── Config menu — pending input (URL / API key) ──────
      // Must intercept BEFORE dedup so the delete-immediately can fire
      // before the message is recorded in processedUpdates.
      const configConsumed = await this.configMenu.handleTextInput(ctx, text);
      if (configConsumed) return;
      // ─────────────────────────────────────────────────────

      // Deduplicate Telegram retries — set key IMMEDIATELY before processing starts,
      // so that retries arriving while the first attempt is still streaming are blocked.
      const msgKey = `${ctx.chat.id}|${ctx.msg.message_id}`;
      if (this.processedUpdates.has(msgKey)) {
        log.telegram.info(`Skipping duplicate message ${msgKey}`);
        return;
      }
      this.processedUpdates.set(msgKey, Date.now());

      // ─── Pending npm install approval ────────────────────
      const state = this.getUserState(userId);
      if (state.pendingInstallResolve) {
        const resolve = state.pendingInstallResolve;
        state.pendingInstallResolve = undefined;
        const answer = text.trim().toLowerCase();
        resolve(answer === "yes" || answer === "y");
        return;
      }
      // ─────────────────────────────────────────────────────

      await ctx.api.sendChatAction(ctx.chat.id, "typing");

      // Reset heartbeat suppression — user is active
      this.pinger?.notifyUserActivity();
      this.gateway.getCognitiveLoop()?.notifyUserActivity();

      log.telegram.incoming(`user:${userId}`, text);

      // Immediate acknowledgment — lets the user know the message was received
      // before any LLM processing begins. The stream handler will edit this
      // same message with real content so no extra message is created.
      const ACK_MESSAGES = [
        "Got it, let me check...",
        "On it...",
        "Let me think about that...",
        "Working on it...",
        "Give me a sec...",
      ];
      const ackText =
        ACK_MESSAGES[Math.floor(Math.random() * ACK_MESSAGES.length)]!;
      let ackMessageId: number | undefined;
      try {
        const ackMsg = await ctx.api.sendMessage(
          ctx.chat.id,
          `<i>${this.escHtml(ackText)}</i>`,
          { parse_mode: "HTML" },
        );
        ackMessageId = ackMsg.message_id;
      } catch {
        // Non-fatal — proceed without ack
      }

      try {
        const owl = this.gateway.getOwl();
        const owlHeader = `${owl.persona.emoji} <b>${this.escHtml(owl.persona.name)}</b>`;
        const streamCtx = this.createStreamHandler(
          ctx,
          this.gateway.getConfig().gateway?.suppressThinkingMessages ?? true,
          ackMessageId,
        );
        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: String(userId),
            sessionId: makeSessionId(this.id, String(userId)),
            text,
          },
          {
            onProgress: async (msg: string) => {
              // Route tool status and skill usage into the stream message (edit-in-place)
              // instead of sending separate messages for each event.
              const isToolStatus =
                /^[⚙✅❌].*\b(?:Running|Tool finished|Tool failed)\b/.test(msg);
              const isSkillUsage = /\bUsing skill:\b/.test(msg);
              if (isToolStatus || isSkillUsage) {
                streamCtx.pushToolStatus(msg);
                return;
              }

              // Strip thinking/internal tags — never send these to the user,
              // regardless of suppressThinking setting.
              const stripped = this.stripInternalTags(msg);
              if (!stripped) return;

              try {
                const html = this.renderContent(stripped);
                await ctx.reply(html, { parse_mode: "HTML" });
                await ctx.api.sendChatAction(ctx.chat.id, "typing");
              } catch (err) {
                log.telegram.warn(
                  `onProgress failed: ${err instanceof Error ? err.message : err}`,
                );
              }
            },
            askInstall: async (deps: string[]) => {
              await ctx.reply(
                `📦 Install npm deps: <code>${this.escHtml(deps.join(" "))}</code>\n\nReply <b>yes</b> to install or <b>no</b> to skip.`,
                { parse_mode: "HTML" },
              );
              return new Promise<boolean>((resolve) => {
                state.pendingInstallResolve = resolve;
              });
            },
            onStreamEvent: streamCtx.handler,
          },
        );

        log.telegram.outgoing(`user:${userId}`, response.content);
        log.telegram.info(
          `tools:[${response.toolsUsed.join(", ") || "none"}] ` +
            `usage:${response.usage ? `${response.usage.promptTokens}→${response.usage.completionTokens}` : "n/a"}`,
        );

        // Determine if streaming already delivered the content to the user.
        // We check streamedContent (updated live in text_delta handler) and
        // messageId (set when initial message was sent). We do NOT rely on
        // finalResponseSent from the done handler because done may fire
        // after handle() returns.
        const streamed = streamCtx.status.streamedContent;
        const msgId = streamCtx.status.messageId;

        if (msgId && streamed) {
          // Streaming delivered content — replace with fully formatted version.
          const fullHtml =
            `${owlHeader}\n\n` + this.renderContent(response.content);

          if (fullHtml.length <= 4096) {
            // Short response: edit in place (single clean message)
            try {
              await this.bot.api.editMessageText(ctx.chat.id, msgId, fullHtml, {
                parse_mode: "HTML",
              });
            } catch (editErr) {
              log.telegram.warn(
                `[Telegram] Final edit failed: ${editErr instanceof Error ? editErr.message : editErr}`,
              );
              // Delete the raw streaming message so user doesn't see both versions
              await this.bot.api
                .deleteMessage(ctx.chat.id, msgId)
                .catch(() => {});
              await this.sendChunked(ctx.chat.id, fullHtml).catch(() => {
                this.bot.api
                  .sendMessage(
                    ctx.chat.id,
                    this.stripInternalTags(response.content),
                  )
                  .catch(() => {});
              });
            }
          } else {
            // Long response (> 4096 chars): split into chunks.
            // Edit the streaming message with the first chunk, send the rest fresh.
            const chunks = this.splitMessage(fullHtml, 3800);
            try {
              await this.bot.api.editMessageText(
                ctx.chat.id,
                msgId,
                chunks[0]!,
                { parse_mode: "HTML" },
              );
            } catch {
              // Edit failed — delete streaming message and start fresh
              await this.bot.api
                .deleteMessage(ctx.chat.id, msgId)
                .catch(() => {});
              await this.bot.api
                .sendMessage(ctx.chat.id, chunks[0]!, { parse_mode: "HTML" })
                .catch(() => {});
            }
            // Send remaining chunks
            for (let i = 1; i < Math.min(chunks.length, 5); i++) {
              await new Promise((r) => setTimeout(r, 400));
              await this.bot.api
                .sendMessage(ctx.chat.id, chunks[i]!, { parse_mode: "HTML" })
                .catch(() => {});
            }
            if (chunks.length > 5) {
              await this.bot.api
                .sendMessage(
                  ctx.chat.id,
                  `<i>...${chunks.length - 5} more sections omitted...</i>`,
                  { parse_mode: "HTML" },
                )
                .catch(() => {});
            }
          }
          streamCtx.status.finalResponseSent = true;
        } else {
          // No streaming content — send the full formatted response
          const text = this.formatResponse(response);
          await this.sendChunked(ctx.chat.id, text);
          streamCtx.status.finalResponseSent = true;
        }

        // ─── Feedback buttons ─────────────────────────────────
        // Send 👍/👎 inline keyboard after each response so the user can
        // signal whether the answer was helpful. The gateway uses this to
        // boost or retire success-recipe facts in the FactStore.
        const feedbackId = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
        this.gateway.registerFeedback(feedbackId, {
          sessionId: makeSessionId(this.id, String(userId)),
          userId: String(userId),
          userMessage: text.slice(0, 150),
          assistantSummary: response.content.slice(0, 200),
          toolsUsed: response.toolsUsed ?? [],
        });
        await this.bot.api
          .sendMessage(ctx.chat.id, "Was this helpful?", {
            reply_markup: {
              inline_keyboard: [
                [
                  { text: "👍", callback_data: `fb:like:${feedbackId}` },
                  { text: "👎", callback_data: `fb:dislike:${feedbackId}` },
                ],
              ],
            },
          })
          .catch(() => {}); // Non-fatal if delivery fails

        if (response.usage) {
          await ctx.reply(
            `_${response.usage.promptTokens}→${response.usage.completionTokens} tokens_`,
            { parse_mode: "MarkdownV2" },
          );
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.telegram.error(`Error for user ${userId}: ${msg}`);
        await ctx.reply(
          "Something went wrong. Please try again or use /reset to start fresh.",
        );
      }
    });

    // ─── Telegram voice messages (OGG Opus → STT → gateway) ──────
    this.bot.on("message:voice", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      const userId = ctx.from?.id;
      if (!userId) return;

      this.trackChat(ctx.chat.id);
      this.userToChatId.set(String(userId), ctx.chat.id);

      const voice = ctx.message.voice;
      log.telegram.info(`Voice message from user:${userId}, duration: ${voice.duration}s`);

      // Show "recording" indicator while we process
      await ctx.api.sendChatAction(ctx.chat.id, "typing");

      // ── Step 1: Download OGG from Telegram ─────────────────
      let oggBuffer: Buffer;
      try {
        const fileInfo = await ctx.api.getFile(voice.file_id);
        const fileUrl = `https://api.telegram.org/file/bot${this.config.botToken}/${fileInfo.file_path}`;
        const resp = await fetch(fileUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status} downloading voice`);
        oggBuffer = Buffer.from(await resp.arrayBuffer());
      } catch (err) {
        log.telegram.error(`Voice download failed: ${(err as Error).message}`);
        await ctx.reply("❌ Could not download voice message. Please try again.");
        return;
      }

      // ── Step 2: OGG → WAV ──────────────────────────────────
      let wavPath: string;
      try {
        wavPath = await new OggConverter().convert(oggBuffer);
      } catch (err) {
        log.telegram.error(`OGG conversion failed: ${(err as Error).message}`);
        await ctx.reply("❌ Could not process audio format. Please try again.");
        return;
      }

      // ── Step 3: Transcribe with Whisper ────────────────────
      let text: string;
      try {
        const statusMsg = await ctx.reply("🎤 <i>Transcribing voice message…</i>", {
          parse_mode: "HTML",
        });
        text = await this.stt.transcribe(wavPath);
        await ctx.api.deleteMessage(ctx.chat.id, statusMsg.message_id).catch(() => {});
      } catch (err) {
        log.telegram.error(`STT failed: ${(err as Error).message}`);
        await ctx.reply("❌ Transcription failed. Make sure whisper.cpp is set up (run in voice mode first).");
        return;
      }

      if (!text.trim()) {
        await ctx.reply("🔇 <i>Could not hear anything in the voice message.</i>", {
          parse_mode: "HTML",
        });
        return;
      }

      log.telegram.incoming(`user:${userId}`, `[voice] ${text}`);

      // Echo transcript back so the user sees what was heard
      await ctx.reply(`🎤 <i>${this.escHtml(text)}</i>`, { parse_mode: "HTML" });

      // ── Step 4: Route through gateway ──────────────────────
      await ctx.api.sendChatAction(ctx.chat.id, "typing");
      this.pinger?.notifyUserActivity();
      this.gateway.getCognitiveLoop()?.notifyUserActivity();

      try {
        const owl = this.gateway.getOwl();
        const owlHeader = `${owl.persona.emoji} <b>${this.escHtml(owl.persona.name)}</b>`;
        const streamCtx = this.createStreamHandler(
          ctx,
          this.gateway.getConfig().gateway?.suppressThinkingMessages ?? true,
          undefined,
        );

        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: String(userId),
            sessionId: makeSessionId(this.id, String(userId)),
            text,
          },
          {
            onProgress: async (msg: string) => {
              const stripped = this.stripInternalTags(msg);
              if (!stripped) return;
              try {
                await ctx.reply(this.renderContent(stripped), { parse_mode: "HTML" });
                await ctx.api.sendChatAction(ctx.chat.id, "typing");
              } catch { /* non-fatal */ }
            },
            onStreamEvent: streamCtx.handler,
          },
        );

        log.telegram.outgoing(`user:${userId}`, response.content);

        const streamed = streamCtx.status.streamedContent;
        const msgId = streamCtx.status.messageId;

        if (msgId && streamed) {
          const fullHtml = `${owlHeader}\n\n` + this.renderContent(response.content);
          if (fullHtml.length <= 4096) {
            await this.bot.api.editMessageText(ctx.chat.id, msgId, fullHtml, {
              parse_mode: "HTML",
            }).catch(() => {});
          } else {
            const chunks = this.splitMessage(fullHtml, 3800);
            await this.bot.api.editMessageText(ctx.chat.id, msgId, chunks[0]!, {
              parse_mode: "HTML",
            }).catch(() => {});
            for (let i = 1; i < Math.min(chunks.length, 5); i++) {
              await new Promise((r) => setTimeout(r, 400));
              await this.bot.api.sendMessage(ctx.chat.id, chunks[i]!, { parse_mode: "HTML" }).catch(() => {});
            }
          }
        } else {
          await this.sendChunked(ctx.chat.id, this.formatResponse(response));
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.telegram.error(`Voice gateway error for user ${userId}: ${msg}`);
        await ctx.reply("Something went wrong processing your voice message. Please try again.");
      }
    });

    // ─── Callback query router ─────────────────────────────────
    // Routes cfg:* to the config menu and fb:* to the feedback handler.
    this.bot.on("callback_query:data", async (ctx) => {
      const data = ctx.callbackQuery.data ?? "";

      // ── Skills wizard callbacks (menu:* and wiz:*) ───────
      if (data.startsWith("wiz:") || data.startsWith("menu:")) {
        if (!this.isAllowed(ctx)) {
          await ctx.answerCallbackQuery({ text: "⛔ Not authorised." });
          return;
        }
        try {
          await ctx.answerCallbackQuery();
        } catch {
          /* query expired */
        }
        const userId = ctx.from?.id;
        if (!userId) return;
        try {
          const response = await this.gateway.handle(
            {
              id: makeMessageId(),
              channelId: this.id,
              userId: String(userId),
              sessionId: makeSessionId(this.id, String(userId)),
              text: data,
            },
            { onProgress: async () => {}, askInstall: async () => false },
          );
          const chatId = ctx.chat?.id ?? ctx.callbackQuery.message?.chat.id;
          if (chatId) await this.sendWizardResponse(chatId, response);
        } catch (err) {
          log.telegram.error(`Wizard callback error: ${err instanceof Error ? err.message : err}`);
        }
        return;
      }

      // ── Config menu callbacks ────────────────────────────
      if (data.startsWith("cfg:")) {
        if (!this.isAllowed(ctx)) {
          await ctx.answerCallbackQuery({ text: "⛔ Not authorised." });
          return;
        }
        await this.configMenu.handleCallback(ctx, data);
        return;
      }

      // ── Voice menu callbacks ─────────────────────────────
      if (data.startsWith("vcfg:")) {
        if (!this.isAllowed(ctx)) {
          await ctx.answerCallbackQuery({ text: "⛔ Not authorised." });
          return;
        }
        await this.voiceMenu.handleCallback(ctx, data);
        return;
      }

      if (!data.startsWith("fb:")) return;

      const parts = data.split(":");
      const signal = parts[1] as "like" | "dislike";
      const feedbackId = parts.slice(2).join(":"); // feedbackId may contain colons

      if ((signal !== "like" && signal !== "dislike") || !feedbackId) {
        try {
          await ctx.answerCallbackQuery();
        } catch {
          /* query expired — ignore */
        }
        return;
      }

      // Dismiss loading spinner on the button
      try {
        await ctx.answerCallbackQuery({
          text: signal === "like" ? "👍 Thanks!" : "👎 Got it, I'll improve.",
        });
      } catch (err) {
        // "query is too old" — Telegram already timed out the client response.
        // This is harmless; the feedback was still processed via recordFeedback().
        log.telegram.debug(
          `answerCallbackQuery expired (non-fatal): ${(err as Error).message}`,
        );
      }

      // Replace the button message with a plain confirmation
      try {
        await ctx.editMessageText(
          signal === "like" ? "👍 Helpful" : "👎 Not helpful",
        );
      } catch {
        // Message too old to edit — ignore
      }

      // Forward to gateway for learning integration
      await this.gateway.recordFeedback(feedbackId, signal).catch((err) => {
        log.telegram.warn(
          `recordFeedback failed: ${err instanceof Error ? err.message : err}`,
        );
      });
    });

    this.bot.catch((err) => {
      const msg = (err as Error).message ?? String(err);
      if (
        msg.includes("query is too old") ||
        msg.includes("query ID is invalid")
      ) {
        log.telegram.debug(
          `Telegram callback query expired (non-fatal): ${msg}`,
        );
        return;
      }
      log.telegram.error(`Bot error: ${msg}`);
    });
  }

  // ─── Proactive Pinger ─────────────────────────────────────────

  private async startPinger(self: TelegramAdapter): Promise<void> {
    const owl = self.gateway.getOwl();
    const config = self.gateway.getConfig();

    // Resolve skills directory for pattern mining + skill evolution
    const cwd = self.gateway.getCwd() ?? process.cwd();
    const skillsDir = self.gateway.getSkillsLoader()
      ? join(cwd, "skills")
      : undefined;

    // Build ProactiveJobQueue — persistent SQLite-backed job queue that
    // replaces the 8 independent setInterval timers with a single worker.
    let jobQueue: import("../../heartbeat/job-queue.js").ProactiveJobQueue | undefined;
    try {
      const { ProactiveJobQueue } = await import("../../heartbeat/job-queue.js");
      jobQueue = new ProactiveJobQueue(cwd);
    } catch (err) {
      log.engine.warn(`[Telegram] ProactiveJobQueue init failed: ${err instanceof Error ? err.message : err}`);
    }

    this.pinger = new ProactivePinger({
      provider: self.gateway.getProvider(),
      owl,
      config,
      capabilityLedger: self.gateway.getCapabilityLedger()!,
      learningEngine: self.gateway.getLearningEngine(),
      learningOrchestrator: self.gateway.getLearningOrchestrator(),
      preferenceStore: self.gateway.getPreferenceStore(),
      reflexionEngine: self.gateway.getReflexionEngine(),
      toolRegistry: self.gateway.getToolRegistry(),
      skillsRegistry: self.gateway.getSkillsLoader()?.getRegistry(),
      skillsDir,
      sessionStore: self.gateway.getSessionStore(),
      episodicMemory: self.gateway.getEpisodicMemory(),
      knowledgeCouncil: self.gateway.getKnowledgeCouncil(),
      owlRegistry: self.gateway.getOwlRegistry(),
      goalGraph: self.gateway.getGoalGraph(),
      proactiveLoop: self.gateway.getProactiveLoop(),
      eventBus: self.gateway.getEventBus(),
      jobQueue,
      userId: "default",
      sendToUser: async (message: string) => {
        await self.broadcast({
          content: message,
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          toolsUsed: [],
        });
      },
    });
    this.pinger.start();

    // Attach BackgroundWorker to pinger (Phase 2 — agentic loop)
    const db = self.gateway.getDb?.();
    const pelletStore = self.gateway.getPelletStore?.();
    const toolRegistry = self.gateway.getToolRegistry();
    if (db && pelletStore && toolRegistry && config) {
      import("../../agent/background-worker.js").then(({ BackgroundWorker }) => {
        const worker = new BackgroundWorker({
          db,
          pelletStore,
          provider: self.gateway.getProvider(),
          owl,
          toolRegistry,
          config,
          eventBus: self.gateway.getEventBus(),
          briefingTarget: undefined,
        });
        self._backgroundWorker = worker;
        self.pinger!.setBackgroundWorker(worker);
      }).catch(() => {});
    }
  }

  // ─── Streaming (edit-in-place) ──────────────────────────────────

  /**
   * Creates a stream handler and returns both the handler function and a
   * status object that tracks whether streaming successfully delivered content.
   *
   * `status.streamedContent` holds the text that was actually streamed to the
   * user (text_delta only — tool status messages are NOT counted). After the
   * gateway returns, the caller compares this against the final response to
   * decide whether `sendResponse` is needed.
   */
  private createStreamHandler(
    ctx: Context,
    suppressThinking: boolean,
    initialMessageId?: number,
  ): {
    handler: (event: StreamEvent) => Promise<void>;
    status: {
      streamedContent: string;
      streamedContentWithHeader: string;
      messageId: number | null;
      finalResponseSent: boolean;
    };
    /** Push a tool status line into the streaming message (edit-in-place). */
    pushToolStatus: (msg: string) => void;
    suppressThinking: boolean;
  } {
    const chatId = ctx.chat?.id;
    const status = {
      streamedContent: "",
      streamedContentWithHeader: "",
      messageId: null as number | null,
      finalResponseSent: false,
    };
    if (!chatId)
      return {
        handler: async () => {},
        status,
        pushToolStatus: () => {},
        suppressThinking,
      };

    let messageId: number | null = initialMessageId ?? null;
    if (messageId) {
      status.messageId = messageId;
    }
    let displayText = ""; // Full text shown in Telegram (includes tool status)
    let pureContent = ""; // Only text_delta content (no tool noise)
    let lastEditTime = 0;
    let pendingEdit: ReturnType<typeof setTimeout> | null = null;
    let hasToolStatus = false; // Track if we've shown tool status lines
    let contentStarted = false; // Track if actual content has started
    let editFailures = 0; // Track consecutive edit failures
    let initialMessageDelivered = false; // Track if the first message was sent
    const MAX_EDIT_FAILURES = 3;
    const THROTTLE_MS = 1000;

    // Convert a streaming chunk to HTML inline — used only for the initial
    // message creation (first few chars). All throttled edits use renderContent
    // on the full pureContent so tables/headings are always converted.
    const chunkToHtml = (raw: string): string =>
      this.escHtml(raw)
        .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
        .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<i>$1</i>")
        .replace(/`(.+?)`/g, "<code>$1</code>");

    const flushEdit = async () => {
      if (!messageId || !pureContent || editFailures >= MAX_EDIT_FAILURES)
        return;
      // Render the full accumulated content so every throttled edit shows
      // properly converted tables, headings, blockquotes — not just inline markdown.
      // This means the streaming display is always fully formatted, not just the final edit.
      const rendered = hasToolStatus
        ? displayText // tool status lines mixed in — keep as-is (already HTML)
        : this.renderContent(pureContent);
      try {
        await this.bot.api.editMessageText(chatId, messageId, rendered, {
          parse_mode: "HTML",
        });
        lastEditTime = Date.now();
        editFailures = 0;
      } catch (err) {
        editFailures++;
        if (editFailures >= MAX_EDIT_FAILURES) {
          log.telegram.warn(
            `[Telegram] Too many edit failures (${editFailures}), switching to non-streaming delivery`,
          );
        }
      }
    };

    const handler = async (event: StreamEvent) => {
      switch (event.type) {
        case "text_delta": {
          // Strip internal [DONE] signal — it's an engine marker, not user content
          let chunk = event.content.replace(/\[DONE\]/g, "");
          if (!chunk) break;

          // Always strip thinking/internal content from chunks before sending to user.
          // suppressThinking controls whether the MODEL sees thinking tags, not whether
          // the USER sees them — users should never see internal reasoning.
          chunk = this.stripInternalTags(chunk);
          if (!chunk) break;

          // Insert a separator between tool status and response content
          if (hasToolStatus && !contentStarted) {
            displayText += "\n\n";
            contentStarted = true;
          }
          // displayText is HTML — convert inline markdown as we accumulate
          displayText += chunkToHtml(chunk);
          pureContent += chunk; // pureContent stays plain text for dedup detection
          // Keep status in sync so the post-handle code can detect
          // that streaming delivered content — the done event may
          // fire after handle() returns.
          status.streamedContent = pureContent;

          if (!messageId) {
            // Send initial message
            try {
              const sent = await this.bot.api.sendMessage(
                chatId,
                displayText || "...", // already HTML
                { parse_mode: "HTML" },
              );
              messageId = sent.message_id;
              status.messageId = sent.message_id;
              status.streamedContentWithHeader = displayText;
              lastEditTime = Date.now();
              initialMessageDelivered = true;
            } catch {
              // If initial send fails, streaming will fall back to final response
            }
            return;
          }

          // Throttled edit
          const elapsed = Date.now() - lastEditTime;
          if (elapsed >= THROTTLE_MS) {
            if (pendingEdit) {
              clearTimeout(pendingEdit);
              pendingEdit = null;
            }
            await flushEdit();
          } else if (!pendingEdit) {
            pendingEdit = setTimeout(() => {
              pendingEdit = null;
              flushEdit().catch(() => {});
            }, THROTTLE_MS - elapsed);
          }
          break;
        }
        case "tool_start": {
          // Skip — tool execution status is handled by onProgress
          // via pushToolStatus to avoid duplicate lines.
          break;
        }
        case "tool_end": {
          // Skip — handled by onProgress via pushToolStatus.
          break;
        }
        case "done": {
          displayText = displayText.replace(/\[DONE\]/g, "").trimEnd();
          pureContent = pureContent.replace(/\[DONE\]/g, "").trimEnd();
          status.streamedContent = pureContent;
          status.finalResponseSent = true;

          // Cancel any pending throttled edit — the post-handle code
          // will do the authoritative final edit with the owl header.
          // Do NOT call flushEdit() here: it would overwrite the
          // header that the post-handle code adds.
          if (pendingEdit) {
            clearTimeout(pendingEdit);
            pendingEdit = null;
          }
          log.telegram.info(
            `[Telegram] done fired: initialDelivered=${initialMessageDelivered} pureLen=${pureContent.length}`,
          );
          break;
        }
      }
    };

    /**
     * Push a tool execution status line into the streaming message.
     * Called from onProgress for tool events so they appear inline
     * (edit-in-place) instead of as separate Telegram messages.
     */
    const pushToolStatus = (msg: string) => {
      // Escape and convert tool status to HTML before appending
      const html = this.escHtml(msg)
        .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
        .replace(/`(.+?)`/g, "<code>$1</code>");
      displayText += `\n${html}`;
      hasToolStatus = true;
      flushEdit().catch(() => {});

      // If no streaming message exists yet, create one
      if (!messageId) {
        this.bot.api
          .sendMessage(chatId!, displayText || "...", {
            // already HTML
            parse_mode: "HTML",
          })
          .then((sent) => {
            messageId = sent.message_id;
            status.messageId = sent.message_id;
            lastEditTime = Date.now();
          })
          .catch(() => {});
      }
    };

    return { handler, status, pushToolStatus, suppressThinking };
  }

  // ─── Response formatting ──────────────────────────────────────

  /**
   * Strip internal engine markers and LLM reasoning tags from content
   * before sending to users. These should never be visible.
   */
  private stripInternalTags(text: string): string {
    return text
      .replace(/<inline_thought>[\s\S]*?<\/inline_thought>/gi, "")
      .replace(/<think>[\s\S]*?<\/think>/gi, "")
      .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, "")
      .replace(/<scratchpad>[\s\S]*?<\/scratchpad>/gi, "")
      .replace(/<reflection>[\s\S]*?<\/reflection>/gi, "")
      .replace(/<thinking>[\s\S]*?<\/thinking>/gi, "")
      .replace(/<memo>[\s\S]*?<\/memo>/gi, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  /**
   * Prepare content for Telegram HTML rendering:
   * 1. Strip internal engine tags
   * 2. Convert any markdown tables to Telegram-friendly format
   * 3. Escape HTML special characters
   *
   * All three rendering paths (streaming final edit, non-streaming send,
   * sendToUser/broadcast) use this single method.
   */
  /**
   * Prepare content for Telegram HTML rendering — single pass:
   * 1. Strip internal engine tags
   * 2. Convert markdown tables, headings, blockquotes → plain text + markdown bold
   * 3. HTML-escape the whole result (safe — no HTML in input at this point)
   * 4. Apply markdown bold/code → HTML tags
   */
  private renderContent(text: string): string {
    const clean = this.stripInternalTags(text);
    const converted = convertTables(clean); // plain text + **bold** only, no HTML
    return this.escHtml(converted)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<i>$1</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
  }

  private formatResponse(response: GatewayResponse): string {
    // Pre-formatted HTML (agent-watch, system messages) — send as-is, no header
    if (response.preformatted) {
      return response.content;
    }
    const owlHeader = `${this.escHtml(response.owlEmoji ?? "")} <b>${this.escHtml(response.owlName)}</b>`;
    return `${owlHeader}\n\n${this.renderContent(response.content)}`;
  }

  private async sendWizardResponse(
    chatId: number,
    response: GatewayResponse,
  ): Promise<void> {
    const text = this.formatResponse(response);
    if (response.inlineKeyboard && response.inlineKeyboard.length > 0) {
      await this.bot.api.sendMessage(chatId, text, {
        parse_mode: "HTML",
        reply_markup: {
          inline_keyboard: response.inlineKeyboard.map((row) =>
            row.map((btn) => ({ text: btn.text, callback_data: btn.data })),
          ),
        },
      });
    } else {
      await this.sendChunked(chatId, text);
    }
  }

  private async sendChunked(
    chatId: number,
    html: string,
    parseMode: "HTML" | "MarkdownV2" = "HTML",
  ): Promise<void> {
    const MAX_LEN = 4096;
    const CHUNK_LEN = 3800;
    const MAX_CHUNKS = 5;

    if (html.length <= MAX_LEN) {
      await this.bot.api.sendMessage(chatId, html, { parse_mode: parseMode });
      return;
    }

    const chunks = this.splitMessage(html, CHUNK_LEN);
    for (let i = 0; i < Math.min(chunks.length, MAX_CHUNKS); i++) {
      await this.bot.api.sendMessage(chatId, chunks[i], {
        parse_mode: parseMode,
      });
      if (i < Math.min(chunks.length, MAX_CHUNKS) - 1) {
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
    if (chunks.length > MAX_CHUNKS) {
      await this.bot.api.sendMessage(
        chatId,
        `<i>...${chunks.length - MAX_CHUNKS} more chunks omitted...</i>`,
        { parse_mode: "HTML" },
      );
    }
  }

  private splitMessage(text: string, maxLen: number): string[] {
    const chunks: string[] = [];
    let remaining = text;
    while (remaining.length > 0) {
      if (remaining.length <= maxLen) {
        chunks.push(remaining);
        break;
      }
      let splitAt = remaining.lastIndexOf("\n", maxLen);
      if (splitAt === -1 || splitAt < maxLen / 2)
        splitAt = remaining.lastIndexOf(" ", maxLen);
      if (splitAt === -1 || splitAt < maxLen / 2) splitAt = maxLen;
      chunks.push(remaining.substring(0, splitAt));
      remaining = remaining.substring(splitAt).trimStart();
    }
    return chunks;
  }

  // ─── Helpers ─────────────────────────────────────────────────

  private isAllowed(ctx: Context): boolean {
    const userId = ctx.from?.id;
    if (!userId) return false;
    if (!this.config.allowedUserIds?.length) return true;
    const allowed = this.config.allowedUserIds.includes(userId);
    if (!allowed) ctx.reply("🔒 Not authorized.").catch(() => {});
    return allowed;
  }

  private getUserState(userId: number): UserState {
    if (!this.userState.has(userId)) this.userState.set(userId, {});
    return this.userState.get(userId)!;
  }

  private trackChat(chatId: number): void {
    if (!this.activeChatIds.has(chatId)) {
      this.activeChatIds.add(chatId);
      this.saveChatIds().catch(() => {});
    }
  }

  private async loadChatIds(): Promise<void> {
    if (!existsSync(this.chatIdsPath)) return;
    try {
      const ids: number[] = JSON.parse(
        await readFile(this.chatIdsPath, "utf-8"),
      );
      for (const id of ids) this.activeChatIds.add(id);
      log.telegram.info(`Loaded ${ids.length} known chat ID(s)`);
    } catch {
      /* non-fatal */
    }
  }

  private async saveChatIds(): Promise<void> {
    try {
      const dir = join(this.chatIdsPath, "..");
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await writeFile(
        this.chatIdsPath,
        JSON.stringify([...this.activeChatIds]),
        "utf-8",
      );
    } catch (err) {
      log.telegram.warn(
        `Could not persist chat IDs: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /** Escape special characters for Telegram MarkdownV2. */
  private esc(text: string): string {
    return text.replace(/([_*\[\]()~`>#+\-=|{}.!\\])/g, "\\$1");
  }

  /** Escape for Telegram HTML mode. */
  private escHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /** Returns the background worker instance (if running). */
  getBackgroundWorker() {
    return this._backgroundWorker;
  }
}
