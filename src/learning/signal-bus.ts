/**
 * StackOwl — Signal Bus
 *
 * Pub/Sub system for routing micro-learner signals to all
 * interested subsystems. Solves the unidirectional learning problem:
 * MicroLearner captures signals, but nothing routes them to DNA
 * evolution, skill routing, proactive planner, or orchestrator.
 *
 * Subscribers:
 *   - DNA EvolutionEngine → sentiment trends → evolution decisions
 *   - SkillRouter → tool usage patterns → skill prioritization
 *   - AutonomousPlanner → temporal patterns → scheduling
 *   - MutationTracker → satisfaction feedback → rollback decisions
 */

import type { MicroSignal } from "./micro-learner.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type SignalFilter = (signal: MicroSignal) => boolean;
export type SignalHandler = (signals: MicroSignal[]) => void | Promise<void>;

export interface SignalSubscription {
  id: string;
  name: string;
  filter: SignalFilter;
  handler: SignalHandler;
  /** Batch signals instead of firing one at a time */
  batchSize: number;
  /** Max interval between batch flushes (ms) */
  flushIntervalMs: number;
}

export interface SignalBusStats {
  totalSignals: number;
  subscriberCount: number;
  signalsByType: Record<string, number>;
  lastSignalAt: string;
}

// ─── Filters ─────────────────────────────────────────────────────

/** Pre-built filters for common use cases */
export const SignalFilters = {
  /** Only sentiment signals (positive/negative reactions) */
  sentiment: (s: MicroSignal) => s.type === "sentiment",
  /** Only topic mentions */
  topics: (s: MicroSignal) => s.type === "topic",
  /** Only tool usage events */
  toolUsage: (s: MicroSignal) => s.type === "tool_use",
  /** Only temporal patterns */
  temporal: (s: MicroSignal) => s.type === "temporal",
  /** Only style signals */
  style: (s: MicroSignal) => s.type === "style",
  /** Negative sentiment only */
  negative: (s: MicroSignal) => s.type === "sentiment" && s.key === "negative",
  /** Positive sentiment only */
  positive: (s: MicroSignal) => s.type === "sentiment" && s.key === "positive",
  /** All signals */
  all: (_s: MicroSignal) => true,
} as const;

// ─── Signal Bus ──────────────────────────────────────────────────

export class SignalBus {
  private subscribers: Map<string, SignalSubscription> = new Map();
  private buffers: Map<string, MicroSignal[]> = new Map();
  private flushTimers: Map<string, ReturnType<typeof setTimeout>> = new Map();
  private stats: SignalBusStats = {
    totalSignals: 0,
    subscriberCount: 0,
    signalsByType: {},
    lastSignalAt: "",
  };

  /**
   * Subscribe to signals with a filter and handler.
   * Returns an unsubscribe function.
   */
  subscribe(
    name: string,
    filter: SignalFilter,
    handler: SignalHandler,
    options?: { batchSize?: number; flushIntervalMs?: number },
  ): () => void {
    const id = `sub_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

    const subscription: SignalSubscription = {
      id,
      name,
      filter,
      handler,
      batchSize: options?.batchSize ?? 1,
      flushIntervalMs: options?.flushIntervalMs ?? 5000,
    };

    this.subscribers.set(id, subscription);
    this.buffers.set(id, []);
    this.stats.subscriberCount = this.subscribers.size;

    log.engine.debug(`[SignalBus] Subscriber added: "${name}" (${id})`);

    // Return unsubscribe function
    return () => {
      this.subscribers.delete(id);
      this.buffers.delete(id);
      const timer = this.flushTimers.get(id);
      if (timer) clearTimeout(timer);
      this.flushTimers.delete(id);
      this.stats.subscriberCount = this.subscribers.size;
    };
  }

  /**
   * Publish a signal to all matching subscribers.
   * Signals are batched per subscriber according to their batchSize.
   */
  publish(signal: MicroSignal): void {
    this.stats.totalSignals++;
    this.stats.signalsByType[signal.type] =
      (this.stats.signalsByType[signal.type] ?? 0) + 1;
    this.stats.lastSignalAt = signal.timestamp;

    for (const [id, sub] of this.subscribers) {
      if (!sub.filter(signal)) continue;

      const buffer = this.buffers.get(id) ?? [];
      buffer.push(signal);
      this.buffers.set(id, buffer);

      if (buffer.length >= sub.batchSize) {
        this.flush(id);
      } else {
        // Set a timer to flush even if batch isn't full
        this.scheduleFlush(id, sub.flushIntervalMs);
      }
    }
  }

  /**
   * Publish multiple signals at once (e.g., from a single message).
   */
  publishBatch(signals: MicroSignal[]): void {
    for (const signal of signals) {
      this.publish(signal);
    }
  }

  /**
   * Flush all pending buffers immediately.
   * Call before shutdown to ensure no signals are lost.
   */
  flushAll(): void {
    for (const id of this.subscribers.keys()) {
      this.flush(id);
    }
  }

  /**
   * Get bus statistics for monitoring.
   */
  getStats(): SignalBusStats {
    return { ...this.stats };
  }

  /**
   * Remove all subscribers. Call during shutdown.
   */
  destroy(): void {
    this.flushAll();
    for (const timer of this.flushTimers.values()) {
      clearTimeout(timer);
    }
    this.subscribers.clear();
    this.buffers.clear();
    this.flushTimers.clear();
  }

  // ─── Private ───────────────────────────────────────────────────

  private flush(subscriberId: string): void {
    const buffer = this.buffers.get(subscriberId);
    const sub = this.subscribers.get(subscriberId);
    if (!buffer || buffer.length === 0 || !sub) return;

    // Clear buffer before calling handler to avoid re-entrancy
    this.buffers.set(subscriberId, []);

    // Clear any pending flush timer
    const timer = this.flushTimers.get(subscriberId);
    if (timer) {
      clearTimeout(timer);
      this.flushTimers.delete(subscriberId);
    }

    try {
      const result = sub.handler(buffer);
      // If handler returns a promise, catch errors
      if (result instanceof Promise) {
        result.catch((err) => {
          log.engine.warn(
            `[SignalBus] Handler "${sub.name}" failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        });
      }
    } catch (err) {
      log.engine.warn(
        `[SignalBus] Handler "${sub.name}" threw: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private scheduleFlush(subscriberId: string, delayMs: number): void {
    // Don't re-schedule if already pending
    if (this.flushTimers.has(subscriberId)) return;

    const timer = setTimeout(() => {
      this.flushTimers.delete(subscriberId);
      this.flush(subscriberId);
    }, delayMs);

    this.flushTimers.set(subscriberId, timer);
  }
}
