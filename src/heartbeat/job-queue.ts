/**
 * StackOwl — Proactive Job Queue
 *
 * Replaces the 8 independent `setInterval` timers in ProactivePinger with a
 * single, SQLite-backed durable job queue.
 *
 * Problem solved: the old timer soup had no memory. If quiet hours blocked a
 * "morning brief" job, it was forgotten. If the process restarted, all pending
 * jobs were lost. Each timer independently decided what to do on every tick,
 * with no shared state or priority.
 *
 * Design:
 *   - Jobs are rows in a SQLite table: {id, type, userId, scheduledAt, payload, status, priority}
 *   - A single 30-second worker tick polls for due jobs, respects quiet hours
 *     by rescheduling (not discarding), executes them in priority order, and
 *     marks them done.
 *   - Recurring jobs (daily brief, check-in) re-enqueue themselves after
 *     execution.
 *   - Quiet hours pause delivery — jobs shift forward to next wake window.
 *
 * Inspired by: Agenda.js (Node.js durable job scheduler), BullMQ (priority queues),
 * Temporal.io (durable workflows with sleep/wake semantics).
 */

import Database from "better-sqlite3";
import { join } from "node:path";
import { existsSync, mkdirSync } from "node:fs";
import { log } from "../logger.js";

// ─── Job Types ───────────────────────────────────────────────────

export type JobType =
  | "morning_brief"          // Daily morning summary
  | "check_in"               // Periodic user check-in
  | "memory_consolidation"   // Compress old messages
  | "tool_pruning"           // Remove unused synthesized tools
  | "self_study"             // Research a topic gap
  | "knowledge_council"      // Cross-owl knowledge session
  | "dream_reflexion"        // Reflexion/self-improvement pass
  | "skill_evolution"        // Evolve owl skills
  | "background_task"        // Arbitrary background work
  | "goal_check";            // Review active goals

export type JobStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface ProactiveJob {
  id: string;
  type: JobType;
  userId: string;
  /** ISO-8601 timestamp — when this job should fire */
  scheduledAt: string;
  /** JSON-serialized payload (arbitrary per job type) */
  payload: string;
  status: JobStatus;
  /** 1 (low) – 10 (critical). Higher runs first. */
  priority: number;
  /** Number of times this job has been attempted */
  attempts: number;
  /** When it was last attempted */
  lastAttemptAt?: string;
  /** Failure reason if status === failed */
  error?: string;
  createdAt: string;
}

// ─── Queue ───────────────────────────────────────────────────────

export class ProactiveJobQueue {
  private db: Database.Database;

  constructor(workspacePath: string) {
    const dbPath = join(workspacePath, "proactive-jobs.db");

    if (!existsSync(workspacePath)) {
      mkdirSync(workspacePath, { recursive: true });
    }

    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("synchronous = NORMAL");
    this.createSchema();
    log.engine.debug("[JobQueue] Initialized proactive job queue");
  }

  private createSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS proactive_jobs (
        id          TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        user_id     TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,
        payload     TEXT NOT NULL DEFAULT '{}',
        status      TEXT NOT NULL DEFAULT 'pending',
        priority    INTEGER NOT NULL DEFAULT 5,
        attempts    INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT,
        error       TEXT,
        created_at  TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_pj_status_scheduled
        ON proactive_jobs (status, scheduled_at);

      CREATE INDEX IF NOT EXISTS idx_pj_user
        ON proactive_jobs (user_id, status);
    `);
  }

  // ─── Enqueue ─────────────────────────────────────────────────

  /**
   * Schedule a new job. Deduplicates by (type, userId) for single-instance
   * recurring jobs — if one already exists as pending, it's updated instead.
   */
  schedule(params: {
    type: JobType;
    userId: string;
    scheduledAt: Date;
    payload?: Record<string, unknown>;
    priority?: number;
    deduplicate?: boolean;
  }): string {
    const now = new Date().toISOString();

    if (params.deduplicate !== false) {
      // For recurring jobs: update the existing pending job rather than stacking
      const existing = this.db
        .prepare(
          `SELECT id FROM proactive_jobs
           WHERE type = ? AND user_id = ? AND status = 'pending'
           LIMIT 1`,
        )
        .get(params.type, params.userId) as { id: string } | undefined;

      if (existing) {
        this.db
          .prepare(
            `UPDATE proactive_jobs
             SET scheduled_at = ?, payload = ?, priority = ?
             WHERE id = ?`,
          )
          .run(
            params.scheduledAt.toISOString(),
            JSON.stringify(params.payload ?? {}),
            params.priority ?? 5,
            existing.id,
          );
        return existing.id;
      }
    }

    const id = `job_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    this.db
      .prepare(
        `INSERT INTO proactive_jobs
         (id, type, user_id, scheduled_at, payload, status, priority, attempts, created_at)
         VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?)`,
      )
      .run(
        id,
        params.type,
        params.userId,
        params.scheduledAt.toISOString(),
        JSON.stringify(params.payload ?? {}),
        params.priority ?? 5,
        now,
      );

    return id;
  }

  /**
   * Get all jobs due now (scheduledAt <= now, status = pending), ordered by priority.
   */
  getDueJobs(limit = 10): ProactiveJob[] {
    const now = new Date().toISOString();
    return this.db
      .prepare(
        `SELECT * FROM proactive_jobs
         WHERE status = 'pending' AND scheduled_at <= ?
         ORDER BY priority DESC, scheduled_at ASC
         LIMIT ?`,
      )
      .all(now, limit) as ProactiveJob[];
  }

  /**
   * Mark a job as running (prevents duplicate execution).
   */
  markRunning(jobId: string): void {
    this.db
      .prepare(
        `UPDATE proactive_jobs
         SET status = 'running', last_attempt_at = ?, attempts = attempts + 1
         WHERE id = ?`,
      )
      .run(new Date().toISOString(), jobId);
  }

  /**
   * Mark a job as done.
   */
  markDone(jobId: string): void {
    this.db
      .prepare(`UPDATE proactive_jobs SET status = 'done' WHERE id = ?`)
      .run(jobId);
  }

  /**
   * Mark a job as failed with an error.
   */
  markFailed(jobId: string, error: string): void {
    this.db
      .prepare(
        `UPDATE proactive_jobs SET status = 'failed', error = ? WHERE id = ?`,
      )
      .run(error.slice(0, 500), jobId);
  }

  /**
   * Reschedule a job (used when quiet hours block delivery).
   */
  reschedule(jobId: string, newTime: Date): void {
    this.db
      .prepare(
        `UPDATE proactive_jobs
         SET scheduled_at = ?, status = 'pending'
         WHERE id = ?`,
      )
      .run(newTime.toISOString(), jobId);
  }

  /**
   * Get the next scheduled time for a given job type for a user.
   * Returns null if no pending job of that type exists.
   */
  getNextScheduled(type: JobType, userId: string): Date | null {
    const row = this.db
      .prepare(
        `SELECT scheduled_at FROM proactive_jobs
         WHERE type = ? AND user_id = ? AND status = 'pending'
         ORDER BY scheduled_at ASC LIMIT 1`,
      )
      .get(type, userId) as { scheduled_at: string } | undefined;
    return row ? new Date(row.scheduled_at) : null;
  }

  /**
   * Prune old done/failed/skipped jobs older than retentionDays.
   */
  cleanup(retentionDays = 7): number {
    const cutoff = new Date(
      Date.now() - retentionDays * 24 * 60 * 60 * 1000,
    ).toISOString();
    const result = this.db
      .prepare(
        `DELETE FROM proactive_jobs
         WHERE status IN ('done','failed','skipped') AND created_at < ?`,
      )
      .run(cutoff);
    return result.changes;
  }

  close(): void {
    this.db.close();
  }
}
