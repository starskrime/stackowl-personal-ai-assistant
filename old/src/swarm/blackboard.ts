/**
 * StackOwl — Swarm Blackboard
 *
 * Shared key-value store for inter-agent communication during
 * SWARM parallel execution. Agents can read/write to the blackboard
 * during their execution, enabling coordination without blocking.
 *
 * Features:
 *   - Concurrent-safe read/write (single-threaded JS, but async-safe)
 *   - Typed entries with timestamps
 *   - Wait-for-key capability (agent can yield until a key appears)
 *   - Automatic cleanup after swarm execution completes
 */

import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface BlackboardEntry {
  key: string;
  value: unknown;
  writtenBy: string; // owl name or agent ID
  timestamp: number;
}

// ─── Blackboard ──────────────────────────────────────────────────

export class SwarmBlackboard {
  private entries: Map<string, BlackboardEntry> = new Map();
  /** Waiters: callbacks waiting for a specific key to appear */
  private waiters: Map<string, Array<(entry: BlackboardEntry) => void>> =
    new Map();

  /**
   * Write a value to the blackboard.
   * If another agent is waiting for this key, it will be notified immediately.
   */
  write(key: string, value: unknown, writtenBy: string): void {
    const entry: BlackboardEntry = {
      key,
      value,
      writtenBy,
      timestamp: Date.now(),
    };

    this.entries.set(key, entry);

    log.engine.info(`[Blackboard] ${writtenBy} wrote "${key}"`);

    // Notify any waiters
    const keyWaiters = this.waiters.get(key);
    if (keyWaiters && keyWaiters.length > 0) {
      for (const resolve of keyWaiters) {
        resolve(entry);
      }
      this.waiters.delete(key);
    }
  }

  /**
   * Read a value from the blackboard.
   * Returns undefined if the key doesn't exist yet.
   */
  read<T = unknown>(key: string): T | undefined {
    const entry = this.entries.get(key);
    return entry ? (entry.value as T) : undefined;
  }

  /**
   * Check if a key exists on the blackboard.
   */
  has(key: string): boolean {
    return this.entries.has(key);
  }

  /**
   * Wait for a key to appear on the blackboard.
   * If the key already exists, resolves immediately.
   * Supports a timeout to prevent infinite waits.
   */
  async waitFor<T = unknown>(
    key: string,
    timeoutMs: number = 30_000,
  ): Promise<T> {
    // Already available
    const existing = this.entries.get(key);
    if (existing) return existing.value as T;

    // Wait with timeout
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        // Remove this waiter on timeout
        const keyWaiters = this.waiters.get(key);
        if (keyWaiters) {
          const idx = keyWaiters.indexOf(waiterCallback);
          if (idx >= 0) keyWaiters.splice(idx, 1);
        }
        reject(
          new Error(
            `Blackboard: timeout waiting for key "${key}" after ${timeoutMs}ms`,
          ),
        );
      }, timeoutMs);

      const waiterCallback = (entry: BlackboardEntry) => {
        clearTimeout(timer);
        resolve(entry.value as T);
      };

      if (!this.waiters.has(key)) {
        this.waiters.set(key, []);
      }
      this.waiters.get(key)!.push(waiterCallback);
    });
  }

  /**
   * Get all entries written by a specific agent.
   */
  getByAuthor(agentId: string): BlackboardEntry[] {
    return [...this.entries.values()].filter((e) => e.writtenBy === agentId);
  }

  /**
   * Get all entries as a readable summary (for injection into synthesis prompt).
   */
  toSummary(): string {
    if (this.entries.size === 0) return "";

    const lines = ["<swarm_shared_context>"];
    for (const [key, entry] of this.entries) {
      const valueStr =
        typeof entry.value === "string"
          ? entry.value.slice(0, 300)
          : JSON.stringify(entry.value).slice(0, 300);
      lines.push(`  [${entry.writtenBy}] ${key}: ${valueStr}`);
    }
    lines.push("</swarm_shared_context>");
    return lines.join("\n");
  }

  /**
   * Get the number of entries.
   */
  get size(): number {
    return this.entries.size;
  }

  /**
   * Clear all entries. Called after swarm execution completes.
   */
  clear(): void {
    this.entries.clear();
    // Reject any outstanding waiters
    for (const [key, keyWaiters] of this.waiters) {
      for (const resolve of keyWaiters) {
        // Resolve with undefined to avoid unhandled rejections
        resolve({
          key,
          value: undefined,
          writtenBy: "system",
          timestamp: Date.now(),
        });
      }
    }
    this.waiters.clear();
  }
}
