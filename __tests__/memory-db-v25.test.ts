import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";

describe("v25 migration", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
  });

  it("creates memories table with all required columns", () => {
    applyV25Migration(db);
    const cols = db.prepare(`PRAGMA table_info(memories)`).all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "id",
        "kind",
        "content",
        "embedding",
        "importance",
        "goal_id",
        "subgoal_id",
        "verdict",
        "source_turn_id",
        "source_channel",
        "valid_at",
        "invalid_at",
        "created_at",
        "updated_at",
        "access_count",
        "last_accessed_at",
      ]),
    );
  });

  it("creates supporting tables", () => {
    applyV25Migration(db);
    const tables = db
      .prepare(`SELECT name FROM sqlite_master WHERE type='table'`)
      .all() as Array<{ name: string }>;
    const names = tables.map((t) => t.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "memories",
        "memory_invalidations",
        "memory_contradictions",
        "memory_access_log",
      ]),
    );
  });

  it("creates required indexes", () => {
    applyV25Migration(db);
    const indexes = db
      .prepare(`SELECT name FROM sqlite_master WHERE type='index'`)
      .all() as Array<{ name: string }>;
    const names = indexes.map((i) => i.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "idx_memories_kind",
        "idx_memories_valid",
        "idx_memories_goal",
        "idx_memories_importance",
        "idx_inv_memory",
        "idx_contra_memory",
        "idx_access_memory",
      ]),
    );
  });

  it("is idempotent", () => {
    applyV25Migration(db);
    expect(() => applyV25Migration(db)).not.toThrow();
    const cnt = db
      .prepare(
        `SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name='memories'`,
      )
      .get() as { c: number };
    expect(cnt.c).toBe(1);
  });

  it("kind CHECK constraint rejects invalid values", () => {
    applyV25Migration(db);
    expect(() => {
      db.prepare(
        `INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run("x", "garbage", "c", 0.5, "2026-01-01", "2026-01-01", "2026-01-01");
    }).toThrow();
  });

  it("verdict CHECK constraint accepts spec values", () => {
    applyV25Migration(db);
    for (const v of ["ADVANCES", "PARTIAL", "BLOCKED", "NEUTRAL"]) {
      expect(() => {
        db.prepare(
          `INSERT INTO memories (id, kind, content, importance, verdict, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        ).run(`v_${v}`, "semantic", "c", 0.5, v, "2026-01-01", "2026-01-01", "2026-01-01");
      }).not.toThrow();
    }
  });

  it("importance CHECK constraint enforces 0..1 range", () => {
    applyV25Migration(db);
    expect(() => {
      db.prepare(
        `INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run("hi", "semantic", "c", 1.5, "2026-01-01", "2026-01-01", "2026-01-01");
    }).toThrow();
    expect(() => {
      db.prepare(
        `INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run("lo", "semantic", "c", -0.1, "2026-01-01", "2026-01-01", "2026-01-01");
    }).toThrow();
  });
});
