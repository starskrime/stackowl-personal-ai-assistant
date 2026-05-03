import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyV22Migration } from "../src/memory/db.js";

describe("schema v22", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
  });

  afterEach(() => { db.close(); });

  it("creates proactive_jobs, proactive_deliveries, proactive_engagement tables", () => {
    applyV22Migration(db);
    const tables = db.prepare(
      `SELECT name FROM sqlite_master WHERE type='table'`
    ).all() as { name: string }[];
    const names = tables.map(t => t.name);
    expect(names).toContain("proactive_jobs");
    expect(names).toContain("proactive_deliveries");
    expect(names).toContain("proactive_engagement");
  });

  it("adds retry_count, suppress_count, goal_id, error columns to proactive_jobs", () => {
    applyV22Migration(db);
    const cols = (db.prepare(`PRAGMA table_info(proactive_jobs)`).all() as { name: string }[])
      .map(c => c.name);
    expect(cols).toContain("retry_count");
    expect(cols).toContain("suppress_count");
    expect(cols).toContain("goal_id");
    expect(cols).toContain("error");
  });

  it("creates idx_pj_goal index on proactive_jobs(goal_id, status)", () => {
    applyV22Migration(db);
    const indexes = (db.prepare(
      `SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='proactive_jobs'`
    ).all() as { name: string }[]).map(i => i.name);
    expect(indexes).toContain("idx_pj_goal");
    expect(indexes).toContain("idx_pj_status_scheduled");
    expect(indexes).toContain("idx_pj_user");
  });

  it("is idempotent — safe to run twice", () => {
    expect(() => {
      applyV22Migration(db);
      applyV22Migration(db);
    }).not.toThrow();
  });

  it("upgrades a pre-existing proactive_jobs table without dropping data", () => {
    db.exec(`CREATE TABLE proactive_jobs (
      id TEXT PRIMARY KEY, type TEXT NOT NULL, user_id TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 5,
      attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, created_at TEXT NOT NULL
    )`);
    db.prepare(
      `INSERT INTO proactive_jobs (id, type, user_id, scheduled_at, payload, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    ).run("j1", "check_in", "u1", new Date().toISOString(), "{}", new Date().toISOString());

    applyV22Migration(db);

    const row = db.prepare(`SELECT id, retry_count, suppress_count FROM proactive_jobs WHERE id = ?`).get("j1") as any;
    expect(row.id).toBe("j1");
    expect(row.retry_count).toBe(0);
    expect(row.suppress_count).toBe(0);
  });
});
