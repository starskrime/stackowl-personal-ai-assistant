/**
 * StackOwl — Gateway Middleware Pipeline
 *
 * Composable middleware for request/response processing.
 * Each middleware can short-circuit (before) or transform (after).
 */

import type { GatewayMessage, GatewayResponse } from "./types.js";
import type { HookPipeline } from "../plugins/hook-pipeline.js";
import { log } from "../logger.js";

export interface MiddlewareContext {
  sessionId: string;
  channelId: string;
  userId: string;
}

export interface GatewayMiddleware {
  name: string;
  /** Return a GatewayResponse to short-circuit, or null to continue */
  before?(
    message: GatewayMessage,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse | null>;
  /** Transform the response after engine processing */
  after?(
    message: GatewayMessage,
    response: GatewayResponse,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse>;
}

// ─── Rate Limit Middleware ────────────────────────────────────

interface RateLimitConfig {
  maxPerMinute: number;
  maxPerHour: number;
}

export class RateLimitMiddleware implements GatewayMiddleware {
  readonly name = "rate-limit";
  private minuteWindow: Map<string, number[]> = new Map();
  private hourWindow: Map<string, number[]> = new Map();

  constructor(private config: RateLimitConfig) {}

  async before(
    _message: GatewayMessage,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse | null> {
    const now = Date.now();
    const key = ctx.sessionId;

    // Minute window
    const minuteHits = this.getWindow(this.minuteWindow, key, now, 60_000);
    if (minuteHits.length >= this.config.maxPerMinute) {
      log.engine.warn(
        `[rate-limit] Session "${key}" exceeded ${this.config.maxPerMinute}/min`,
      );
      return {
        content:
          "You're sending messages too quickly. Please wait a moment and try again.",
        owlName: "System",
        owlEmoji: "⏱️",
        toolsUsed: [],
      };
    }

    // Hour window
    const hourHits = this.getWindow(this.hourWindow, key, now, 3_600_000);
    if (hourHits.length >= this.config.maxPerHour) {
      log.engine.warn(
        `[rate-limit] Session "${key}" exceeded ${this.config.maxPerHour}/hr`,
      );
      return {
        content:
          "You've reached the hourly message limit. Please wait a bit before sending more messages.",
        owlName: "System",
        owlEmoji: "⏱️",
        toolsUsed: [],
      };
    }

    // Record this request
    minuteHits.push(now);
    hourHits.push(now);
    return null;
  }

  private getWindow(
    store: Map<string, number[]>,
    key: string,
    now: number,
    windowMs: number,
  ): number[] {
    let hits = store.get(key) ?? [];
    hits = hits.filter((t) => now - t < windowMs);
    store.set(key, hits);
    return hits;
  }
}

// ─── Logging Middleware ───────────────────────────────────────

export class LoggingMiddleware implements GatewayMiddleware {
  readonly name = "logging";
  private startTimes: Map<string, number> = new Map();

  async before(
    message: GatewayMessage,
    ctx: MiddlewareContext,
  ): Promise<null> {
    this.startTimes.set(ctx.sessionId + message.id, Date.now());
    log.engine.info(
      `[mw:log] IN  channel=${ctx.channelId} session=${ctx.sessionId} len=${message.text.length}`,
    );
    return null;
  }

  async after(
    message: GatewayMessage,
    response: GatewayResponse,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse> {
    const start = this.startTimes.get(ctx.sessionId + message.id);
    const elapsed = start ? Date.now() - start : 0;
    this.startTimes.delete(ctx.sessionId + message.id);

    log.engine.info(
      `[mw:log] OUT channel=${ctx.channelId} session=${ctx.sessionId} ` +
        `tools=[${response.toolsUsed.join(",")}] ` +
        `tokens=${response.usage ? `${response.usage.promptTokens}→${response.usage.completionTokens}` : "n/a"} ` +
        `elapsed=${elapsed}ms`,
    );
    return response;
  }
}

// ─── Plugin Hook Middleware ──────────────────────────────────

/**
 * Bridges the plugin HookPipeline into the gateway middleware pipeline.
 * Executes beforeEngine/afterEngine hooks from all registered plugins.
 */
export class PluginHookMiddleware implements GatewayMiddleware {
  readonly name = "plugin-hooks";

  constructor(private hookPipeline: HookPipeline) {}

  async before(
    message: GatewayMessage,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse | null> {
    if (!this.hookPipeline.has("beforeEngine")) return null;

    try {
      return await this.hookPipeline.executeBefore<GatewayResponse>(
        "beforeEngine",
        message,
        ctx,
      );
    } catch (err) {
      log.engine.warn(
        `[PluginHookMiddleware] beforeEngine error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }

  async after(
    message: GatewayMessage,
    response: GatewayResponse,
    ctx: MiddlewareContext,
  ): Promise<GatewayResponse> {
    if (!this.hookPipeline.has("afterEngine")) return response;

    try {
      return await this.hookPipeline.executeAfter<GatewayResponse>(
        "afterEngine",
        response,
        message,
        ctx,
      );
    } catch (err) {
      log.engine.warn(
        `[PluginHookMiddleware] afterEngine error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return response;
    }
  }
}
