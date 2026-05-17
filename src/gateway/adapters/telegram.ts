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
import { runWithContext } from "../../infra/observability/context.js";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, extname } from "node:path";
import { ProactivePinger } from "../../heartbeat/proactive.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessage, OwlGateway } from "../core.js";
import type { ChannelAdapter, GatewayResponse } from "../types.js";
import { convertTables } from "../formatters/table-converter.js";
import { TelegramConfigMenu } from "./telegram-config/menu.js";
import { TelegramVoiceMenu } from "./telegram-config/voice-menu.js";
import { TelegramRootMenu } from "./telegram-menu/index.js";
import { saveConfig } from "../../config/loader.js";
import { TelegramProgressNotifier, type TelegramApi } from "../../progress/notifiers/telegram.js";
import { pickRandomPhrase } from "../../shared/progress.js";
import { OggConverter } from "../../voice/ogg-converter.js";
import { WhisperSTT } from "../../voice/stt.js";
import { formatToolEvent } from "../narration-formatter.js";
import type { GatewayEventBus, GatewaySystemEvent } from "../event-bus.js";
import { TelegramCommandRouter } from "./telegram/command-router.js";
import { TelegramCallbackRouter } from "./telegram/callback-router.js";
import { TelegramStreamHandler } from "./telegram/stream-handler.js";
import { SessionStore } from "./telegram/session-store.js";

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
  private _progressNotifier!: TelegramProgressNotifier;

  /** Exposes the wired ProactivePinger so other adapters can share it. */
  getPinger(): ProactivePinger | null { return this.pinger; }
  private _backgroundWorker: import("../../agent/background-worker.js").BackgroundWorker | null = null;
  private readonly activeChatIds: Set<number> = new Set();
  /** Per-user state with 48h TTL — replaces the raw Map to fix the memory leak. */
  private readonly userStateStore = new SessionStore<UserState>({ ttlMs: 48 * 60 * 60 * 1000, cleanupIntervalMs: 60 * 60 * 1000 });
  private chatIdsPath: string;
  private processedUpdates = new Map<string, number>();
  private updateCleanupInterval: ReturnType<typeof setInterval> | null = null;
  private readonly userToChatId: Map<string, number> = new Map();
  /** Command router — stored as field so start() can call updateBotMenu(). */
  private commandRouter!: TelegramCommandRouter;
  /** Interactive /config menu controller (public for web form token delegation) */
  public configMenu: TelegramConfigMenu;
  /** Interactive /voice menu controller */
  private voiceMenu: TelegramVoiceMenu;
  /** Unified nav control panel controller */
  private rootMenu: TelegramRootMenu;
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

    // Progress notifier — registered on the shared ProgressManager
    this._progressNotifier = new TelegramProgressNotifier(this.bot.api as unknown as TelegramApi);
    gateway.getProgressManager().register(this._progressNotifier);
    this.chatIdsPath =
      config.chatIdsPath ??
      join(gateway.getWorkspacePath(), "known_chat_ids.json");

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
          catch (err) { log.telegram.warn("providerRegistry.get failed, using default provider", err, { name }); return gateway.getProvider(); }
        },
        listProviders: () => {
          try { return (gateway as any).ctx?.providerRegistry?.listProviders() ?? [gwConfig.defaultProvider]; }
          catch (err) { log.telegram.warn("providerRegistry.listProviders failed, using default", err); return [gwConfig.defaultProvider]; }
        },
      },
      gateway.getProviderManager(),
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

    // ── Unified nav menu ─────────────────────────────────────────
    this.rootMenu = new TelegramRootMenu(gateway, this.configMenu, this.voiceMenu);

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

    // Bot menu is now driven by REGISTRY via TelegramCommandRouter.updateBotMenu().
    // This replaces the hardcoded setMyCommands list — any new command added to
    // REGISTRY automatically appears in Telegram's bot menu without adapter changes.
    await this.commandRouter.updateBotMenu(this.bot);

    await this.bot.api.setChatMenuButton({
      menu_button: { type: "commands" },
    }).catch(err => log.telegram.warn(`setChatMenuButton failed: ${err instanceof Error ? err.message : err}`));

    if (this.updateCleanupInterval) {
      clearInterval(this.updateCleanupInterval);
      this.updateCleanupInterval = null;
    }
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
    this.userStateStore.destroy();
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
    log.telegram.debug("deliverFile: entry", { userId, filePath: filePath.slice(0, 200), hasCaption: !!caption });

    const chatId = this.userToChatId.get(userId) ?? [...this.activeChatIds][0];
    if (!chatId) {
      log.telegram.warn(`deliverFile: no chatId found for user ${userId}`);
      throw new Error(`No active chat found for user ${userId} — file not delivered.`);
    }

    const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp"]);
    const VIDEO_EXTS = new Set([".mp4", ".mov", ".avi", ".mkv", ".webm"]);
    const isUrl = filePath.startsWith("http://") || filePath.startsWith("https://");
    const ext = extname(isUrl ? new URL(filePath).pathname : filePath).toLowerCase();
    const payload = isUrl ? filePath : new InputFile(filePath);
    const kind = IMAGE_EXTS.has(ext) ? "photo" : VIDEO_EXTS.has(ext) ? "video" : "document";

    log.telegram.debug("deliverFile: sending", { chatId, ext, kind, isUrl });

    try {
      if (kind === "photo") {
        await this.bot.api.sendPhoto(chatId, payload, caption ? { caption } : {});
      } else if (kind === "video") {
        await this.bot.api.sendVideo(chatId, payload, caption ? { caption } : {});
      } else {
        await this.bot.api.sendDocument(chatId, payload, caption ? { caption } : {});
      }
      log.telegram.debug("deliverFile: exit", { success: true, chatId, kind, ext });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.telegram.error("deliverFile: send failed", err as Error, { chatId, kind, ext, filePath: filePath.slice(0, 200) });
      if (msg.includes("401") || msg.toLowerCase().includes("unauthorized")) {
        throw new Error(
          `Telegram bot token is invalid or expired (401). Update 'telegram.botToken' in stackowl.config.json.`,
        );
      }
      throw new Error(`Telegram file delivery failed (${kind}): ${msg}`);
    }
  }

  // ─── Bot handlers ─────────────────────────────────────────────

  private setupHandlers(): void {
    log.telegram.debug("telegram.setupHandlers: entry");

    // ─── 1. Global auth middleware ──────────────────────────────
    this.bot.use(async (ctx, next) => {
      if (!this.isAllowed(ctx)) return;
      return next();
    });

    // ─── 2. Command routing via REGISTRY + special-case handlers ─
    this.commandRouter = new TelegramCommandRouter({
      gateway: this.gateway,
      specialCaseHandlers: {
        start: async (ctx) => {
          this.trackChat(ctx.chat!.id);
          const owl = this.gateway.getOwl();
          const { Keyboard } = await import("grammy");
          const persistentKeyboard = new Keyboard()
            .text("🎛 Menu").text("📊 Status")
            .row()
            .text("🦉 Owls").text("⚙️ Settings")
            .resized()
            .persistent();
          await ctx.reply(
            `${owl.persona.emoji} *${this.esc(owl.persona.name)}* reporting for duty\\!\n\n` +
              `I'm your personal AI assistant\\. Talk to me naturally — I'll handle the rest\\. 🦉\n\n` +
              `Use the buttons below or tap ☰ for all commands\\.`,
            { parse_mode: "MarkdownV2", reply_markup: persistentKeyboard },
          );
        },
        reset: async (ctx) => {
          const userId = String(ctx.from?.id ?? ctx.chat?.id);
          const sessionId = makeSessionId(this.id, userId);
          await this.gateway.endSession(sessionId).catch((err) => {
            log.telegram.warn("endSession failed", err as Error);
          });
          await ctx.reply("🔄 Context reset. Starting fresh.");
        },
        clear: async (ctx) => {
          const userId = String(ctx.from?.id ?? ctx.chat?.id);
          const sessionId = makeSessionId(this.id, userId);
          await this.gateway.endSession(sessionId).catch((err) => {
            log.telegram.warn("endSession failed", err as Error);
          });
          await ctx.reply("🔄 Context reset. Starting fresh.");
        },
        config: async (ctx) => {
          this.trackChat(ctx.chat!.id);
          const rawArgs = (typeof ctx.match === "string" ? ctx.match : ctx.match?.[0] ?? "").trim();
          if (rawArgs) {
            await this.dispatchRegistryCommand(ctx, `/config ${rawArgs}`, () => this.configMenu.handleCommand(ctx));
            return;
          }
          await this.configMenu.handleCommand(ctx);
        },
        voice: async (ctx) => {
          this.trackChat(ctx.chat!.id);
          await this.voiceMenu.handleCommand(ctx);
        },
        menu: async (ctx) => {
          this.trackChat(ctx.chat!.id);
          await this.rootMenu.handleCommand(ctx);
        },
      },
    });
    this.commandRouter.register(this.bot);

    // ─── 3. Menu interception (before main text handler) ──────────
    this.bot.on("message:text", async (ctx, next) => {
      const text = ctx.message.text;
      if (!text || text.startsWith("/")) return next();
      const configConsumed = await this.configMenu.handleTextInput(ctx, text);
      if (configConsumed) return;
      const navConsumed = await this.rootMenu.handleTextInput(ctx, text);
      if (navConsumed) return;
      return next();
    });

    // ─── 4. Main message:text handler ──────────────────────────────
    this.bot.on("message:text", async (ctx) => {
      const userId = ctx.from?.id;
      if (!userId) return;
      const text = ctx.message.text;
      if (!text || text.startsWith("/")) return;

      this.trackChat(ctx.chat.id);
      this.userToChatId.set(String(userId), ctx.chat.id);

      const msgKey = `${ctx.chat.id}|${ctx.msg.message_id}`;
      if (this.processedUpdates.has(msgKey)) {
        log.telegram.info(`Skipping duplicate message ${msgKey}`);
        return;
      }
      this.processedUpdates.set(msgKey, Date.now());

      const state = this.getUserState(userId);
      if (state.pendingInstallResolve) {
        const resolve = state.pendingInstallResolve;
        state.pendingInstallResolve = undefined;
        const answer = text.trim().toLowerCase();
        resolve(answer === "yes" || answer === "y");
        return;
      }

      this.pinger?.notifyUserActivity();
      this.gateway.getCognitiveLoop()?.notifyUserActivity();
      log.telegram.incoming(`user:${userId}`, text);

      const msg = makeMessage(this.id, String(userId), text);
      if (!msg) return;

      const turnId = makeSessionId(this.id, String(userId));
      this._progressNotifier.bindSession(turnId, ctx.chat.id);
      await this.gateway.getProgressManager().notifyStart(pickRandomPhrase(), turnId);
      const ackMessageId = this._progressNotifier.getAckMessageId(turnId);

      try {
        const owl = this.gateway.getOwl();
        const owlHeader = `${owl.persona.emoji} <b>${this.escHtml(owl.persona.name)}</b>`;
        const suppressThinking = this.gateway.getConfig().gateway?.suppressThinkingMessages ?? true;
        const streamHandler = new TelegramStreamHandler({
          chatId: ctx.chat.id,
          botApi: this.bot.api,
          suppressThinking,
          initialMessageId: ackMessageId,
          onStreamClaimed: () => this._progressNotifier.markStreamClaimed(turnId),
        });

        const response = await runWithContext({
          channelId: "telegram",
          userId: String(userId),
          sessionId: makeSessionId(this.id, String(userId)),
          messageId: msg.id,
          spanName: "channel.telegram.handle",
        }, () => this.gateway.handle(
          msg,
          {
            onProgress: async (progressMsg: string) => {
              const isToolStatus =
                /^[⚙✅❌].*\b(?:Running|Tool finished|Tool failed)\b/.test(progressMsg);
              const isSkillUsage = /\bUsing skill:\b/.test(progressMsg);
              if (isToolStatus || isSkillUsage) {
                streamHandler.pushToolStatus(progressMsg);
                return;
              }
              const stripped = this.stripInternalTags(progressMsg);
              if (!stripped) return;
              try {
                await ctx.reply(this.renderContent(stripped), { parse_mode: "HTML" });
                await ctx.api.sendChatAction(ctx.chat.id, "typing");
              } catch (err) {
                log.telegram.warn(`onProgress failed: ${err instanceof Error ? err.message : err}`);
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
            onStreamEvent: (e) => streamHandler.handle(e),
          },
        ));

        log.telegram.outgoing(`user:${userId}`, response.content);
        log.telegram.info(
          `tools:[${response.toolsUsed.join(", ") || "none"}] ` +
            `usage:${response.usage ? `${response.usage.promptTokens}→${response.usage.completionTokens}` : "n/a"}`,
        );

        const streamed = streamHandler.status.streamedContent;
        const msgId = streamHandler.status.messageId;

        if (msgId && streamed) {
          const fullHtml = `${owlHeader}\n\n` + this.renderContent(response.content);
          if (fullHtml.length <= 4096) {
            try {
              await this.bot.api.editMessageText(ctx.chat.id, msgId, fullHtml, { parse_mode: "HTML" });
            } catch (editErr) {
              log.telegram.warn(`[Telegram] Final edit failed: ${editErr instanceof Error ? editErr.message : editErr}`);
              await this.bot.api.deleteMessage(ctx.chat.id, msgId).catch(() => {});
              await this.sendChunked(ctx.chat.id, fullHtml).catch(() => {
                this.bot.api.sendMessage(ctx.chat.id, this.stripInternalTags(response.content)).catch(() => {});
              });
            }
          } else {
            const chunks = this.splitMessage(fullHtml, 3800);
            try {
              await this.bot.api.editMessageText(ctx.chat.id, msgId, chunks[0]!, { parse_mode: "HTML" });
            } catch (err) {
              log.telegram.warn("streaming message edit failed, retrying with fresh message", err);
              await this.bot.api.deleteMessage(ctx.chat.id, msgId)
                .catch((e) => { log.telegram.warn("delete streaming message failed", e); });
              await this.bot.api.sendMessage(ctx.chat.id, chunks[0]!, { parse_mode: "HTML" })
                .catch((e) => { log.telegram.warn("fallback send after edit failure failed", e); });
            }
            for (let i = 1; i < Math.min(chunks.length, 5); i++) {
              await new Promise((r) => setTimeout(r, 400));
              await this.bot.api.sendMessage(ctx.chat.id, chunks[i]!, { parse_mode: "HTML" }).catch(() => {});
            }
            if (chunks.length > 5) {
              await this.bot.api
                .sendMessage(ctx.chat.id, `<i>...${chunks.length - 5} more sections omitted...</i>`, { parse_mode: "HTML" })
                .catch(() => {});
            }
          }
          streamHandler.status.finalResponseSent = true;
        } else {
          await this.sendChunked(ctx.chat.id, this.formatResponse(response));
          streamHandler.status.finalResponseSent = true;
        }

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
              inline_keyboard: [[
                { text: "👍", callback_data: `fb:like:${feedbackId}` },
                { text: "👎", callback_data: `fb:dislike:${feedbackId}` },
              ]],
            },
          })
          .catch(() => {});

        if (response.usage) {
          await ctx.reply(
            `_${response.usage.promptTokens}→${response.usage.completionTokens} tokens_`,
            { parse_mode: "MarkdownV2" },
          );
        }
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        log.telegram.error(`Error for user ${userId}: ${errMsg}`);
        await ctx.reply("Something went wrong. Please try again or use /reset to start fresh.");
      } finally {
        await this.gateway.getProgressManager().notifyStop(turnId).catch((err) => {
          log.telegram.warn("telegram: notifyStop failed in finally", err, { turnId });
        });
      }
    });

    // ─── 5. Voice message handler ───────────────────────────────
    this.bot.on("message:voice", async (ctx) => {
      const userId = ctx.from?.id;
      if (!userId) return;

      this.trackChat(ctx.chat.id);
      this.userToChatId.set(String(userId), ctx.chat.id);

      const voice = ctx.message.voice;
      log.telegram.info(`Voice message from user:${userId}, duration: ${voice.duration}s`);
      await ctx.api.sendChatAction(ctx.chat.id, "typing");

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

      let wavPath: string;
      try {
        wavPath = await new OggConverter().convert(oggBuffer);
      } catch (err) {
        log.telegram.error(`OGG conversion failed: ${(err as Error).message}`);
        await ctx.reply("❌ Could not process audio format. Please try again.");
        return;
      }

      let voiceText: string;
      try {
        const statusMsg = await ctx.reply("🎤 <i>Transcribing voice message…</i>", { parse_mode: "HTML" });
        voiceText = await this.stt.transcribe(wavPath);
        await ctx.api.deleteMessage(ctx.chat.id, statusMsg.message_id).catch(() => {});
      } catch (err) {
        log.telegram.error(`STT failed: ${(err as Error).message}`);
        await ctx.reply("❌ Transcription failed. Make sure whisper.cpp is set up (run in voice mode first).");
        return;
      }

      if (!voiceText.trim()) {
        await ctx.reply("🔇 <i>Could not hear anything in the voice message.</i>", { parse_mode: "HTML" });
        return;
      }

      log.telegram.incoming(`user:${userId}`, `[voice] ${voiceText}`);
      await ctx.reply(`🎤 <i>${this.escHtml(voiceText)}</i>`, { parse_mode: "HTML" });

      this.pinger?.notifyUserActivity();
      this.gateway.getCognitiveLoop()?.notifyUserActivity();

      const voiceMsg = makeMessage(this.id, String(userId), voiceText);
      if (!voiceMsg) return;

      const voiceTurnId = makeSessionId(this.id, String(userId));
      this._progressNotifier.bindSession(voiceTurnId, ctx.chat.id);
      await this.gateway.getProgressManager().notifyStart(pickRandomPhrase(), voiceTurnId);
      const voiceAckMessageId = this._progressNotifier.getAckMessageId(voiceTurnId);

      try {
        const owl = this.gateway.getOwl();
        const owlHeader = `${owl.persona.emoji} <b>${this.escHtml(owl.persona.name)}</b>`;
        const suppressThinking = this.gateway.getConfig().gateway?.suppressThinkingMessages ?? true;
        const streamHandler = new TelegramStreamHandler({
          chatId: ctx.chat.id,
          botApi: this.bot.api,
          suppressThinking,
          initialMessageId: voiceAckMessageId,
          onStreamClaimed: () => this._progressNotifier.markStreamClaimed(voiceTurnId),
        });

        const response = await runWithContext({
          channelId: "telegram",
          userId: String(userId),
          sessionId: makeSessionId(this.id, String(userId)),
          messageId: voiceMsg.id,
          spanName: "channel.telegram.handle",
        }, () => this.gateway.handle(
          voiceMsg,
          {
            onProgress: async (progressMsg: string) => {
              const stripped = this.stripInternalTags(progressMsg);
              if (!stripped) return;
              try {
                await ctx.reply(this.renderContent(stripped), { parse_mode: "HTML" });
                await ctx.api.sendChatAction(ctx.chat.id, "typing");
              } catch (err) {
                log.telegram.warn("progress reply failed", err);
              }
            },
            onStreamEvent: (e) => streamHandler.handle(e),
          },
        ));

        log.telegram.outgoing(`user:${userId}`, response.content);

        const streamed = streamHandler.status.streamedContent;
        const msgId = streamHandler.status.messageId;

        if (msgId && streamed) {
          const fullHtml = `${owlHeader}\n\n` + this.renderContent(response.content);
          if (fullHtml.length <= 4096) {
            await this.bot.api.editMessageText(ctx.chat.id, msgId, fullHtml, { parse_mode: "HTML" }).catch(() => {});
          } else {
            const chunks = this.splitMessage(fullHtml, 3800);
            await this.bot.api.editMessageText(ctx.chat.id, msgId, chunks[0]!, { parse_mode: "HTML" }).catch(() => {});
            for (let i = 1; i < Math.min(chunks.length, 5); i++) {
              await new Promise((r) => setTimeout(r, 400));
              await this.bot.api.sendMessage(ctx.chat.id, chunks[i]!, { parse_mode: "HTML" }).catch(() => {});
            }
          }
        } else {
          await this.sendChunked(ctx.chat.id, this.formatResponse(response));
        }
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        log.telegram.error(`Voice gateway error for user ${userId}: ${errMsg}`);
        await ctx.reply("Something went wrong processing your voice message. Please try again.");
      } finally {
        await this.gateway.getProgressManager().notifyStop(voiceTurnId).catch((err) => {
          log.telegram.warn("telegram: notifyStop failed in finally (voice)", err, { voiceTurnId });
        });
      }
    });

    // ─── 6. Callback query router ──────────────────────────────
    const callbackRouter = new TelegramCallbackRouter({
      isAllowed: (ctx) => this.isAllowed(ctx),
      handlers: {
        onNav: async (ctx, data) => {
          await this.rootMenu.handleCallback(ctx, data);
        },
        onWizard: async (ctx, data) => {
          try { await ctx.answerCallbackQuery(); } catch (err) {
            log.telegram.warn("answerCallbackQuery failed (query likely expired)", err);
          }
          const userId = ctx.from?.id;
          if (!userId) return;
          try {
            const cbMsg = makeMessage(this.id, String(userId), data);
            if (!cbMsg) return;
            const response = await runWithContext({
              channelId: "telegram",
              userId: String(userId),
              sessionId: makeSessionId(this.id, String(userId)),
              messageId: cbMsg.id,
              spanName: "channel.telegram.handle",
            }, () => this.gateway.handle(cbMsg, { onProgress: async () => {}, askInstall: async () => false }));
            const chatId = ctx.chat?.id ?? ctx.callbackQuery?.message?.chat.id;
            if (chatId) await this.sendWizardResponse(chatId, response);
          } catch (err) {
            log.telegram.error(`Wizard callback error: ${err instanceof Error ? err.message : err}`);
          }
        },
        onConfig: async (ctx, data) => { await this.configMenu.handleCallback(ctx, data); },
        onVoice:  async (ctx, data) => { await this.voiceMenu.handleCallback(ctx, data); },
        onFeedback: async (ctx, data) => { await this.handleFeedback(ctx, data); },
      },
    });
    callbackRouter.register(this.bot);

    // ─── 7. Global error handler ────────────────────────────────
    this.bot.catch((err) => {
      const msg = (err as Error).message ?? String(err);
      if (msg.includes("query is too old") || msg.includes("query ID is invalid")) {
        log.telegram.debug(`Telegram callback query expired (non-fatal): ${msg}`);
        return;
      }
      log.telegram.error(`Bot error: ${msg}`);
    });

    log.telegram.debug("telegram.setupHandlers: exit");
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
      const { ProactiveJobQueue, migrateJobsDb } = await import("../../heartbeat/job-queue.js");
      const mainDb = self.gateway.getDb?.()?.rawDb;
      if (mainDb) {
        migrateJobsDb(cwd, mainDb);
        jobQueue = new ProactiveJobQueue(mainDb);
      } else {
        log.engine.warn("[Telegram] Main DB unavailable — falling back to standalone proactive-jobs.db");
        jobQueue = new ProactiveJobQueue(cwd);
      }
    } catch (err) {
      log.engine.warn(`[Telegram] ProactiveJobQueue init failed: ${err instanceof Error ? err.message : err}`);
    }

    this.pinger = new ProactivePinger({
      provider: self.gateway.getProvider(),
      owl,
      config,
      capabilityLedger: self.gateway.getCapabilityLedger()!,
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
      gatewayEventBus: self.gateway.gatewayEventBus,
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
      }).catch((err) => {
        log.telegram.error("background worker import failed", err as Error);
      });
    }
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

  /**
   * Route a slash-command string through the universal registry.
   * On panel fallback, calls panelFallback() if provided; otherwise replies with the fallback text.
   */
  private async dispatchRegistryCommand(
    ctx: Context,
    command: string,
    panelFallback?: () => Promise<void>,
  ): Promise<void> {
    const { dispatchCoreCommand, buildCoreCtx } = await import("../commands/core-dispatcher.js");
    const { renderForTelegram } = await import("../commands/channel-renderer.js");
    try {
      const { result, panelFallback: isPanel } = await dispatchCoreCommand(command, buildCoreCtx(this.gateway));
      if (isPanel && panelFallback) {
        await panelFallback();
        return;
      }
      const text = renderForTelegram(result);
      if (text) await ctx.reply(text, { parse_mode: "MarkdownV2" }).catch(() => ctx.reply(text));
    } catch (err) {
      log.telegram.error(`telegram.registry: dispatch failed for "${command}"`, err as Error);
      await ctx.reply("❌ Command failed\\. Check logs\\.").catch(() => {});
    }
  }

  private isAllowed(ctx: Context): boolean {
    const userId = ctx.from?.id;
    if (!userId) return false;
    if (!this.config.allowedUserIds?.length) return true;
    const allowed = this.config.allowedUserIds.includes(userId);
    if (!allowed) ctx.reply("🔒 Not authorized.").catch(() => {});
    return allowed;
  }

  private getUserState(userId: number): UserState {
    const existing = this.userStateStore.get(userId);
    if (existing) return existing;
    const state: UserState = {};
    this.userStateStore.set(userId, state);
    return state;
  }

  private async handleFeedback(ctx: Context, data: string): Promise<void> {
    log.telegram.debug("telegram.handleFeedback: entry", { data: data.slice(0, 30) });
    const parts = data.split(":");
    const signal = parts[1] as "like" | "dislike";
    const feedbackId = parts.slice(2).join(":");

    if ((signal !== "like" && signal !== "dislike") || !feedbackId) {
      try { await ctx.answerCallbackQuery(); } catch (err) {
        log.telegram.warn("answerCallbackQuery (invalid feedback) failed", err);
      }
      log.telegram.debug("telegram.handleFeedback: exit — invalid signal");
      return;
    }

    try {
      await ctx.answerCallbackQuery({ text: signal === "like" ? "👍 Thanks!" : "👎 Got it, I'll improve." });
    } catch (err) {
      log.telegram.debug(`answerCallbackQuery expired (non-fatal): ${(err as Error).message}`);
    }

    try {
      await ctx.editMessageText(signal === "like" ? "👍 Helpful" : "👎 Not helpful");
    } catch (err) {
      log.telegram.warn("feedback message edit failed (message too old)", err);
    }

    await this.gateway.recordFeedback(feedbackId, signal).catch((err) => {
      log.telegram.warn(`recordFeedback failed: ${err instanceof Error ? err.message : err}`);
    });
    log.telegram.debug("telegram.handleFeedback: exit", { signal });
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
    } catch (err) {
      log.telegram.warn("loadChatIds failed", err);
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

export interface TelegramNarrationDeps {
  send: (text: string) => Promise<void> | void;
  chatId: string;
}

/**
 * Subscribe to tool:* events on the given bus and stream narration to a Telegram chat.
 * Throttled to one message per 1.5s to avoid Telegram flood-bans.
 */
export function subscribeTelegramNarration(
  bus: GatewayEventBus,
  deps: TelegramNarrationDeps,
): void {
  const events: Array<GatewaySystemEvent["type"]> = [
    "tool:start",
    "tool:result",
    "tool:goal_advance",
    "tool:goal_blocked",
  ];
  let lastSentAt = 0;
  const minIntervalMs = 1500;
  for (const ev of events) {
    bus.on(ev as any, async (event: any) => {
      const now = Date.now();
      if (now - lastSentAt < minIntervalMs) return;
      const line = formatToolEvent(event);
      if (!line) return;
      lastSentAt = now;
      try {
        await deps.send(line);
      } catch (err) {
        log.telegram.warn(`Narration send failed: ${(err as Error).message}`);
      }
    });
  }
}
