import { describe, it, expect, beforeEach } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import Database from "better-sqlite3";
import { applyV25Migration, backupBeforeV25 } from "../src/memory/db.js";

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

describe("v25 migration — backup", () => {
  it("creates a backup file before applying when given a file-backed db path", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "v25-"));
    const dbPath = path.join(tmp, "memory.db");
    const db = new Database(dbPath);
    db.pragma("journal_mode = WAL");
    db.exec(`CREATE TABLE legacy (id TEXT PRIMARY KEY); INSERT INTO legacy VALUES ('a');`);
    db.close();

    const db2 = new Database(dbPath);
    db2.pragma("journal_mode = WAL");
    const backupPath = backupBeforeV25(dbPath);
    applyV25Migration(db2);

    expect(backupPath).not.toBeNull();
    expect(fs.existsSync(backupPath as string)).toBe(true);
    expect(backupPath).toContain(".v24-backup");
    db2.close();
    fs.rmSync(tmp, { recursive: true });
  });

  it("backupBeforeV25 returns null for null path (in-memory db)", () => {
    expect(backupBeforeV25(null)).toBeNull();
  });

  it("backupBeforeV25 returns null when source file is missing", () => {
    const missing = path.join(os.tmpdir(), `does-not-exist-${Date.now()}.db`);
    expect(backupBeforeV25(missing)).toBeNull();
  });
});

describe("v25 migration — legacy data merge", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
  });

  it("merges legacy `facts` rows into memories with kind='semantic'", () => {
    db.exec(`
      CREATE TABLE facts (
        id TEXT PRIMARY KEY,
        user_id TEXT, owl_name TEXT, fact TEXT NOT NULL,
        entity TEXT, category TEXT, confidence REAL,
        source TEXT, embedding TEXT, access_count INTEGER,
        expires_at TEXT, created_at TEXT, updated_at TEXT, invalidated_at TEXT
      );
      INSERT INTO facts (id, fact, confidence, created_at, updated_at)
        VALUES ('f1','user prefers concise replies', 0.7, '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT * FROM memories WHERE id = 'f1'`).get() as
      | { kind: string; content: string; importance: number; invalid_at: string | null }
      | undefined;
    expect(row).toBeDefined();
    expect(row?.kind).toBe("semantic");
    expect(row?.content).toBe("user prefers concise replies");
    expect(row?.importance).toBe(0.7);
    expect(row?.invalid_at).toBeNull();
  });

  it("preserves invalidated_at on `facts` merge", () => {
    db.exec(`
      CREATE TABLE facts (
        id TEXT PRIMARY KEY, user_id TEXT, owl_name TEXT, fact TEXT NOT NULL,
        entity TEXT, category TEXT, confidence REAL, source TEXT, embedding TEXT,
        access_count INTEGER, expires_at TEXT, created_at TEXT, updated_at TEXT,
        invalidated_at TEXT
      );
      INSERT INTO facts (id, fact, confidence, created_at, updated_at, invalidated_at)
        VALUES ('f2','old fact', 0.5, '2026-01-01','2026-01-01','2026-02-01');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT invalid_at FROM memories WHERE id = 'f2'`).get() as
      | { invalid_at: string }
      | undefined;
    expect(row?.invalid_at).toBe("2026-02-01");
  });

  it("merges legacy `episodes` rows into memories with kind='episodic'", () => {
    db.exec(`
      CREATE TABLE episodes (
        id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, owl_name TEXT,
        summary TEXT NOT NULL, key_facts TEXT, topics TEXT, sentiment TEXT,
        importance REAL, embedding TEXT, created_at TEXT
      );
      INSERT INTO episodes (id, summary, importance, created_at)
        VALUES ('e1','user worked on element 12', 0.6, '2026-04-30T00:00:00Z');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT * FROM memories WHERE id = 'e1'`).get() as
      | { kind: string; content: string; importance: number }
      | undefined;
    expect(row?.kind).toBe("episodic");
    expect(row?.content).toBe("user worked on element 12");
    expect(row?.importance).toBe(0.6);
  });

  it("merges legacy `pellets` rows into memories with kind='semantic'", () => {
    db.exec(`
      CREATE TABLE pellets (
        id TEXT PRIMARY KEY, tag TEXT, title TEXT, content TEXT NOT NULL, created_at TEXT
      );
      INSERT INTO pellets (id, content, created_at) VALUES ('p1','synthesis output', '2026-01-01');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT kind FROM memories WHERE id = 'p1'`).get() as
      | { kind: string }
      | undefined;
    expect(row?.kind).toBe("semantic");
  });

  it("merges legacy `summaries` rows into memories with kind='episodic'", () => {
    db.exec(`
      CREATE TABLE summaries (
        id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, owl_name TEXT,
        from_seq INTEGER, to_seq INTEGER, message_count INTEGER,
        summary_text TEXT NOT NULL, task TEXT, accomplished TEXT, key_facts TEXT,
        decisions TEXT, failed_approaches TEXT, open_questions TEXT,
        tokens_saved INTEGER, created_at TEXT
      );
      INSERT INTO summaries (id, summary_text, created_at)
        VALUES ('s1','session summary', '2026-04-29');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT kind, content FROM memories WHERE id = 's1'`).get() as
      | { kind: string; content: string }
      | undefined;
    expect(row?.kind).toBe("episodic");
    expect(row?.content).toBe("session summary");
  });

  it("legacy merge is idempotent (running twice does not duplicate rows)", () => {
    db.exec(`
      CREATE TABLE facts (
        id TEXT PRIMARY KEY, fact TEXT NOT NULL, confidence REAL,
        created_at TEXT, updated_at TEXT, invalidated_at TEXT
      );
      INSERT INTO facts (id, fact, confidence, created_at, updated_at)
        VALUES ('f1','x', 0.5, '2026-01-01','2026-01-01');
    `);
    applyV25Migration(db);
    applyV25Migration(db);
    const cnt = db.prepare(`SELECT COUNT(*) AS c FROM memories WHERE id = 'f1'`).get() as {
      c: number;
    };
    expect(cnt.c).toBe(1);
  });

  it("skips merge when no legacy tables exist", () => {
    expect(() => applyV25Migration(db)).not.toThrow();
    const cnt = db.prepare(`SELECT COUNT(*) AS c FROM memories`).get() as { c: number };
    expect(cnt.c).toBe(0);
  });

  it("leaves legacy tables intact after merge (non-destructive)", () => {
    db.exec(`
      CREATE TABLE facts (
        id TEXT PRIMARY KEY, fact TEXT NOT NULL, confidence REAL,
        created_at TEXT, updated_at TEXT, invalidated_at TEXT
      );
      INSERT INTO facts (id, fact, confidence, created_at, updated_at)
        VALUES ('f1','keep me', 0.5, '2026-01-01','2026-01-01');
    `);
    applyV25Migration(db);
    const legacyRow = db.prepare(`SELECT id, fact FROM facts WHERE id = 'f1'`).get() as
      | { id: string; fact: string }
      | undefined;
    expect(legacyRow?.id).toBe("f1");
    expect(legacyRow?.fact).toBe("keep me");
  });
});
