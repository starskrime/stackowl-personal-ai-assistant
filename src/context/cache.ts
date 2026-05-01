interface CacheEntry {
  output: string;
  tokensUsed: number;
  cachedAt: number;
  ttlMs: number;
  userId?: string;
}

export class ContextCache {
  // Primary store: fullKey → entry. Map insertion order = LRU order.
  private store = new Map<string, CacheEntry>();
  // Reverse index: userId → Set of fullKeys
  private userIndex = new Map<string, Set<string>>();
  private hits = 0;
  private misses = 0;
  private evictions = 0;

  constructor(private maxEntries: number = 200) {}

  private fullKey(layerName: string, cacheKey: string): string {
    return `${layerName}:${cacheKey}`;
  }

  private removeFromUserIndex(key: string, userId?: string): void {
    if (!userId) return;
    const keys = this.userIndex.get(userId);
    if (!keys) return;
    keys.delete(key);
    if (keys.size === 0) this.userIndex.delete(userId);
  }

  get(layerName: string, cacheKey: string): string | null {
    const key = this.fullKey(layerName, cacheKey);
    const entry = this.store.get(key);
    if (!entry) { this.misses++; return null; }
    if (Date.now() - entry.cachedAt > entry.ttlMs) {
      this.removeFromUserIndex(key, entry.userId);
      this.store.delete(key);
      this.misses++;
      return null;
    }
    // Refresh LRU position
    this.store.delete(key);
    this.store.set(key, entry);
    this.hits++;
    return entry.output;
  }

  set(layerName: string, cacheKey: string, output: string, ttlMs: number, userId?: string): void {
    const key = this.fullKey(layerName, cacheKey);
    // Evict oldest if at capacity
    if (this.store.size >= this.maxEntries) {
      const oldest = this.store.keys().next().value;
      if (oldest !== undefined) {
        const oldestEntry = this.store.get(oldest);
        this.removeFromUserIndex(oldest, oldestEntry?.userId);
        this.store.delete(oldest);
        this.evictions++;
      }
    }
    this.store.set(key, {
      output,
      tokensUsed: Math.ceil(output.length / 3.8),
      cachedAt: Date.now(),
      ttlMs,
      userId,
    });
    if (userId) {
      const keys = this.userIndex.get(userId) ?? new Set();
      keys.add(key);
      this.userIndex.set(userId, keys);
    }
  }

  invalidate(layerName: string): void {
    const prefix = `${layerName}:`;
    for (const [key, entry] of [...this.store.entries()]) {
      if (key.startsWith(prefix)) {
        this.removeFromUserIndex(key, entry.userId);
        this.store.delete(key);
      }
    }
  }

  invalidateUser(userId: string): void {
    const keys = this.userIndex.get(userId);
    if (!keys) return;
    for (const key of keys) this.store.delete(key);
    this.userIndex.delete(userId);
  }

  stats(): { size: number; hitRate: number; evictions: number } {
    const total = this.hits + this.misses;
    return {
      size: this.store.size,
      hitRate: total > 0 ? this.hits / total : 0,
      evictions: this.evictions,
    };
  }
}
