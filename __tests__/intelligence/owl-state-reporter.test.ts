import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { OwlStateReporter } from "../../src/intelligence/owl-state-reporter.js";

describe("OwlStateReporter", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db as any);
  });

  afterEach(() => db.close());

  it("reports zero counts when db is empty", async () => {
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("Memory:");
    expect(report).toContain("0 facts");
    expect(report).toContain("0 pellets");
  });

  it("includes fact count when facts exist", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at)
      VALUES ('f1', 'u1', 'aria', 'user likes TypeScript', 'preference', 0.9, 'explicit', 0, datetime('now'), datetime('now'))
    `).run();
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("1 fact");
  });

  it("includes active task when in_progress task exists", async () => {
    db.prepare(`
      INSERT INTO owl_task_ledger (id, session_id, user_id, task_id, subgoal_index, subgoal_text, state_json, status, attempt_count, created_at)
      VALUES ('l1', 's1', 'u1', 't1', 1, 'Search TypeScript docs', '{}', 'in_progress', 2, datetime('now'))
    `).run();
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("Active task");
    expect(report).toContain("Search TypeScript docs");
  });
});
