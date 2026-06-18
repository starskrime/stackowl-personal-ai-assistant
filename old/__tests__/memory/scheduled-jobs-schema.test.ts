import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let dir: string;

beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "stackowl-sched-schema-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("scheduled_jobs schema", () => {
  it("table exists after MemoryDatabase init", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
      .get();
    expect(row).toBeTruthy();
  });

  it("table has the expected columns", () => {
    const db = new MemoryDatabase(dir);
    const cols = db.rawDb.prepare("PRAGMA table_info(scheduled_jobs)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toEqual(expect.arrayContaining([
      "id", "type", "message", "schedule_at", "interval_ms", "next_fire_at",
      "created_at", "status", "metadata",
    ]));
  });

  it("insert + query a job", () => {
    const db = new MemoryDatabase(dir);
    db.rawDb.prepare(`
      INSERT INTO scheduled_jobs (id, type, message, next_fire_at, status, metadata)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run("j1", "remind", "test", new Date().toISOString(), "active", "{}");
    const row = db.rawDb.prepare("SELECT * FROM scheduled_jobs WHERE id = ?").get("j1") as any;
    expect(row.type).toBe("remind");
  });
});
