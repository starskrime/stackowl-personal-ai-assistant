// src/gateway/adapters/telegram/voice-handler.ts
import type { Bot, Context } from "grammy";
import { log } from "../../../logger.js";
import type { OwlGateway } from "../../core.js";
import { OggConverter } from "../../../voice/ogg-converter.js";
import { TelegramMessageProcessor } from "./message-processor.js";

export interface TelegramVoiceHandlerOptions {
  gateway: OwlGateway;
  isAllowed: (ctx: Context) => boolean;
  trackChat: (chatId: number, userId: string) => void;
  stt: { transcribe(wavPath: string): Promise<string> };
  botToken: string;
  unknownErrorFallback?: string;
}

export class TelegramVoiceHandler {
  private readonly gateway: OwlGateway;
  private readonly isAllowed: (ctx: Context) => boolean;
  private readonly trackChat: (chatId: number, userId: string) => void;
  private readonly stt: TelegramVoiceHandlerOptions["stt"];
  private readonly botToken: string;
  private readonly processor: TelegramMessageProcessor;
  private readonly unknownErrorFallback: string;

  constructor(opts: TelegramVoiceHandlerOptions) {
    log.telegram.debug("voice-handler.constructor: entry");
    this.gateway = opts.gateway;
    this.isAllowed = opts.isAllowed;
    this.trackChat = opts.trackChat;
    this.stt = opts.stt;
    this.botToken = opts.botToken;
    this.unknownErrorFallback = opts.unknownErrorFallback ?? "❌";
    this.processor = new TelegramMessageProcessor({
      gateway: opts.gateway,
      unknownErrorFallback: this.unknownErrorFallback,
    });
    log.telegram.debug("voice-handler.constructor: exit");
  }

  register(bot: Bot): void {
    log.telegram.debug("voice-handler.register: entry");
    bot.on("message:voice", async (ctx) => {
      log.telegram.debug("voice-handler.handle: entry", { userId: ctx.from?.id });

      if (!this.isAllowed(ctx)) return;
      const userId = ctx.from?.id;
      if (!userId) return;

      this.trackChat(ctx.chat.id, String(userId));
      const voice = ctx.message.voice;
      log.telegram.debug("voice-handler.handle: decision — voice received", { userId, duration: voice.duration });

      await ctx.api.sendChatAction(ctx.chat.id, "typing");

      // Step 1: Download OGG
      let oggBuffer: Buffer;
      try {
        const fileInfo = await ctx.api.getFile(voice.file_id);
        const fileUrl = `https://api.telegram.org/file/bot${this.botToken}/${fileInfo.file_path}`;
        const resp = await fetch(fileUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        oggBuffer = Buffer.from(await resp.arrayBuffer());
        log.telegram.debug("voice-handler.handle: step — OGG downloaded", { userId, bytes: oggBuffer.length });
      } catch (err) {
        log.telegram.error("voice-handler.handle: OGG download failed", err as Error, { userId });
        await ctx.reply(this.unknownErrorFallback).catch(() => {});
        return;
      }

      // Step 2: OGG → WAV
      let wavPath: string;
      try {
        wavPath = await new OggConverter().convert(oggBuffer);
        log.telegram.debug("voice-handler.handle: step — OGG to WAV", { userId, wavPath });
      } catch (err) {
        log.telegram.error("voice-handler.handle: OGG conversion failed", err as Error, { userId });
        await ctx.reply(this.unknownErrorFallback).catch(() => {});
        return;
      }

      // Step 3: Transcribe
      let text: string;
      try {
        const statusMsg = await ctx.reply("🎤 <i>…</i>", { parse_mode: "HTML" });
        text = await this.stt.transcribe(wavPath);
        await ctx.api.deleteMessage(ctx.chat.id, statusMsg.message_id).catch(() => {});
        log.telegram.debug("voice-handler.handle: step — transcribed", { userId, textLen: text.length });
      } catch (err) {
        log.telegram.error("voice-handler.handle: STT failed", err as Error, { userId });
        await ctx.reply(this.unknownErrorFallback).catch(() => {});
        return;
      }

      if (!text.trim()) {
        await ctx.reply("🔇").catch(() => {});
        return;
      }

      await ctx.reply(`🎤 <i>${text}</i>`, { parse_mode: "HTML" }).catch(() => {});
      this.gateway.getCognitiveLoop?.()?.notifyUserActivity?.();

      log.telegram.debug("voice-handler.handle: step — routing to gateway", { userId });
      await this.processor.handle({ ctx, userId, text });
      log.telegram.debug("voice-handler.handle: exit", { userId });
    });
    log.telegram.debug("voice-handler.register: exit");
  }
}
