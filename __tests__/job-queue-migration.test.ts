import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { ProactiveJobQueue, migrateJobsDb } from "../src/heartbeat/job-queue.js";

describe("ProactiveJobQueue with external DB", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
  });

  afterEach(() => { db.close(); });

  it("accepts a Database instance instead of workspace path", () => {
    const queue = new ProactiveJobQueue(db);
    expect(() =>
      queue.schedule({
        type: "morning_brief",
        userId: "user1",
        scheduledAt: new Date(),
      })
    ).not.toThrow();
  });

  it("getDueJobs returns scheduled jobs from injected DB", () => {
    const queue = new ProactiveJobQueue(db);
    queue.schedule({
      type: "check_in",
      userId: "user1",
      scheduledAt: new Date(Date.now() - 1000),
    });
    const due = queue.getDueJobs();
    expect(due.length).toBe(1);
    expect(due[0].type).toBe("check_in");
  });
});

describe("migrateJobsDb", () => {
  it("is a no-op when old DB path does not exist", () => {
    const mainDb = new Database(":memory:");
    mainDb.pragma("journal_mode = WAL");
    mainDb.exec(`CREATE TABLE IF NOT EXISTS proactive_jobs (
      id TEXT PRIMARY KEY, type TEXT NOT NULL, user_id TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 5,
      attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT,
      error TEXT, retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
    )`);
    expect(() => migrateJobsDb("/nonexistent/path", mainDb)).not.toThrow();
    mainDb.close();
  });
});
