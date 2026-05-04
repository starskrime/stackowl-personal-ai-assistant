import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { v4 as uuid } from "uuid";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";
import { GatewayEventBus, type GatewaySystemEvent } from "../src/gateway/event-bus.js";

function makeEmbedding(seed: number): Float32Array {
  const arr = new Float32Array(8);
  for (let i = 0; i < 8; i++) arr[i] = Math.sin(seed + i);
  return arr;
}

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

describe("MemoryRepository.search", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("returns empty array when no memories", async () => {
    const results = await repo.search("anything");
    expect(results).toEqual([]);
  });

  it("filters by kind", async () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "user prefers concise answers", importance: 0.7, embedding: makeEmbedding(1) },
      { id: uuid(), kind: "episodic", content: "user worked on element 12 yesterday", importance: 0.5, embedding: makeEmbedding(2) },
    ]);
    const results = await repo.search("preference", { kinds: ["semantic"], topK: 10 });
    expect(results).toHaveLength(1);
    expect(results[0].kind).toBe("semantic");
  });

  it("excludes invalidated memories by default", async () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "old fact", importance: 0.5, embedding: makeEmbedding(3) }]);
    repo.invalidate(id, { reason: "user corrected", invalidatedBy: "test" });
    const results = await repo.search("old", { topK: 10 });
    expect(results.find((r) => r.id === id)).toBeUndefined();
  });

  it("includes invalidated memories when includeInvalid=true", async () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "old fact", importance: 0.5, embedding: makeEmbedding(3) }]);
    repo.invalidate(id, { reason: "user corrected", invalidatedBy: "test" });
    const results = await repo.search("old", { topK: 10, includeInvalid: true });
    expect(results.find((r) => r.id === id)).toBeDefined();
  });

  it("re-ranks by α·recency + β·importance + γ·relevance", async () => {
    const oldId = uuid();
    const newId = uuid();
    repo.insertBatch([
      { id: oldId, kind: "semantic", content: "user likes typescript", importance: 0.5, embedding: makeEmbedding(10) },
    ]);
    await new Promise((r) => setTimeout(r, 10));
    repo.insertBatch([
      { id: newId, kind: "semantic", content: "user likes typescript", importance: 0.5, embedding: makeEmbedding(10) },
    ]);
    const results = await repo.search("typescript", { topK: 2 });
    expect(results[0].id).toBe(newId);
  });

  it("respects minImportance filter", async () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "low importance", importance: 0.2, embedding: makeEmbedding(20) },
      { id: uuid(), kind: "semantic", content: "high importance", importance: 0.9, embedding: makeEmbedding(20) },
    ]);
    const results = await repo.search("importance", { topK: 10, minImportance: 0.5 });
    expect(results).toHaveLength(1);
    expect(results[0].importance).toBeGreaterThanOrEqual(0.5);
  });
});

describe("MemoryRepository.getById / history / recordAccess / stats", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("getById returns the record", () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "x", importance: 0.5 }]);
    const r = repo.getById(id);
    expect(r?.id).toBe(id);
  });

  it("getById returns null for missing", () => {
    expect(repo.getById("nope")).toBeNull();
  });

  it("history returns invalidations + contradictions", () => {
    const id = uuid();
    const cId = uuid();
    repo.insertBatch([
      { id, kind: "semantic", content: "old", importance: 0.5 },
      { id: cId, kind: "semantic", content: "contradicts old", importance: 0.5 },
    ]);
    repo.invalidate(id, { reason: "contradicted", invalidatedBy: "writer", contradicts: [cId] });
    const h = repo.history(id);
    expect(h.invalidations).toHaveLength(1);
    expect(h.contradictions).toHaveLength(1);
  });

  it("recordAccess increments access_count + updates last_accessed_at", () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "x", importance: 0.5 }]);
    repo.recordAccess(id);
    repo.recordAccess(id);
    const r = repo.getById(id);
    expect(r?.access_count).toBe(2);
    expect(r?.last_accessed_at).not.toBeNull();
  });

  it("stats returns counts by kind + invalidated + avg importance", () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "a", importance: 0.4 },
      { id: uuid(), kind: "semantic", content: "b", importance: 0.6 },
      { id: uuid(), kind: "episodic", content: "c", importance: 0.8 },
    ]);
    const s = repo.stats();
    expect(s.total).toBe(3);
    expect(s.byKind.semantic).toBe(2);
    expect(s.byKind.episodic).toBe(1);
    expect(s.avgImportance).toBeCloseTo(0.6, 2);
    expect(s.invalidated).toBe(0);
  });
});

describe("MemoryRepository.insertBatch — validation & upsert", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("rejects importance > 1", () => {
    expect(() =>
      repo.insertBatch([{ id: "x", kind: "semantic", content: "c", importance: 1.5 }]),
    ).toThrow();
  });

  it("rejects importance < 0", () => {
    expect(() =>
      repo.insertBatch([{ id: "x", kind: "semantic", content: "c", importance: -0.1 }]),
    ).toThrow();
  });

  it("upserts on conflicting id (replaces content + bumps updated_at)", async () => {
    const id = "u1";
    repo.insertBatch([{ id, kind: "semantic", content: "first", importance: 0.5 }]);
    const before = repo.getById(id)!;
    await new Promise((r) => setTimeout(r, 10));
    repo.insertBatch([{ id, kind: "semantic", content: "second", importance: 0.6 }]);
    const after = repo.getById(id)!;
    expect(after.content).toBe("second");
    expect(after.importance).toBe(0.6);
    expect(after.updated_at).not.toBe(before.updated_at);
    expect(after.created_at).toBe(before.created_at);
  });

  it("transaction rolls back on partial failure (rejects whole batch on bad row)", () => {
    expect(() =>
      repo.insertBatch([
        { id: "ok", kind: "semantic", content: "ok", importance: 0.5 },
        { id: "bad", kind: "semantic", content: "x", importance: 2.0 },
      ]),
    ).toThrow();
    expect(repo.getById("ok")).toBeNull();
    expect(repo.getById("bad")).toBeNull();
  });
});

describe("MemoryRepository — events", () => {
  let db: Database.Database;
  let bus: GatewayEventBus;
  let repo: MemoryRepository;
  let captured: GatewaySystemEvent[];

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    captured = [];
    bus.on("memory:written", (e) => captured.push(e));
    bus.on("memory:invalidated", (e) => captured.push(e));
    repo = new MemoryRepository(db, bus);
  });

  it("emits memory:written for each inserted record", () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "x", importance: 0.5, goal_id: "g1" },
      { id: "b", kind: "episodic", content: "y", importance: 0.7 },
    ]);
    const written = captured.filter((e) => e.type === "memory:written");
    expect(written).toHaveLength(2);
    expect(written[0]).toMatchObject({ id: "a", kind: "semantic", goal_id: "g1", importance: 0.5 });
    expect(written[1]).toMatchObject({ id: "b", kind: "episodic", goal_id: null });
  });

  it("emits memory:invalidated", () => {
    repo.insertBatch([{ id: "a", kind: "semantic", content: "x", importance: 0.5 }]);
    repo.invalidate("a", { reason: "user corrected", invalidatedBy: "test" });
    const inv = captured.find((e) => e.type === "memory:invalidated");
    expect(inv).toBeDefined();
    expect(inv).toMatchObject({ id: "a", reason: "user corrected", invalidated_by: "test" });
  });

  it("does not require a bus (optional dependency)", () => {
    const repoNoBus = new MemoryRepository(db);
    expect(() =>
      repoNoBus.insertBatch([{ id: "z", kind: "semantic", content: "x", importance: 0.5 }]),
    ).not.toThrow();
  });
});
