/**
 * StackOwl — Session Manager
 *
 * Extracted from gateway/core.ts. Manages session lifecycle:
 * creation, caching, saving, eviction, and topic switch detection.
 */

import type { ChatMessage } from "../../providers/base.js";
import type { Session, SessionStore } from "../../memory/store.js";
import type { GatewayMessage } from "../types.js";
import type { EventBus } from "../../events/bus.js";
import { log } from "../../logger.js";

// ─── Constants ───────────────────────────────────────────────────

const MAX_SESSION_HISTORY = 50;
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

export interface SessionCache {
  session: Session;
  lastActivity: number;
}

// ─── Topic Switch Detection ─────────────────────────────────────

const RESET_PHRASES = [
  "new topic", "start over", "forget that", "forget everything",
  "fresh start", "reset", "clear", "/new", "new task",
];
const LONE_GREETINGS = ["hi", "hello", "hey", "yo", "sup"];

// ─── Session Manager ────────────────────────────────────────────

export class SessionManager {
  private cache = new Map<string, SessionCache>();
  private evictionInterval: NodeJS.Timeout;

  constructor(
    private sessionStore: SessionStore,
    private defaultOwlName: string,
    private eventBus: EventBus | null,
  ) {
    this.evictionInterval = setInterval(() => this.evictStale(), 30 * 60 * 1000);
    this.evictionInterval.unref?.();
  }

  async getOrCreate(message: GatewayMessage): Promise<Session> {
    const cached = this.cache.get(message.sessionId);
    if (cached) {
      cached.lastActivity = Date.now();
      return cached.session;
    }

    // Try to load from persistent store
    let session = await this.sessionStore.loadSession(message.sessionId);
    if (!session) {
      session = this.sessionStore.createSession(this.defaultOwlName);
      session.id = message.sessionId;

      this.eventBus?.emit("session:created", {
        sessionId: session.id,
        owlName: this.defaultOwlName,
        channelId: message.channelId,
      });
    }

    this.cache.set(message.sessionId, {
      session,
      lastActivity: Date.now(),
    });
    return session;
  }

  async save(
    session: Session,
    userMessage: string,
    newMessages: ChatMessage[],
    _isTurn: boolean,
    assistantResponse?: string,
  ): Promise<void> {
    // Add user message
    session.messages.push({ role: "user", content: userMessage });

    // Add new messages from engine (tool calls, tool results, etc.)
    if (newMessages.length > 0) {
      session.messages.push(...newMessages);
    }

    // Add assistant response if not already in newMessages
    if (assistantResponse && !newMessages.some(m => m.role === "assistant" && m.content === assistantResponse)) {
      session.messages.push({ role: "assistant", content: assistantResponse });
    }

    // Trim to max history
    if (session.messages.length > MAX_SESSION_HISTORY) {
      session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
    }

    session.metadata.lastUpdatedAt = Date.now();
    await this.sessionStore.saveSession(session);
  }

  detectTopicSwitch(text: string, history: ChatMessage[]): string | null {
    if (history.length === 0) return null;

    const trimmed = text.trim().toLowerCase();
    const isReset = RESET_PHRASES.some(
      (p) => trimmed === p || trimmed.startsWith(p + " "),
    );
    const isLoneGreeting = LONE_GREETINGS.includes(trimmed);

    if (isReset || isLoneGreeting) {
      log.engine.info(
        `Topic switch detected (keyword: "${trimmed}"). Context will be flushed.`,
      );
      return "[SYSTEM DIRECTIVE: Context has been flushed. You are starting a fresh task.]";
    }

    return null;
  }

  getCache(): Map<string, SessionCache> {
    return this.cache;
  }

  private evictStale(): void {
    const now = Date.now();
    let evicted = 0;
    for (const [key, cached] of this.cache) {
      if (now - cached.lastActivity > SESSION_TIMEOUT_MS) {
        this.cache.delete(key);
        evicted++;

        this.eventBus?.emit("session:ended", {
          sessionId: key,
          messageCount: cached.session.messages.length,
        });
      }
    }
    if (evicted > 0) {
      log.engine.info(`[SessionManager] Evicted ${evicted} stale sessions`);
    }
  }

  destroy(): void {
    clearInterval(this.evictionInterval);
  }
}
