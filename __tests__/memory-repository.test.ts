import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";

describe("MemoryRepository — skeleton", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("constructs with a Database handle", () => {
    expect(repo).toBeInstanceOf(MemoryRepository);
  });

  it("exposes the canonical surface", () => {
    expect(typeof repo.search).toBe("function");
    expect(typeof repo.insertBatch).toBe("function");
    expect(typeof repo.invalidate).toBe("function");
    expect(typeof repo.getById).toBe("function");
    expect(typeof repo.history).toBe("function");
    expect(typeof repo.recordAccess).toBe("function");
    expect(typeof repo.stats).toBe("function");
  });
});
