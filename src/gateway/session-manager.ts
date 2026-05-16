/**
 * StackOwl — SessionManager
 *
 * Owns the in-memory session cache and all session lifecycle operations
 * previously embedded in OwlGateway. Extracted to reduce core.ts surface area
 * and eliminate implicit coupling between the cache map and the session store.
 *
 * Responsibilities:
 *   - Cache lookups and TTL-based eviction
 *   - Delegation to SessionService (SQLite) when available, JSON store fallback
 *   - Cache invalidation on explicit end / eviction
 */

import type { Session } from "../memory/store.js";
import type { GatewayMessage, GatewayContext } from "./types.js";
import { log } from "../logger.js";

const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours

interface SessionCache {
  session: Session;
  lastActivity: number;
}

// ─── Public interface ────────────────────────────────────────────

export interface ISessionManager {
  getOrCreate(message: GatewayMessage): Promise<Session>;
  save(session: Session): Promise<void>;
  invalidate(sessionId: string): void;
  evictStale(): void;
  getActiveCount(): number;
  getStaleIds(now: number): string[];
  getCached(sessionId: string): { session: Session; lastActivity: number } | undefined;
  entries(): IterableIterator<[string, { session: Session; lastActivity: number }]>;
}

// ─── Implementation ──────────────────────────────────────────────

export class SessionManager implements ISessionManager {
  private readonly cache = new Map<string, SessionCache>();

  constructor(
    private readonly ctx: Pick<
      GatewayContext,
      "sessionStore" | "sessionService" | "owl"
    >,
  ) {}

  /**
   * Return the session for this message, either from cache, SQLite, or JSON
   * store. Creates a new session when none exists.
   */
  async getOrCreate(message: GatewayMessage): Promise<Session> {
    const key = message.sessionId;
    log.gateway.debug("SessionManager.getOrCreate: entry", {
      sessionId: key,
      channelId: message.channelId,
    });

    // Cache hit — refresh timestamp and return immediately
    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
      cached.lastActivity = Date.now();
      log.gateway.debug("SessionManager.getOrCreate: cache hit", { sessionId: key });
      return cached.session;
    }

    log.gateway.debug("SessionManager.getOrCreate: cache miss, reading store", {
      sessionId: key,
    });

    let session: Session;

    if (this.ctx.sessionService) {
      // Prefer SQLite-backed SessionService
      const parts = key.split(":");
      const userId =
        message.userId ?? (parts.length >= 2 ? parts.slice(1).join(":") : key);
      log.gateway.debug(
        "SessionManager.getOrCreate: delegating to SessionService",
        { sessionId: key, userId },
      );
      session = await this.ctx.sessionService.getOrCreate(
        key,
        userId,
        this.ctx.owl?.persona.name ?? "owl",
      );
    } else {
      // JSON store fallback
      const existing = await this.ctx.sessionStore.loadSession(key);
      if (existing) {
        session = existing;
        log.gateway.debug("SessionManager.getOrCreate: loaded from store", {
          sessionId: key,
        });
      } else {
        session = this.ctx.sessionStore.createSession(
          this.ctx.owl?.persona.name ?? "owl",
        );
        session.id = key;
        await this.ctx.sessionStore.saveSession(session);
        log.gateway.debug("SessionManager.getOrCreate: created new session", {
          sessionId: key,
        });
      }
    }

    this.cache.set(key, { session, lastActivity: Date.now() });
    log.gateway.debug("SessionManager.getOrCreate: exit", {
      sessionId: key,
      messageCount: session.messages.length,
    });
    return session;
  }

  /**
   * Persist session to the JSON store. The SQLite path is handled by callers
   * (via sessionService.addMessages) — this method only touches the JSON store.
   */
  async save(session: Session): Promise<void> {
    log.gateway.debug("SessionManager.save: entry", { sessionId: session.id });
    try {
      await this.ctx.sessionStore.saveSession(session);
      log.gateway.debug("SessionManager.save: exit", { sessionId: session.id });
    } catch (err) {
      log.gateway.error("SessionManager.save: failed", err as Error, {
        sessionId: session.id,
      });
      throw err;
    }
  }

  /** Remove a session from the in-memory cache (does not delete from store). */
  invalidate(sessionId: string): void {
    log.gateway.debug("SessionManager.invalidate: entry", { sessionId });
    this.cache.delete(sessionId);
    log.gateway.debug("SessionManager.invalidate: exit", { sessionId });
  }

  /**
   * Evict sessions that have been idle longer than SESSION_TIMEOUT_MS.
   * Does NOT call endSession — that remains the caller's responsibility so that
   * episodic memory extraction runs at the right time.
   */
  evictStale(): void {
    log.gateway.debug("SessionManager.evictStale: entry", { cacheSize: this.cache.size });
    const now = Date.now();
    let evicted = 0;
    for (const [key, entry] of this.cache) {
      if (now - entry.lastActivity > SESSION_TIMEOUT_MS) {
        this.cache.delete(key);
        evicted++;
      }
    }
    log.gateway.debug("SessionManager.evictStale: exit", { evicted, cacheSize: this.cache.size });
  }

  /** Number of sessions currently held in the in-memory cache. */
  getActiveCount(): number {
    log.gateway.debug("SessionManager.getActiveCount: entry", {});
    const count = this.cache.size;
    log.gateway.debug("SessionManager.getActiveCount: exit", { count });
    return count;
  }

  /**
   * Return a cached session entry without side effects, or undefined if not
   * present. Used by evictStaleSessions in OwlGateway to inspect the session
   * before calling endSession.
   */
  getCached(sessionId: string): { session: Session; lastActivity: number } | undefined {
    return this.cache.get(sessionId);
  }

  /**
   * Return the keys of sessions that would be evicted (are expired) without
   * deleting them. Used by OwlGateway.evictStaleSessions() to run per-session
   * side effects before handing off actual cache deletion to evictStale().
   */
  getStaleIds(now: number): string[] {
    log.gateway.debug("SessionManager.getStaleIds: entry", { cacheSize: this.cache.size, now });
    const stale: string[] = [];
    for (const [key, entry] of this.cache) {
      if (now - entry.lastActivity > SESSION_TIMEOUT_MS) {
        stale.push(key);
      }
    }
    log.gateway.debug("SessionManager.getStaleIds: exit", { staleCount: stale.length });
    return stale;
  }

  /**
   * Iterate all cached entries. Used by OwlGateway.evictStaleSessions()
   * to run endSession logic before handing off eviction to this manager.
   */
  entries(): IterableIterator<[string, { session: Session; lastActivity: number }]> {
    return this.cache.entries();
  }
}
