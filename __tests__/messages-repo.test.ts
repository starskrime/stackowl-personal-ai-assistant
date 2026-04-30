import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

describe("MessagesRepo – getOldestN and deleteByIds", () => {
  let tmpDir: string;
  let db: MemoryDatabase;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "owl-test-"));
    db = new MemoryDatabase(tmpDir);
  });

  afterEach(() => {
    db.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  function seedMessages(sessionId: string, count: number) {
    const msgs = Array.from({ length: count }, (_, i) => ({
      role: "user" as const,
      content: `message ${i + 1}`,
    }));
    db.messages.append(sessionId, "user1", "owl1", msgs);
  }

  it("getOldestN returns correct count in seq order", () => {
    seedMessages("s1", 5);
    const result = db.messages.getOldestN("s1", 3);
    expect(result).toHaveLength(3);
    expect(result[0].seq).toBeLessThan(result[1].seq);
    expect(result[1].seq).toBeLessThan(result[2].seq);
  });

  it("getOldestN returns all when n > total count", () => {
    seedMessages("s1", 3);
    const result = db.messages.getOldestN("s1", 10);
    expect(result).toHaveLength(3);
  });

  it("getOldestN returns empty array for missing session", () => {
    const result = db.messages.getOldestN("nonexistent", 5);
    expect(result).toHaveLength(0);
  });

  it("deleteByIds removes the correct rows", () => {
    seedMessages("s1", 5);
    const oldest = db.messages.getOldestN("s1", 2);
    const idsToDelete = oldest.map((r) => r.id);
    db.messages.deleteByIds(idsToDelete);
    expect(db.messages.countSession("s1")).toBe(3);
  });

  it("deleteByIds with empty array is a no-op", () => {
    seedMessages("s1", 3);
    expect(() => db.messages.deleteByIds([])).not.toThrow();
    expect(db.messages.countSession("s1")).toBe(3);
  });
});
