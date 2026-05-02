import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";

describe("schema v17 migration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("creates owl_task_ledger table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_task_ledger'"
    ).get();
    expect(row).toBeDefined();
  });

  it("creates reflexion_critiques table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='reflexion_critiques'"
    ).get();
    expect(row).toBeDefined();
  });

  it("creates skill_templates table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_templates'"
    ).get();
    expect(row).toBeDefined();
  });

  it("facts table has invalidated_at column", () => {
    const cols = db.prepare("PRAGMA table_info(facts)").all() as { name: string }[];
    expect(cols.map(c => c.name)).toContain("invalidated_at");
  });

  it("outcome_journal table has challenge_instances column", () => {
    const cols = db.prepare("PRAGMA table_info(outcome_journal)").all() as { name: string }[];
    expect(cols.map(c => c.name)).toContain("challenge_instances");
  });
});
