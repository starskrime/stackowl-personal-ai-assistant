/**
 * StackOwl — Task Queue
 *
 * Simple in-process async task queue with concurrency control.
 * Replaces fire-and-forget runBackground() with observable, bounded execution.
 */

import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type TaskPriority = "high" | "normal" | "low";

export interface QueuedTask {
  id: string;
  name: string;
  priority: TaskPriority;
  execute: () => Promise<unknown>;
  createdAt: number;
}

export interface TaskQueueConfig {
  /** Max parallel tasks. Default: 3 */
  concurrency: number;
  /** Max tasks in the queue. Rejects new tasks when full. Default: 100 */
  maxQueueSize: number;
}

export interface TaskQueueStats {
  pending: number;
  active: number;
  completed: number;
  failed: number;
}

const DEFAULT_CONFIG: TaskQueueConfig = {
  concurrency: 3,
  maxQueueSize: 100,
};

// ─── Implementation ─────────────────────────────────────────────

let taskIdCounter = 0;

export class TaskQueue {
  private config: TaskQueueConfig;
  private queue: QueuedTask[] = [];
  private active = 0;
  private activeHigh = 0;
  private criticalDrainResolvers: Array<() => void> = [];
  private stats = { completed: 0, failed: 0 };

  constructor(config?: Partial<TaskQueueConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Add a task to the queue. Returns the task ID.
   * High-priority tasks are inserted before normal/low ones.
   */
  enqueue(
    name: string,
    execute: () => Promise<unknown>,
    priority: TaskPriority = "normal",
  ): string {
    if (this.queue.length >= this.config.maxQueueSize) {
      const err = new Error(
        `[TaskQueue] Queue full (${this.config.maxQueueSize}), cannot add task "${name}"`,
      );
      log.engine.error(err.message);
      throw err;
    }

    const id = `task-${++taskIdCounter}`;
    const task: QueuedTask = {
      id,
      name,
      priority,
      execute,
      createdAt: Date.now(),
    };

    // Insert by priority: high first, then normal, then low
    if (priority === "high") {
      const insertAt = this.queue.findIndex((t) => t.priority !== "high");
      if (insertAt === -1) this.queue.push(task);
      else this.queue.splice(insertAt, 0, task);
    } else if (priority === "low") {
      this.queue.push(task);
    } else {
      const insertAt = this.queue.findIndex((t) => t.priority === "low");
      if (insertAt === -1) this.queue.push(task);
      else this.queue.splice(insertAt, 0, task);
    }

    this.processNext();
    return id;
  }

  /** Wait until all queued and active tasks are done. */
  async drain(): Promise<void> {
    while (this.queue.length > 0 || this.active > 0) {
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  /** Wait until all high-priority tasks are dequeued and completed. */
  drainCritical(): Promise<void> {
    if (!this.queue.some(t => t.priority === "high") && this.activeHigh === 0) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.criticalDrainResolvers.push(resolve);
    });
  }

  getStats(): TaskQueueStats {
    return {
      pending: this.queue.length,
      active: this.active,
      completed: this.stats.completed,
      failed: this.stats.failed,
    };
  }

  private processNext(): void {
    while (this.active < this.config.concurrency && this.queue.length > 0) {
      const task = this.queue.shift()!;
      this.active++;
      if (task.priority === "high") this.activeHigh++;

      const startMs = Date.now();
      task
        .execute()
        .then(() => {
          this.stats.completed++;
          const elapsed = Date.now() - startMs;
          if (elapsed > 5000) {
            log.engine.info(
              `[TaskQueue] Task "${task.name}" completed in ${elapsed}ms`,
            );
          }
        })
        .catch((err) => {
          this.stats.failed++;
          const elapsed = Date.now() - startMs;
          const errMsg =
            err instanceof Error
              ? `${err.message}\n${err.stack ?? ""}`
              : String(err);
          log.engine.error(
            `[TaskQueue] Task "${task.name}" FAILED after ${elapsed}ms:\n${errMsg}`,
          );
        })
        .finally(() => {
          const wasHigh = task.priority === "high";
          if (wasHigh) this.activeHigh--;
          this.active--;

          const hasPendingCritical = wasHigh &&
            this.activeHigh === 0 &&
            !this.queue.some(t => t.priority === "high") &&
            this.criticalDrainResolvers.length > 0;

          if (hasPendingCritical) {
            // Fire resolvers first (as microtasks), then schedule processNext
            // so drainCritical consumers observe completion before normal tasks start.
            const resolvers = this.criticalDrainResolvers.splice(0);
            Promise.resolve().then(() => {
              for (const resolve of resolvers) resolve();
            });
            setTimeout(() => this.processNext(), 0);
          } else {
            this.processNext();
          }
        });
    }
  }
}
