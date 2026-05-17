// src/gateway/adapters/telegram/text-handler.ts
import type { Bot, Context } from "grammy";
import { log } from "../../../logger.js";
import type { OwlGateway } from "../../core.js";
import type { SessionStore } from "./session-store.js";
import { TelegramMessageProcessor } from "./message-processor.js";

interface UserState {
  pendingInstallResolve?: (approved: boolean) => void;
}

export interface TelegramTextHandlerOptions {
  gateway: OwlGateway;
  isAllowed: (ctx: Context) => boolean;
  trackChat: (chatId: number, userId: string) => void;
  sessionStore: SessionStore<UserState>;
  pinger: { notifyUserActivity(): void } | null;
  progressNotifier?: {
    bindSession(id: string, chatId: number): void;
    getAckMessageId(id: string): number | undefined;
    markStreamClaimed(id: string): void;
  };
  unknownErrorFallback?: string;
}

export class TelegramTextHandler {
  private readonly gateway: OwlGateway;
  private readonly isAllowed: (ctx: Context) => boolean;
  private readonly trackChat: (chatId: number, userId: string) => void;
  private readonly sessionStore: SessionStore<UserState>;
  private readonly pinger: TelegramTextHandlerOptions["pinger"];
  private readonly progressNotifier: TelegramTextHandlerOptions["progressNotifier"];
  private readonly processor: TelegramMessageProcessor;
  private readonly inFlight = new Set<number>();

  constructor(opts: TelegramTextHandlerOptions) {
    log.telegram.debug("text-handler.constructor: entry");
    this.gateway = opts.gateway;
    this.isAllowed = opts.isAllowed;
    this.trackChat = opts.trackChat;
    this.sessionStore = opts.sessionStore;
    this.pinger = opts.pinger;
    this.progressNotifier = opts.progressNotifier;
    this.processor = new TelegramMessageProcessor({
      gateway: opts.gateway,
      unknownErrorFallback: opts.unknownErrorFallback,
    });
    log.telegram.debug("text-handler.constructor: exit");
  }

  register(bot: Bot): void {
    log.telegram.debug("text-handler.register: entry");
    bot.on("message:text", async (ctx) => {
      log.telegram.debug("text-handler.handle: entry", { userId: ctx.from?.id });

      if (!this.isAllowed(ctx)) return;
      const userId = ctx.from?.id;
      if (!userId) return;

      const text = ctx.message.text;
      if (!text || text.startsWith("/")) return;

      this.trackChat(ctx.chat.id, String(userId));
      log.telegram.debug("text-handler.handle: decision — checking in-flight", { userId, inFlight: this.inFlight.size });

      if (this.inFlight.has(userId)) {
        log.telegram.warn("text-handler.handle: user already has message in-flight", { userId });
        await ctx.reply("⏳").catch(() => {});
        return;
      }

      this.pinger?.notifyUserActivity();
      this.gateway.getCognitiveLoop?.()?.notifyUserActivity?.();

      // Build sessionId for progress tracking
      const sessionId = `telegram:${userId}`;
      this.progressNotifier?.bindSession(sessionId, ctx.chat.id);

      this.inFlight.add(userId);
      log.telegram.debug("text-handler.handle: step — starting gateway call", { userId });
      try {
        await this.processor.handle({
          ctx,
          userId,
          text,
          ackMessageId: this.progressNotifier?.getAckMessageId(sessionId),
          onStreamClaimed: () => this.progressNotifier?.markStreamClaimed(sessionId),
        });
      } finally {
        this.inFlight.delete(userId);
        log.telegram.debug("text-handler.handle: exit", { userId });
      }
    });
    log.telegram.debug("text-handler.register: exit");
  }
}
