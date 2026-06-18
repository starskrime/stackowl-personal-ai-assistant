import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string;
let db: MemoryDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-v14-"));
  db = new MemoryDatabase(dir);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("schema v14", () => {
  it("task_ledgers table exists", () => {
    const raw = (db as any).db ?? (db as any).rawDb;
    const row = raw.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='task_ledgers'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("hitl_checkpoints table exists", () => {
    const raw = (db as any).db ?? (db as any).rawDb;
    const row = raw.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_checkpoints'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("approach_patterns table exists", () => {
    const raw = (db as any).db ?? (db as any).rawDb;
    const row = raw.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='approach_patterns'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("trajectories has quality_score column", () => {
    const raw = (db as any).db ?? (db as any).rawDb;
    const info = raw.prepare("PRAGMA table_info(trajectories)").all() as {name:string}[];
    const cols = info.map((c: {name:string}) => c.name);
    expect(cols).toContain("quality_score");
    expect(cols).toContain("task_category");
    expect(cols).toContain("degradation_tier");
    expect(cols).toContain("follow_up_sentiment");
  });

  it("schema version is at least 22", () => {
    const raw = (db as any).db ?? (db as any).rawDb;
    const v = (raw.pragma("user_version") as {user_version:number}[])[0]?.user_version;
    expect(v).toBeGreaterThanOrEqual(22);
  });
});
