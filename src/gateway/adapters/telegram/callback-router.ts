import type { Bot, Context } from "grammy";
import { log } from "../../../logger.js";
import { CALLBACK_PREFIX } from "./constants.js";

export interface CallbackHandlers {
  onNav:      (ctx: Context, data: string) => Promise<void>;
  onWizard:   (ctx: Context, data: string) => Promise<void>;
  onConfig:   (ctx: Context, data: string) => Promise<void>;
  onVoice:    (ctx: Context, data: string) => Promise<void>;
  onFeedback: (ctx: Context, data: string) => Promise<void>;
}

export interface TelegramCallbackRouterOptions {
  isAllowed: (ctx: Context) => boolean;
  handlers: CallbackHandlers;
}

/**
 * Routes all callback_query:data updates by prefix to the appropriate handler.
 * Unknown prefixes are silently ack'd (prevents Telegram spinner staying open).
 */
export class TelegramCallbackRouter {
  private readonly isAllowed: (ctx: Context) => boolean;
  private readonly handlers: CallbackHandlers;

  constructor(opts: TelegramCallbackRouterOptions) {
    log.telegram.debug("callback-router.constructor: entry");
    this.isAllowed = opts.isAllowed;
    this.handlers = opts.handlers;
    log.telegram.debug("callback-router.constructor: exit");
  }

  register(bot: Bot): void {
    log.telegram.debug("callback-router.register: entry");
    bot.on("callback_query:data", async (ctx) => {
      const data = ctx.callbackQuery.data;
      log.telegram.debug("callback-router.dispatch: entry", { prefix: data.split(":")[0] + ":" });

      if (!this.isAllowed(ctx)) {
        log.telegram.debug("callback-router.dispatch: denied — user not allowed");
        await ctx.answerCallbackQuery().catch(() => {});
        return;
      }

      if (data.startsWith(CALLBACK_PREFIX.NAV)) {
        await this.handlers.onNav(ctx, data);
        log.telegram.debug("callback-router.dispatch: exit — nav routed");
        return;
      }

      if (data.startsWith(CALLBACK_PREFIX.WIZ) || data.startsWith("menu:")) {
        await this.handlers.onWizard(ctx, data);
        log.telegram.debug("callback-router.dispatch: exit — wizard routed");
        return;
      }

      if (data.startsWith(CALLBACK_PREFIX.CFG)) {
        await this.handlers.onConfig(ctx, data);
        log.telegram.debug("callback-router.dispatch: exit — config routed");
        return;
      }

      if (data.startsWith(CALLBACK_PREFIX.VCFG)) {
        await this.handlers.onVoice(ctx, data);
        log.telegram.debug("callback-router.dispatch: exit — voice routed");
        return;
      }

      if (data.startsWith(CALLBACK_PREFIX.FB)) {
        await this.handlers.onFeedback(ctx, data);
        log.telegram.debug("callback-router.dispatch: exit — feedback routed");
        return;
      }

      log.telegram.warn("callback-router.dispatch: unknown prefix", { data: data.slice(0, 20) });
      await ctx.answerCallbackQuery().catch(() => {});
      log.telegram.debug("callback-router.dispatch: exit — unknown prefix silently acked");
    });
    log.telegram.debug("callback-router.register: exit");
  }
}
