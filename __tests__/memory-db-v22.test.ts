import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import Database from "better-sqlite3";
import { applyV22Migration, MemoryDatabase } from "../src/memory/db.js";

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

    // All four ALTER paths must fire on a legacy v21 table.
    const row = db.prepare(
      `SELECT id, retry_count, suppress_count, goal_id, error FROM proactive_jobs WHERE id = ?`
    ).get("j1") as any;
    expect(row.id).toBe("j1");
    expect(row.retry_count).toBe(0);
    expect(row.suppress_count).toBe(0);
    expect(row.goal_id).toBeNull();
    expect(row.error).toBeNull();
  });

  it("is idempotent at column granularity — partial pre-existing columns", () => {
    // Simulate a half-migrated DB (e.g. from a crashed prior run before
    // the migration was wrapped in a transaction). retry_count already
    // present, the rest missing. The guarded ALTERs must skip retry_count
    // and add the others without throwing.
    db.exec(`CREATE TABLE proactive_jobs (
      id TEXT PRIMARY KEY, type TEXT NOT NULL, user_id TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 5,
      attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, created_at TEXT NOT NULL,
      retry_count INTEGER NOT NULL DEFAULT 7
    )`);

    expect(() => applyV22Migration(db)).not.toThrow();

    const cols = (db.prepare(`PRAGMA table_info(proactive_jobs)`).all() as { name: string; dflt_value: string | null }[]);
    const retryCount = cols.find(c => c.name === "retry_count");
    // Existing column kept its DEFAULT of 7, not clobbered to 0.
    expect(retryCount?.dflt_value).toBe("7");
    expect(cols.map(c => c.name)).toEqual(
      expect.arrayContaining(["suppress_count", "goal_id", "error"])
    );
  });

  it("enforces replied IN (0, 1) on proactive_engagement", () => {
    applyV22Migration(db);
    expect(() =>
      db.prepare(
        `INSERT INTO proactive_engagement
           (id, delivery_id, job_type, goal_id, replied, reply_latency_seconds, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).run("e1", "d1", "check_in", null, 2, null, new Date().toISOString())
    ).toThrow(/CHECK constraint failed/);
  });
});

describe("schema v22 — writeProactiveDelivery ON CONFLICT semantics", () => {
  let tmp: string;
  let memdb: MemoryDatabase;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "stackowl-v22-"));
    memdb = new MemoryDatabase(tmp);
  });

  afterEach(() => {
    memdb.close();
    rmSync(tmp, { recursive: true, force: true });
  });

  it("preserves user_replied_at when re-delivery overwrites status/verdict", () => {
    memdb.writeProactiveDelivery({
      id: "d1", jobId: "j1", channel: "telegram", userId: "u1",
      messagePreview: "first", verdict: "PROCEED",
      deliveredAt: new Date().toISOString(), status: "delivered",
    });

    // Simulate a later UPDATE recording user engagement.
    memdb.rawDb
      .prepare(`UPDATE proactive_deliveries SET user_replied_at = ? WHERE id = ?`)
      .run("2026-05-03T12:00:00.000Z", "d1");

    // Second write for the same id (e.g. re-delivery / status refresh).
    memdb.writeProactiveDelivery({
      id: "d1", jobId: "j1", channel: "telegram", userId: "u1",
      messagePreview: "second", verdict: "SUPPRESS",
      deliveredAt: new Date().toISOString(), status: "redelivered",
    });

    const row = memdb.rawDb.prepare(
      `SELECT status, verdict, message_preview, user_replied_at FROM proactive_deliveries WHERE id = ?`
    ).get("d1") as any;

    expect(row.status).toBe("redelivered");
    expect(row.verdict).toBe("SUPPRESS");
    expect(row.message_preview).toBe("second");
    // The critical assertion: prior reply timestamp survives the conflict path.
    expect(row.user_replied_at).toBe("2026-05-03T12:00:00.000Z");
  });
});
