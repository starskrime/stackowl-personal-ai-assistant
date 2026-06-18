import { log } from '../logger.js';

export class ClarificationCoordinator {
  private recentHashes: Map<string, { sessionKey: string; ts: number }> = new Map();
  private readonly SESSION_WINDOW_MS = 5 * 60 * 1000;

  /**
   * Returns true if a semantically similar question was already asked in this session
   * within the last 5 minutes. Uses a hash of the LLM reasoning string for O(1) dedup.
   */
  shouldSuppressDuplicate(reasoning: string, sessionKey: string): boolean {
    this.evictExpired();
    const hash = this.hashReasoning(reasoning);
    const existing = this.recentHashes.get(hash);
    if (existing && existing.sessionKey === sessionKey) {
      log.engine.info(`[ClarificationCoordinator] Suppressing duplicate (hash=${hash}, session=${sessionKey})`);
      return true;
    }
    this.recentHashes.set(hash, { sessionKey, ts: Date.now() });
    return false;
  }

  private hashReasoning(reasoning: string): string {
    const normalized = reasoning.toLowerCase().slice(0, 60);
    let h = 0;
    for (let i = 0; i < normalized.length; i++) {
      h = (Math.imul(31, h) + normalized.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  private evictExpired(): void {
    const cutoff = Date.now() - this.SESSION_WINDOW_MS;
    for (const [k, v] of this.recentHashes) {
      if (v.ts < cutoff) this.recentHashes.delete(k);
    }
  }

  clear(): void {
    this.recentHashes.clear();
  }
}

export const clarificationCoordinator = new ClarificationCoordinator();
