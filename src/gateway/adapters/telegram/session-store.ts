import { log } from "../../../logger.js";

interface Entry<T> {
  value: T;
  lastSeen: number;
}

interface SessionStoreOptions {
  /** How long an inactive entry lives before eviction. */
  ttlMs: number;
  /** How often to run the cleanup sweep. Default: ttlMs / 2. */
  cleanupIntervalMs?: number;
}

/**
 * Type-safe TTL map for Telegram session state.
 * get() touches lastSeen so active sessions are never evicted mid-processing.
 * Call destroy() in TelegramAdapter.stop() to clear the cleanup interval.
 */
export class SessionStore<T> {
  private readonly store = new Map<number, Entry<T>>();
  private readonly ttlMs: number;
  private readonly cleanupTimer: ReturnType<typeof setInterval>;

  constructor(opts: SessionStoreOptions) {
    log.telegram.debug("session-store.constructor: entry", { ttlMs: opts.ttlMs });
    this.ttlMs = opts.ttlMs;
    const interval = opts.cleanupIntervalMs ?? Math.floor(opts.ttlMs / 2);
    this.cleanupTimer = setInterval(() => this.cleanup(), interval);
    log.telegram.debug("session-store.constructor: exit", { intervalMs: interval });
  }

  get(key: number): T | undefined {
    const entry = this.store.get(key);
    if (!entry) return undefined;
    entry.lastSeen = Date.now(); // touch-on-read
    return entry.value;
  }

  set(key: number, value: T): void {
    this.store.set(key, { value, lastSeen: Date.now() });
  }

  has(key: number): boolean {
    return this.store.has(key);
  }

  delete(key: number): void {
    this.store.delete(key);
  }

  /** Clears the cleanup interval. Must be called in TelegramAdapter.stop(). */
  destroy(): void {
    log.telegram.debug("session-store.destroy: entry");
    clearInterval(this.cleanupTimer);
    log.telegram.debug("session-store.destroy: exit");
  }

  private cleanup(): void {
    log.telegram.debug("session-store.cleanup: entry", { size: this.store.size });
    const now = Date.now();
    let evicted = 0;
    for (const [key, entry] of this.store) {
      if (now - entry.lastSeen > this.ttlMs) {
        this.store.delete(key);
        evicted++;
      }
    }
    log.telegram.debug("session-store.cleanup: exit", { evicted, remaining: this.store.size });
  }
}
