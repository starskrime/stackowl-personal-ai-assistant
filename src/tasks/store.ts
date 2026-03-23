/**
 * StackOwl — Persistent Task Store
 *
 * Durable storage for task state. Survives process restarts.
 * Uses JSON files (one per task) for simplicity — upgradeable to SQLite.
 *
 * Responsibilities:
 *   - Create, read, update, delete tasks
 *   - List tasks by status, user, or session
 *   - Persist checkpoints for resume-on-crash
 *   - Clean up completed/failed tasks older than retention period
 */

import { mkdir, readFile, writeFile, readdir, unlink } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { Task, TaskStatus, TaskCheckpoint, TaskEvent } from './types.js';

// ─── Constants ───────────────────────────────────────────────────

/** Tasks older than this are eligible for cleanup */
const RETENTION_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// ─── Store ───────────────────────────────────────────────────────

export class TaskStore {
  private tasksDir: string;
  /** In-memory index for fast lookups (loaded from disk on init) */
  private cache: Map<string, Task> = new Map();
  /** Event listeners */
  private listeners: Array<(event: TaskEvent) => void> = [];

  constructor(workspacePath: string) {
    this.tasksDir = join(workspacePath, 'tasks');
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  async init(): Promise<void> {
    if (!existsSync(this.tasksDir)) {
      await mkdir(this.tasksDir, { recursive: true });
    }
    await this.loadAll();
  }

  /** Subscribe to task events */
  onEvent(listener: (event: TaskEvent) => void): () => void {
    this.listeners.push(listener);
    return () => {
      const idx = this.listeners.indexOf(listener);
      if (idx >= 0) this.listeners.splice(idx, 1);
    };
  }

  private emit(event: TaskEvent): void {
    for (const listener of this.listeners) {
      try { listener(event); } catch { /* non-fatal */ }
    }
  }

  // ─── CRUD ──────────────────────────────────────────────────────

  /**
   * Create a new task and persist it.
   */
  async create(params: {
    userMessage: string;
    channelId: string;
    userId: string;
    sessionId: string;
    background?: boolean;
  }): Promise<Task> {
    const now = Date.now();
    const task: Task = {
      id: `task_${now}_${Math.random().toString(36).slice(2, 8)}`,
      userMessage: params.userMessage,
      status: 'pending',
      channelId: params.channelId,
      userId: params.userId,
      sessionId: params.sessionId,
      background: params.background ?? false,
      progressMessage: '',
      progressPercent: -1,
      toolsUsed: [],
      retryCount: 0,
      maxRetries: 2,
      createdAt: now,
      updatedAt: now,
    };

    this.cache.set(task.id, task);
    await this.persist(task);
    this.emit({ type: 'task:created', task });
    return task;
  }

  /**
   * Get a task by ID.
   */
  get(taskId: string): Task | undefined {
    return this.cache.get(taskId);
  }

  /**
   * Update a task's status and persist.
   */
  async updateStatus(taskId: string, status: TaskStatus, extra?: Partial<Task>): Promise<Task | undefined> {
    const task = this.cache.get(taskId);
    if (!task) return undefined;

    task.status = status;
    task.updatedAt = Date.now();
    if (extra) Object.assign(task, extra);

    if (status === 'completed' || status === 'failed' || status === 'cancelled') {
      task.completedAt = Date.now();
    }

    await this.persist(task);

    // Emit appropriate event
    switch (status) {
      case 'running':
        this.emit({ type: 'task:started', taskId });
        break;
      case 'completed':
        this.emit({ type: 'task:completed', taskId, result: task.result ?? '' });
        break;
      case 'failed':
        this.emit({ type: 'task:failed', taskId, error: task.error ?? 'Unknown error' });
        break;
      case 'cancelled':
        this.emit({ type: 'task:cancelled', taskId });
        break;
    }

    return task;
  }

  /**
   * Update progress for a running task.
   */
  async updateProgress(taskId: string, message: string, percent: number = -1): Promise<void> {
    const task = this.cache.get(taskId);
    if (!task) return;

    task.progressMessage = message;
    task.progressPercent = percent;
    task.updatedAt = Date.now();

    // Persist less frequently for progress (every 5s) to avoid disk thrashing
    await this.persist(task);
    this.emit({ type: 'task:progress', taskId, message, percent });
  }

  /**
   * Save a ReAct loop checkpoint for resume capability.
   */
  async saveCheckpoint(taskId: string, checkpoint: TaskCheckpoint): Promise<void> {
    const task = this.cache.get(taskId);
    if (!task) return;

    task.checkpoint = checkpoint;
    task.updatedAt = Date.now();
    await this.persist(task);
    this.emit({ type: 'task:checkpoint', taskId, checkpoint });
  }

  // ─── Queries ───────────────────────────────────────────────────

  /**
   * List tasks filtered by criteria.
   */
  list(filter?: {
    status?: TaskStatus | TaskStatus[];
    userId?: string;
    sessionId?: string;
    background?: boolean;
  }): Task[] {
    let tasks = [...this.cache.values()];

    if (filter?.status) {
      const statuses = Array.isArray(filter.status) ? filter.status : [filter.status];
      tasks = tasks.filter(t => statuses.includes(t.status));
    }
    if (filter?.userId) {
      tasks = tasks.filter(t => t.userId === filter.userId);
    }
    if (filter?.sessionId) {
      tasks = tasks.filter(t => t.sessionId === filter.sessionId);
    }
    if (filter?.background !== undefined) {
      tasks = tasks.filter(t => t.background === filter.background);
    }

    return tasks.sort((a, b) => b.updatedAt - a.updatedAt);
  }

  /**
   * Get tasks that were running when the process crashed (for resume).
   */
  getResumable(): Task[] {
    return this.list({ status: ['running', 'pending'] })
      .filter(t => t.checkpoint != null);
  }

  /**
   * Get active background tasks for a user.
   */
  getActiveBackground(userId: string): Task[] {
    return this.list({ userId, status: ['running', 'pending'], background: true });
  }

  // ─── Cleanup ───────────────────────────────────────────────────

  /**
   * Remove completed/failed tasks older than retention period.
   */
  async cleanup(): Promise<number> {
    const cutoff = Date.now() - RETENTION_MS;
    let removed = 0;

    for (const [id, task] of this.cache) {
      if (
        (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') &&
        (task.completedAt ?? task.updatedAt) < cutoff
      ) {
        this.cache.delete(id);
        const filePath = join(this.tasksDir, `${id}.json`);
        await unlink(filePath).catch(() => { /* non-fatal */ });
        removed++;
      }
    }

    return removed;
  }

  // ─── Persistence ───────────────────────────────────────────────

  private async persist(task: Task): Promise<void> {
    const filePath = join(this.tasksDir, `${task.id}.json`);
    await writeFile(filePath, JSON.stringify(task, null, 2), 'utf-8');
  }

  private async loadAll(): Promise<void> {
    if (!existsSync(this.tasksDir)) return;

    try {
      const files = await readdir(this.tasksDir);
      for (const file of files) {
        if (!file.endsWith('.json')) continue;
        try {
          const data = await readFile(join(this.tasksDir, file), 'utf-8');
          const task = JSON.parse(data) as Task;
          this.cache.set(task.id, task);
        } catch {
          // Corrupted file — skip
        }
      }
    } catch {
      // Directory read failed — non-fatal
    }
  }
}
