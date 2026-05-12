import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let dir: string;

beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "stackowl-sessions-schema-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("sessions schema", () => {
  it("sessions table exists after MemoryDatabase init", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
      .get();
    expect(row).toBeTruthy();
  });

  it("session_messages table exists", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='session_messages'")
      .get();
    expect(row).toBeTruthy();
  });

  it("sessions table has all expected columns", () => {
    const db = new MemoryDatabase(dir);
    const cols = db.rawDb.prepare("PRAGMA table_info(sessions)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toEqual(expect.arrayContaining([
      "id", "parent_id", "status", "prompt", "history_json",
      "result", "error", "metadata", "created_at", "updated_at", "terminated_at",
    ]));
  });

  it("status check constraint rejects invalid values", () => {
    const db = new MemoryDatabase(dir);
    expect(() => {
      db.rawDb.prepare(
        "INSERT INTO sessions (id, status, prompt) VALUES (?, ?, ?)"
      ).run("bad", "not_a_status", "test");
    }).toThrow();
  });

  it("session_messages.session_id FK cascades on delete", () => {
    const db = new MemoryDatabase(dir);
    db.rawDb.prepare(
      "INSERT INTO sessions (id, status, prompt) VALUES (?, ?, ?)"
    ).run("s1", "running", "test");
    db.rawDb.prepare(
      "INSERT INTO session_messages (session_id, direction, content) VALUES (?, ?, ?)"
    ).run("s1", "to_session", "hi");

    db.rawDb.prepare("DELETE FROM sessions WHERE id = ?").run("s1");
    const remaining = db.rawDb.prepare(
      "SELECT COUNT(*) as n FROM session_messages WHERE session_id = ?"
    ).get("s1") as { n: number };
    expect(remaining.n).toBe(0);
  });
});
