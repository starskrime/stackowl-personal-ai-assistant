import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { migrateJsonSessionsToSQLite } from "../src/session/migrate.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// Minimal SessionStore mock — matches the async API of the real SessionStore
function makeSessionStore(sessions: Record<string, any>) {
  return {
    listSessions: async () => Object.values(sessions),
    loadSession: async (id: string) => sessions[id] ?? null,
    deleteSession: async (id: string) => {
      delete sessions[id];
    },
  };
}

describe("migrateJsonSessionsToSQLite", () => {
  let tmpDir: string;
  let db: MemoryDatabase;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "owl-migrate-"));
    db = new MemoryDatabase(tmpDir);
  });

  afterEach(() => {
    db.close?.();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("migrates messages from JSON session to SQLite", async () => {
    const sessions: Record<string, any> = {
      "cli:user1": {
        id: "cli:user1",
        messages: [
          { role: "user", content: "hello" },
          { role: "assistant", content: "hi there" },
        ],
        metadata: {
          owlName: "hoot",
          startedAt: Date.now(),
          lastUpdatedAt: Date.now(),
        },
      },
    };
    const store = makeSessionStore(sessions);

    await migrateJsonSessionsToSQLite(store as any, db, "hoot");

    expect(db.messages.countSession("cli:user1")).toBe(2);
    expect(Object.keys(sessions)).toHaveLength(0); // deleted after migration
  });

  it("skips sessions already in SQLite", async () => {
    // pre-populate SQLite
    db.messages.append("cli:user1", "user1", "hoot", [
      { role: "user", content: "already there" },
    ]);

    const deleted: string[] = [];
    const store = {
      listSessions: async () => [
        {
          id: "cli:user1",
          messages: [{ role: "user", content: "already there" }],
          metadata: { owlName: "hoot", startedAt: Date.now(), lastUpdatedAt: Date.now() },
        },
      ],
      loadSession: async (id: string) => null,
      deleteSession: async (id: string) => {
        deleted.push(id);
      },
    };

    await migrateJsonSessionsToSQLite(store as any, db, "hoot");

    // should skip — not deleted (no migration attempt)
    expect(deleted).toHaveLength(0);
    expect(db.messages.countSession("cli:user1")).toBe(1); // unchanged
  });

  it("skips and deletes empty sessions", async () => {
    const sessions: Record<string, any> = {
      "cli:user1": {
        id: "cli:user1",
        messages: [],
        metadata: { owlName: "hoot", startedAt: Date.now(), lastUpdatedAt: Date.now() },
      },
    };
    const store = makeSessionStore(sessions);

    await migrateJsonSessionsToSQLite(store as any, db, "hoot");

    expect(db.messages.countSession("cli:user1")).toBe(0);
    expect(Object.keys(sessions)).toHaveLength(0);
  });

  it("handles no sessions gracefully", async () => {
    const store = makeSessionStore({});
    await expect(
      migrateJsonSessionsToSQLite(store as any, db, "hoot")
    ).resolves.not.toThrow();
  });
});
