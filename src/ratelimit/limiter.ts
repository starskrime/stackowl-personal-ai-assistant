/**
 * StackOwl — Rate Limiter
 *
 * Sliding-window rate limiter supporting multiple keys and windows.
 * Used for per-session, per-user, and per-provider rate limiting.
 */

import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface RateLimitRule {
  /** Identifier: "session", "user", "provider:anthropic", etc. */
  name: string;
  /** Max requests in the window */
  maxRequests: number;
  /** Window size in milliseconds */
  windowMs: number;
}

export interface RateLimitResult {
  allowed: boolean;
  /** Requests remaining in the most restrictive window */
  remaining: number;
  /** If blocked, how long to wait (ms) */
  retryAfterMs?: number;
  /** Which rule blocked the request */
  rule?: string;
}

export interface RateLimitStats {
  [key: string]: {
    used: number;
    limit: number;
    windowMs: number;
    remaining: number;
  };
}

// ─── Implementation ─────────────────────────────────────────────

interface Window {
  timestamps: number[];
}

export class RateLimiter {
  private rules: RateLimitRule[];
  private windows = new Map<string, Window>();
  private cleanupInterval: NodeJS.Timeout;

  constructor(rules: RateLimitRule[]) {
    this.rules = rules;

    // Periodic cleanup of old timestamps
    this.cleanupInterval = setInterval(() => this.cleanup(), 60_000);
    this.cleanupInterval.unref?.();
  }

  /**
   * Check rate limit without consuming a request.
   * @param key - The entity to check (e.g., session ID, user ID)
   */
  check(key: string): RateLimitResult {
    const now = Date.now();
    let minRemaining = Infinity;
    let blockingRule: string | undefined;
    let maxRetryAfter = 0;

    for (const rule of this.rules) {
      const windowKey = `${rule.name}:${key}`;
      const window = this.windows.get(windowKey);

      if (!window) {
        minRemaining = Math.min(minRemaining, rule.maxRequests);
        continue;
      }

      // Count requests in the current window
      const cutoff = now - rule.windowMs;
      const recent = window.timestamps.filter((t) => t > cutoff);
      const remaining = rule.maxRequests - recent.length;

      if (remaining <= 0) {
        blockingRule = rule.name;
        // Oldest request in window determines when a slot opens
        const oldest = recent[0] ?? now;
        const retryAfter = oldest + rule.windowMs - now;
        maxRetryAfter = Math.max(maxRetryAfter, retryAfter);
      }

      minRemaining = Math.min(minRemaining, remaining);
    }

    if (blockingRule) {
      return {
        allowed: false,
        remaining: 0,
        retryAfterMs: maxRetryAfter,
        rule: blockingRule,
      };
    }

    return {
      allowed: true,
      remaining: Math.max(0, minRemaining === Infinity ? 0 : minRemaining),
    };
  }

  /**
   * Check and consume a request. Returns the result.
   */
  consume(key: string): RateLimitResult {
    const result = this.check(key);
    if (!result.allowed) {
      log.engine.debug(
        `[RateLimiter] Blocked "${key}" by rule "${result.rule}" — retry in ${result.retryAfterMs}ms`,
      );
      return result;
    }

    const now = Date.now();
    for (const rule of this.rules) {
      const windowKey = `${rule.name}:${key}`;
      let window = this.windows.get(windowKey);
      if (!window) {
        window = { timestamps: [] };
        this.windows.set(windowKey, window);
      }
      window.timestamps.push(now);
    }

    return result;
  }

  /**
   * Reset all windows for a key.
   */
  reset(key: string): void {
    for (const rule of this.rules) {
      this.windows.delete(`${rule.name}:${key}`);
    }
  }

  /**
   * Get stats for all tracked keys.
   */
  getStats(): RateLimitStats {
    const now = Date.now();
    const stats: RateLimitStats = {};

    for (const [windowKey, window] of this.windows) {
      // Find matching rule
      const rule = this.rules.find((r) => windowKey.startsWith(r.name + ":"));
      if (!rule) continue;

      const cutoff = now - rule.windowMs;
      const recent = window.timestamps.filter((t) => t > cutoff);

      stats[windowKey] = {
        used: recent.length,
        limit: rule.maxRequests,
        windowMs: rule.windowMs,
        remaining: Math.max(0, rule.maxRequests - recent.length),
      };
    }

    return stats;
  }

  /**
   * Stop the cleanup timer. Call on shutdown.
   */
  destroy(): void {
    clearInterval(this.cleanupInterval);
  }

  private cleanup(): void {
    const now = Date.now();
    const maxWindow = Math.max(...this.rules.map((r) => r.windowMs));

    for (const [key, window] of this.windows) {
      window.timestamps = window.timestamps.filter(
        (t) => now - t < maxWindow,
      );
      if (window.timestamps.length === 0) {
        this.windows.delete(key);
      }
    }
  }
}
