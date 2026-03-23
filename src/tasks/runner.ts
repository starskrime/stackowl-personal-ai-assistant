/**
 * StackOwl — Background Task Runner
 *
 * Detaches long-running ReAct loop executions into background promises.
 * The user can continue chatting while background tasks run.
 *
 * Architecture:
 *   1. Gateway detects a long-running request (tool-heavy, multi-step)
 *   2. Runner forks the ReAct loop into a detached Promise
 *   3. Progress updates are delivered via EventBus / channel callbacks
 *   4. Checkpoints are persisted to TaskStore for resume-on-crash
 *   5. User gets a "task started" confirmation and can send new messages
 *
 * Naming: "BackgroundTaskRunner" vs "TaskQueue" — the TaskQueue handles
 * message ordering (FIFO). This runner handles detached execution.
 */

import type { Task } from './types.js';
import type { TaskStore } from './store.js';
import type { OwlEngine, EngineContext, EngineResponse } from '../engine/runtime.js';
import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

export interface BackgroundTaskCallbacks {
  /** Called when background task starts */
  onStart?: (task: Task) => Promise<void>;
  /** Called with progress updates during execution */
  onProgress?: (task: Task, message: string) => Promise<void>;
  /** Called when background task completes */
  onComplete?: (task: Task, response: EngineResponse) => Promise<void>;
  /** Called when background task fails */
  onFail?: (task: Task, error: string) => Promise<void>;
}

interface RunningTask {
  task: Task;
  promise: Promise<EngineResponse | null>;
  abortController: AbortController;
}

// ─── Runner ──────────────────────────────────────────────────────

export class BackgroundTaskRunner {
  /** Currently running background tasks keyed by taskId */
  private running: Map<string, RunningTask> = new Map();
  /** Max concurrent background tasks per user */
  private static readonly MAX_PER_USER = 3;

  constructor(
    private store: TaskStore,
    private engine: OwlEngine,
  ) {}

  /**
   * Check if there are too many background tasks for a user.
   */
  canAccept(userId: string): boolean {
    const active = this.store.getActiveBackground(userId);
    return active.length < BackgroundTaskRunner.MAX_PER_USER;
  }

  /**
   * Start a task in the background. Returns the Task immediately.
   * The actual execution happens asynchronously.
   */
  async start(
    userMessage: string,
    context: EngineContext,
    meta: { channelId: string; userId: string; sessionId: string },
    callbacks: BackgroundTaskCallbacks,
  ): Promise<Task> {
    // Validate capacity
    if (!this.canAccept(meta.userId)) {
      throw new Error(
        `Too many background tasks running (max ${BackgroundTaskRunner.MAX_PER_USER}). ` +
        `Wait for active tasks to complete.`,
      );
    }

    // Create persistent task
    const task = await this.store.create({
      userMessage,
      channelId: meta.channelId,
      userId: meta.userId,
      sessionId: meta.sessionId,
      background: true,
    });

    // Create abort controller for cancellation
    const abortController = new AbortController();

    // Fork execution into a detached promise
    const promise = this.executeInBackground(task, userMessage, context, callbacks, abortController.signal);

    this.running.set(task.id, { task, promise, abortController });

    // Clean up when done
    promise.finally(() => {
      this.running.delete(task.id);
    });

    // Notify start
    await this.store.updateStatus(task.id, 'running');
    if (callbacks.onStart) {
      await callbacks.onStart(task).catch((err: unknown) => {
        log.engine.warn(`[BackgroundRunner] onStart failed: ${err instanceof Error ? err.message : err}`);
      });
    }

    return task;
  }

  /**
   * Cancel a running background task.
   */
  async cancel(taskId: string): Promise<boolean> {
    const running = this.running.get(taskId);
    if (!running) return false;

    running.abortController.abort();
    await this.store.updateStatus(taskId, 'cancelled');
    this.running.delete(taskId);
    return true;
  }

  /**
   * Get status of all running background tasks for a user.
   */
  getRunningTasks(userId: string): Task[] {
    return this.store.getActiveBackground(userId);
  }

  /**
   * Resume tasks that were interrupted by a crash.
   * Called during startup.
   */
  async resumeInterrupted(
    contextFactory: (task: Task) => EngineContext,
    callbacks: BackgroundTaskCallbacks,
  ): Promise<number> {
    const resumable = this.store.getResumable();
    let resumed = 0;

    for (const task of resumable) {
      try {
        log.engine.info(`[BackgroundRunner] Resuming interrupted task: ${task.id}`);
        const context = contextFactory(task);
        const abortController = new AbortController();

        const promise = this.executeInBackground(task, task.userMessage, context, callbacks, abortController.signal);
        this.running.set(task.id, { task, promise, abortController });
        promise.finally(() => this.running.delete(task.id));

        resumed++;
      } catch (err) {
        log.engine.error(`[BackgroundRunner] Failed to resume task ${task.id}: ${err instanceof Error ? err.message : err}`);
        await this.store.updateStatus(task.id, 'failed', {
          error: `Resume failed: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    }

    if (resumed > 0) {
      log.engine.info(`[BackgroundRunner] Resumed ${resumed} interrupted task(s)`);
    }

    return resumed;
  }

  // ─── Private ───────────────────────────────────────────────────

  private async executeInBackground(
    task: Task,
    userMessage: string,
    context: EngineContext,
    callbacks: BackgroundTaskCallbacks,
    signal: AbortSignal,
  ): Promise<EngineResponse | null> {
    try {
      // Wrap progress to persist + notify
      const wrappedContext: EngineContext = {
        ...context,
        onProgress: async (msg: string) => {
          if (signal.aborted) return;
          await this.store.updateProgress(task.id, msg);
          if (callbacks.onProgress) {
            await callbacks.onProgress(task, msg).catch(() => {});
          }
          // Also call the original onProgress if present
          if (context.onProgress) {
            await context.onProgress(msg).catch(() => {});
          }
        },
      };

      // Execute the ReAct loop
      const response = await this.engine.run(userMessage, wrappedContext);

      if (signal.aborted) {
        return null;
      }

      // Mark completed
      await this.store.updateStatus(task.id, 'completed', {
        result: response.content,
        toolsUsed: response.toolsUsed,
      });

      if (callbacks.onComplete) {
        await callbacks.onComplete(task, response).catch((err: unknown) => {
          log.engine.warn(`[BackgroundRunner] onComplete failed: ${err instanceof Error ? err.message : err}`);
        });
      }

      return response;
    } catch (err) {
      if (signal.aborted) return null;

      const errorMsg = err instanceof Error ? err.message : String(err);
      log.engine.error(`[BackgroundRunner] Task ${task.id} failed: ${errorMsg}`);

      await this.store.updateStatus(task.id, 'failed', { error: errorMsg });

      if (callbacks.onFail) {
        await callbacks.onFail(task, errorMsg).catch(() => {});
      }

      return null;
    }
  }
}
