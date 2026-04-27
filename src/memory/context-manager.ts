/**
 * StackOwl — Context Manager
 *
 * Manages multi-turn conversation context within a session.
 * Accumulates messages and provides context retrieval with
 * token-aware rolling window management.
 */

import type { ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

export interface ContextWindowConfig {
  maxMessages: number;
  maxTokens: number;
  tokensPerMessage: number;
}

const DEFAULT_CONFIG: ContextWindowConfig = {
  maxMessages: 100,
  maxTokens: 12000,
  tokensPerMessage: 150,
};

export interface ContextEntry {
  messages: ChatMessage[];
  totalTokens: number;
  messageCount: number;
  oldestTimestamp?: number;
  newestTimestamp?: number;
}

export interface TruncationResult {
  wasTruncated: boolean;
  removedCount: number;
  removedTokens: number;
  truncationPoint: "none" | "messages" | "tokens";
}

export class ContextManager {
  private contexts: Map<string, ContextEntry> = new Map();
  private config: ContextWindowConfig;

  constructor(config: Partial<ContextWindowConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Get or create context for a session
   */
  getOrCreate(sessionId: string): ContextEntry {
    let ctx = this.contexts.get(sessionId);
    if (!ctx) {
      ctx = {
        messages: [],
        totalTokens: 0,
        messageCount: 0,
      };
      this.contexts.set(sessionId, ctx);
    }
    return ctx;
  }

  /**
   * Add a message to the session context
   */
  addMessage(sessionId: string, message: ChatMessage): TruncationResult {
    const ctx = this.getOrCreate(sessionId);

    const messageTokens = this.estimateTokens(message.content);
    const now = Date.now();

    ctx.messages.push(message);
    ctx.totalTokens += messageTokens;
    ctx.messageCount = ctx.messages.length;

    if (!ctx.oldestTimestamp) {
      ctx.oldestTimestamp = now;
    }
    ctx.newestTimestamp = now;

    return this.enforceLimits(sessionId);
  }

  /**
   * Add multiple messages at once (e.g., from session restore)
   */
  addMessages(sessionId: string, messages: ChatMessage[]): TruncationResult {
    let totalResult: TruncationResult = {
      wasTruncated: false,
      removedCount: 0,
      removedTokens: 0,
      truncationPoint: "none",
    };

    for (const msg of messages) {
      const result = this.addMessage(sessionId, msg);
      if (result.wasTruncated) {
        totalResult.wasTruncated = true;
        totalResult.removedCount += result.removedCount;
        totalResult.removedTokens += result.removedTokens;
        totalResult.truncationPoint = result.truncationPoint;
      }
    }

    return totalResult;
  }

  /**
   * Get all messages for a session
   */
  getMessages(sessionId: string): ChatMessage[] {
    const ctx = this.contexts.get(sessionId);
    return ctx ? [...ctx.messages] : [];
  }

  /**
   * Get message count for a session
   */
  getMessageCount(sessionId: string): number {
    const ctx = this.contexts.get(sessionId);
    return ctx?.messageCount ?? 0;
  }

  /**
   * Get estimated token count for a session
   */
  getTokenCount(sessionId: string): number {
    const ctx = this.contexts.get(sessionId);
    return ctx?.totalTokens ?? 0;
  }

  /**
   * Get recent messages within token limit
   */
  getRecentMessages(sessionId: string, maxTokens?: number): ChatMessage[] {
    const ctx = this.contexts.get(sessionId);
    if (!ctx || ctx.messages.length === 0) return [];

    const limit = maxTokens ?? this.config.maxTokens;
    let tokenCount = 0;
    const recent: ChatMessage[] = [];

    for (let i = ctx.messages.length - 1; i >= 0; i--) {
      const msg = ctx.messages[i];
      const msgTokens = this.estimateTokens(msg.content);

      if (tokenCount + msgTokens > limit) break;

      recent.unshift(msg);
      tokenCount += msgTokens;
    }

    return recent;
  }

  /**
   * Clear context for a session
   */
  clear(sessionId: string): void {
    this.contexts.delete(sessionId);
  }

  /**
   * Clear all contexts
   */
  clearAll(): void {
    this.contexts.clear();
  }

  /**
   * Check if context exists for a session
   */
  has(sessionId: string): boolean {
    return this.contexts.has(sessionId);
  }

  /**
   * Get session metadata
   */
  getSessionInfo(sessionId: string): {
    messageCount: number;
    tokenCount: number;
    oldestTimestamp?: number;
    newestTimestamp?: number;
  } | null {
    const ctx = this.contexts.get(sessionId);
    if (!ctx) return null;

    return {
      messageCount: ctx.messageCount,
      tokenCount: ctx.totalTokens,
      oldestTimestamp: ctx.oldestTimestamp,
      newestTimestamp: ctx.newestTimestamp,
    };
  }

  /**
   * Build a context string for system prompt injection
   */
  buildContextString(sessionId: string, options?: {
    maxTokens?: number;
    includeMetadata?: boolean;
  }): string {
    const messages = options?.maxTokens
      ? this.getRecentMessages(sessionId, options.maxTokens)
      : this.getMessages(sessionId);

    if (messages.length === 0) return "";

    const parts: string[] = [];

    if (options?.includeMetadata) {
      const info = this.getSessionInfo(sessionId);
      if (info) {
        const oldest = info.oldestTimestamp
          ? new Date(info.oldestTimestamp).toLocaleDateString()
          : "unknown";
        const newest = info.newestTimestamp
          ? new Date(info.newestTimestamp).toLocaleDateString()
          : "unknown";
        parts.push(`[Session context: ${messages.length} messages, ${info.tokenCount} tokens, ${oldest} - ${newest}]`);
      }
    }

    const msgLines = messages.map(
      (m) => `${m.role}: ${m.content.slice(0, 200)}${m.content.length > 200 ? "..." : ""}`,
    );
    parts.push(msgLines.join("\n"));

    return parts.join("\n\n");
  }

  /**
   * Enforce token and message limits
   */
  private enforceLimits(sessionId: string): TruncationResult {
    const ctx = this.contexts.get(sessionId);
    if (!ctx) {
      return { wasTruncated: false, removedCount: 0, removedTokens: 0, truncationPoint: "none" };
    }

    let removedCount = 0;
    let removedTokens = 0;
    let truncationPoint: TruncationResult["truncationPoint"] = "none";

    while (
      (ctx.messageCount > this.config.maxMessages ||
        ctx.totalTokens > this.config.maxTokens) &&
      ctx.messages.length > 1
    ) {
      const removed = ctx.messages.shift();
      if (removed) {
        const removedTokenCount = this.estimateTokens(removed.content);
        removedTokens += removedTokenCount;
        removedCount++;
        ctx.totalTokens -= removedTokenCount;
        ctx.messageCount = ctx.messages.length;

        if (ctx.messages.length > 0 && !ctx.oldestTimestamp) {
          ctx.oldestTimestamp = Date.now();
        }
      }
      truncationPoint = "tokens";
    }

    if (removedCount > 0) {
      log.engine.debug(
        `[ContextManager] Truncated ${removedCount} messages (${removedTokens} tokens) for session ${sessionId}`,
      );
    }

    return {
      wasTruncated: removedCount > 0,
      removedCount,
      removedTokens,
      truncationPoint,
    };
  }

  /**
   * Estimate tokens for text (rough approximation)
   */
  private estimateTokens(text: string): number {
    return Math.ceil(text.length / 3.8);
  }
}
