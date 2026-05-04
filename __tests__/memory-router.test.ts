import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { dispatchMemoryCommand } from "../src/gateway/commands/memory-router.js";

describe("dispatchMemoryCommand", () => {
  let db: import("better-sqlite3").Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("/memory list returns formatted output for empty db", async () => {
    const out = await dispatchMemoryCommand("list", [], { repo });
    expect(out).toContain("0 memories");
  });

  it("/memory list summarizes recent memories", async () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "user likes TypeScript", importance: 0.7 },
      { id: "b", kind: "episodic", content: "shipped Element 14", importance: 0.5 },
    ]);
    const out = await dispatchMemoryCommand("list", [], { repo });
    expect(out).toContain("2 memories");
    expect(out).toContain("[semantic]");
    expect(out).toContain("[episodic]");
  });

  it("/memory search <query>", async () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "user likes TypeScript", importance: 0.7 },
    ]);
    const out = await dispatchMemoryCommand("search", ["TypeScript"], { repo });
    expect(out).toContain("TypeScript");
  });

  it("/memory search with empty args returns usage", async () => {
    const out = await dispatchMemoryCommand("search", [], { repo });
    expect(out.toLowerCase()).toContain("usage");
  });

  it("/memory stats", async () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "x", importance: 0.5 },
      { id: "b", kind: "episodic", content: "y", importance: 0.5 },
    ]);
    const out = await dispatchMemoryCommand("stats", [], { repo });
    expect(out).toContain("Total: 2");
    expect(out).toContain("semantic: 1");
    expect(out).toContain("episodic: 1");
  });

  it("/memory invalidate <id> <reason> works", async () => {
    repo.insertBatch([
      { id: "x", kind: "semantic", content: "stale fact", importance: 0.3 },
    ]);
    const out = await dispatchMemoryCommand("invalidate", ["x", "user", "corrected"], { repo });
    expect(out.toLowerCase()).toContain("invalidated");
    expect(repo.getById("x")?.invalid_at).not.toBeNull();
  });

  it("/memory invalidate without reason returns usage", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "fact", importance: 0.3 }]);
    const out = await dispatchMemoryCommand("invalidate", ["x"], { repo });
    expect(out.toLowerCase()).toContain("usage");
  });

  it("/memory history <id>", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "fact", importance: 0.5 }]);
    repo.invalidate("x", { reason: "test", invalidatedBy: "tester" });
    const out = await dispatchMemoryCommand("history", ["x"], { repo });
    expect(out).toContain("test");
  });

  it("/memory history without id returns usage", async () => {
    const out = await dispatchMemoryCommand("history", [], { repo });
    expect(out.toLowerCase()).toContain("usage");
  });

  it("/memory get <id>", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "fact", importance: 0.5 }]);
    const out = await dispatchMemoryCommand("get", ["x"], { repo });
    expect(out).toContain("fact");
  });

  it("/memory get unknown returns not-found", async () => {
    const out = await dispatchMemoryCommand("get", ["nope"], { repo });
    expect(out).toContain("not found");
  });

  it("/memory export emits JSON", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "fact", importance: 0.5 }]);
    const out = await dispatchMemoryCommand("export", [], { repo });
    const parsed = JSON.parse(out);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed[0].id).toBe("x");
  });

  it("/memory unknown verb returns help text", async () => {
    const out = await dispatchMemoryCommand("frobnicate", [], { repo });
    expect(out).toContain("/memory list");
    expect(out).toContain("/memory search");
  });
});
