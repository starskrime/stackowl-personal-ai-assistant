import { describe, it, expect, afterEach } from "vitest";
import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { applyMigrations, MemoryDatabase } from "../../src/memory/db.js";

describe("Schema v21 — pellet_generation_runs", () => {
  it("creates pellet_generation_runs table on fresh DB", () => {
    const db = new Database(":memory:");
    applyMigrations(db);
    const row = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='pellet_generation_runs'").get();
    expect(row).toBeTruthy();
  });

  it("migration is idempotent on existing DB", () => {
    const db = new Database(":memory:");
    applyMigrations(db);
    expect(() => applyMigrations(db)).not.toThrow();
  });
});

describe("MemoryDatabase.getPelletGenRun / setPelletGenRun", () => {
  let tmpDir: string;
  let mdb: MemoryDatabase;

  function setup() {
    tmpDir = mkdtempSync(join(tmpdir(), "owl-pellet-gen-"));
    mdb = new MemoryDatabase(tmpDir);
  }

  afterEach(() => {
    mdb.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns null for unknown key", async () => {
    setup();
    const result = await mdb.getPelletGenRun("council");
    expect(result).toBeNull();
  });

  it("stores and retrieves run time", async () => {
    setup();
    const now = new Date("2026-05-03T12:00:00Z");
    await mdb.setPelletGenRun("council", now);
    const result = await mdb.getPelletGenRun("council");
    expect(result?.toISOString()).toBe(now.toISOString());
  });
});
