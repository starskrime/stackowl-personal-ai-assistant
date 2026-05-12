import type { MemoryDatabase } from "../memory/db.js";
import type { ScheduledJob, JobStatus } from "./types.js";

interface Row {
  id: string;
  type: "remind" | "repeat";
  message: string;
  schedule_at: string | null;
  interval_ms: number | null;
  next_fire_at: string;
  created_at: string;
  status: JobStatus;
  metadata: string | null;
}

function toJob(r: Row): ScheduledJob {
  return {
    id: r.id,
    type: r.type,
    message: r.message,
    scheduleAt: r.schedule_at ?? undefined,
    intervalMs: r.interval_ms ?? undefined,
    nextFireAt: r.next_fire_at,
    createdAt: r.created_at,
    status: r.status,
    metadata: r.metadata ? JSON.parse(r.metadata) : {},
  };
}

export class ScheduleStore {
  constructor(private readonly db: MemoryDatabase) {}

  add(job: ScheduledJob): void {
    this.db.rawDb.prepare(`
      INSERT OR REPLACE INTO scheduled_jobs
      (id, type, message, schedule_at, interval_ms, next_fire_at, created_at, status, metadata)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      job.id,
      job.type,
      job.message,
      job.scheduleAt ?? null,
      job.intervalMs ?? null,
      job.nextFireAt,
      job.createdAt,
      job.status,
      JSON.stringify(job.metadata ?? {}),
    );
  }

  update(id: string, patch: Partial<ScheduledJob>): void {
    const existing = this.findOne(id);
    if (!existing) return;
    const next: ScheduledJob = {
      ...existing,
      ...patch,
      metadata: { ...existing.metadata, ...(patch.metadata ?? {}) },
    };
    this.add(next);
  }

  remove(id: string): void {
    this.db.rawDb.prepare("DELETE FROM scheduled_jobs WHERE id = ?").run(id);
  }

  findOne(id: string): ScheduledJob | null {
    const row = this.db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE id = ?").get(id) as Row | undefined;
    return row ? toJob(row) : null;
  }

  list(filter?: { status?: JobStatus }): ScheduledJob[] {
    const rows = filter?.status
      ? (this.db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY next_fire_at").all(filter.status) as Row[])
      : (this.db.rawDb.prepare("SELECT * FROM scheduled_jobs ORDER BY next_fire_at").all() as Row[]);
    return rows.map(toJob);
  }

  due(now: Date): ScheduledJob[] {
    const rows = this.db.rawDb.prepare(
      "SELECT * FROM scheduled_jobs WHERE status = 'active' AND next_fire_at <= ? ORDER BY next_fire_at",
    ).all(now.toISOString()) as Row[];
    return rows.map(toJob);
  }
}
