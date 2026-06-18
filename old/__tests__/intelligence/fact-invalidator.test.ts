import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { FactInvalidator } from "../../src/intelligence/fact-invalidator.js";

describe("FactInvalidator", () => {
  let db: InstanceType<typeof Database>;

  function insertFact(id: string, text: string, embedding: number[]) {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at, embedding)
      VALUES (?, 'u1', 'aria', ?, 'personal', 0.9, 'explicit', 0, datetime('now'), datetime('now'), ?)
    `).run(id, text, JSON.stringify(embedding));
  }

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("invalidates old location fact when new location extracted with temporal trigger", async () => {
    insertFact("f1", "User lives in London", [0.9, 0.1, 0.0, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.9, 0.1, 0.0, 0.0];

    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f1'").get() as any;
    expect(row.invalidated_at).not.toBeNull();
  });

  it("does NOT invalidate when no temporal trigger present", async () => {
    insertFact("f2", "User likes TypeScript", [0.8, 0.1, 0.1, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.8, 0.1, 0.1, 0.0];

    await invalidator.check("User prefers TypeScript over JavaScript", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f2'").get() as any;
    expect(row.invalidated_at).toBeNull();
  });

  it("does NOT invalidate when similarity is below threshold", async () => {
    insertFact("f3", "User lives in London", [0.9, 0.1, 0.0, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.1, 0.9, 0.0, 0.0]; // different vector

    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f3'").get() as any;
    expect(row.invalidated_at).toBeNull();
  });
});
