// src/gateway/adapters/telegram/message-processor.ts
//
// Shared core for routing a prepared text message through the OwlGateway and
// delivering the response back to the Telegram chat.  Both TelegramTextHandler
// and TelegramVoiceHandler delegate to this class after their channel-specific
// setup (de-dup guard / voice pipeline) is complete.
//
// This is the Task 7 implementation.  It intentionally keeps the gateway call
// and Telegram reply logic in one place so that text and voice paths share
// identical output behaviour.

import type { Context } from "grammy";
import { log } from "../../../logger.js";
import type { OwlGateway } from "../../core.js";
import { makeSessionId, makeMessage } from "../../core.js";

export interface TelegramMessageProcessorOptions {
  gateway: OwlGateway;
  unknownErrorFallback?: string;
}

export interface TelegramMessageProcessorHandleArgs {
  ctx: Context;
  userId: number;
  text: string;
  ackMessageId?: number;
  onStreamClaimed?: () => void;
}

export class TelegramMessageProcessor {
  private readonly gateway: OwlGateway;
  private readonly unknownErrorFallback: string;
  /** Stable channel ID used when building session IDs and GatewayMessages. */
  private static readonly CHANNEL_ID = "telegram";

  constructor(opts: TelegramMessageProcessorOptions) {
    log.telegram.debug("message-processor.constructor: entry");
    this.gateway = opts.gateway;
    this.unknownErrorFallback = opts.unknownErrorFallback ?? "❌";
    log.telegram.debug("message-processor.constructor: exit");
  }

  async handle(args: TelegramMessageProcessorHandleArgs): Promise<void> {
    const { ctx, userId, text } = args;
    log.telegram.debug("message-processor.handle: entry", { userId, textLen: text.length });

    const msg = makeMessage(TelegramMessageProcessor.CHANNEL_ID, String(userId), text);
    if (!msg) {
      log.telegram.warn("message-processor.handle: makeMessage returned null — skipping", { userId });
      return;
    }

    log.telegram.debug("message-processor.handle: decision — routing to gateway", { userId, sessionId: msg.sessionId });

    try {
      const sessionId = makeSessionId(TelegramMessageProcessor.CHANNEL_ID, String(userId));
      const response = await this.gateway.handle(msg, {
        onProgress: async (progressMsg: string) => {
          // Best-effort progress delivery; swallow failures to avoid crashing the pipeline
          await ctx.reply(progressMsg).catch((err) => {
            log.telegram.warn("message-processor.handle: onProgress reply failed", err, { userId });
          });
        },
      });

      log.telegram.debug("message-processor.handle: step — gateway responded", {
        userId,
        sessionId,
        contentLen: response.content.length,
      });

      // Deliver response in chunks that respect Telegram's 4096-char limit
      const content = response.content;
      if (content.length <= 4096) {
        await ctx.reply(content, { parse_mode: "HTML" }).catch(async () => {
          // Strip HTML and retry as plain text on parse failure
          await ctx.reply(content).catch((err) => {
            log.telegram.error("message-processor.handle: reply failed (plain fallback)", err as Error, { userId });
          });
        });
      } else {
        // Chunked delivery
        for (let offset = 0; offset < content.length; offset += 4096) {
          const chunk = content.slice(offset, offset + 4096);
          await ctx.reply(chunk, { parse_mode: "HTML" }).catch(async () => {
            await ctx.reply(chunk).catch((err) => {
              log.telegram.error("message-processor.handle: chunked reply failed", err as Error, { userId, offset });
            });
          });
        }
      }

      log.telegram.debug("message-processor.handle: exit", { userId });
    } catch (err) {
      log.telegram.error("message-processor.handle: gateway call failed", err as Error, { userId });
      await ctx.reply(this.unknownErrorFallback).catch((replyErr) => {
        log.telegram.error("message-processor.handle: error fallback reply failed", replyErr as Error, { userId });
      });
    }
  }
}
