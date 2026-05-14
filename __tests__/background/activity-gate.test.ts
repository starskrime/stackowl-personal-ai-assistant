import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ActivityGate } from "../../src/background/activity-gate.js";
import { randomUUID } from "node:crypto";

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

describe("ActivityGate", () => {
  let gate: ActivityGate;

  beforeEach(() => {
    gate = new ActivityGate(db);
  });

  it("hasNewActivity returns false when no messages exist and job has never run", async () => {
    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(false);
  });

  it("hasNewActivity returns true when a user message exists but job never ran", async () => {
    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello world", 1, new Date().toISOString());

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(true);
  });

  it("hasNewActivity returns false when hash matches last seen", async () => {
    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello world", 1, new Date().toISOString());

    await gate.markSeen("desire-execution");

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(false);
  });

  it("hasNewActivity returns true after a new user message arrives", async () => {
    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "first message", 1, new Date(Date.now() - 1000).toISOString());

    await gate.markSeen("desire-execution");

    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "second message", 2, new Date().toISOString());

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(true);
  });

  it("markSeen updates the stored hash so next hasNewActivity returns false", async () => {
    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello", 1, new Date().toISOString());

    expect(await gate.hasNewActivity("council")).toBe(true);
    await gate.markSeen("council");
    expect(await gate.hasNewActivity("council")).toBe(false);
  });

  it("different jobIds are independent", async () => {
    (db as any)["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello", 1, new Date().toISOString());

    await gate.markSeen("council");

    expect(await gate.hasNewActivity("dream")).toBe(true);
    expect(await gate.hasNewActivity("council")).toBe(false);
  });
});
