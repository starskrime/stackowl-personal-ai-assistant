import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let db: MemoryDatabase;
let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "activity-gate-test-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  db.close();
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("ActivityGateRepo", () => {
  it("getHash returns null for unknown jobId", () => {
    const hash = db.activityGate.getHash("desire-execution");
    expect(hash).toBeNull();
  });

  it("setHash then getHash returns the stored value", () => {
    db.activityGate.setHash("desire-execution", "abc123");
    expect(db.activityGate.getHash("desire-execution")).toBe("abc123");
  });

  it("setHash is idempotent — second call overwrites first", () => {
    db.activityGate.setHash("council", "hash1");
    db.activityGate.setHash("council", "hash2");
    expect(db.activityGate.getHash("council")).toBe("hash2");
  });

  it("different jobIds are independent", () => {
    db.activityGate.setHash("council", "aaa");
    db.activityGate.setHash("dream", "bbb");
    expect(db.activityGate.getHash("council")).toBe("aaa");
    expect(db.activityGate.getHash("dream")).toBe("bbb");
  });
});
