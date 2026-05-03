// __tests__/memory/schema-v18.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";

describe("schema v18 migration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => { db = new Database(":memory:"); });
  afterEach(() => { db.close(); });

  it("creates post_processor_job_runs table with correct columns", () => {
    applyAllMigrationsToRawDb(db);
    const cols = db.prepare(
      "PRAGMA table_info(post_processor_job_runs)"
    ).all() as { name: string }[];
    const names = cols.map(c => c.name);
    expect(names).toContain("job_name");
    expect(names).toContain("tier");
    expect(names).toContain("success");
    expect(names).toContain("error_code");
    expect(names).toContain("duration_ms");
    expect(names).toContain("user_id");
    expect(names).toContain("session_id");
    expect(names).toContain("ts");
  });

  it("creates idx_ppjr_job_ts and idx_ppjr_success indexes", () => {
    applyAllMigrationsToRawDb(db);
    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='post_processor_job_runs'"
    ).all() as { name: string }[];
    const names = indexes.map(i => i.name);
    expect(names).toContain("idx_ppjr_job_ts");
    expect(names).toContain("idx_ppjr_success");
  });

  it("migration is idempotent — running twice does not throw", () => {
    applyAllMigrationsToRawDb(db);
    expect(() => applyAllMigrationsToRawDb(db)).not.toThrow();
  });
});
