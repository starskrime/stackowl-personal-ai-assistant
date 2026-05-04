import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import {
  createSemanticMemoryLayer,
  createEpisodicMemoryLayer,
  createWorkingMemoryLayer,
  createProceduralMemoryLayer,
  createMemoryLayers,
} from "../src/memory/layer.js";

describe("MemoryLayer factories", () => {
  let db: import("better-sqlite3").Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("creates four layers with distinct names + priorities", () => {
    const s = createSemanticMemoryLayer({ repo });
    const e = createEpisodicMemoryLayer({ repo });
    const w = createWorkingMemoryLayer({ repo });
    const p = createProceduralMemoryLayer({ repo });
    expect(new Set([s.name, e.name, w.name, p.name]).size).toBe(4);
    expect(s.name).toBe("memory.semantic");
    expect(e.name).toBe("memory.episodic");
    expect(w.name).toBe("memory.working");
    expect(p.name).toBe("memory.procedural");
  });

  it("createMemoryLayers returns the four factories in order", () => {
    const layers = createMemoryLayers({ repo });
    expect(layers).toHaveLength(4);
    expect(layers.map((l) => l.name)).toEqual([
      "memory.semantic",
      "memory.episodic",
      "memory.working",
      "memory.procedural",
    ]);
  });

  it("each layer has cacheTtlMs defined per spec", () => {
    expect(createSemanticMemoryLayer({ repo }).cacheTtlMs).toBeGreaterThanOrEqual(60_000);
    expect(createWorkingMemoryLayer({ repo }).cacheTtlMs).toBeLessThanOrEqual(60_000);
    expect(createProceduralMemoryLayer({ repo }).cacheTtlMs).toBeGreaterThanOrEqual(60_000);
  });

  it("each layer publishes its name as a produces tag", () => {
    const s = createSemanticMemoryLayer({ repo });
    expect(s.produces).toEqual(["memory.semantic"]);
    expect(s.dependsOn).toEqual([]);
  });

  it("semantic layer build() returns formatted memories sorted by score", async () => {
    repo.insertBatch([
      { id: "s1", kind: "semantic", content: "user prefers concise answers", importance: 0.9 },
      { id: "s2", kind: "semantic", content: "user uses TypeScript daily", importance: 0.4 },
    ]);
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({ userMessage: "what should I work on" } as any, {} as any, new Map());
    expect(out).toContain("concise answers");
    expect(out).toContain("TypeScript");
    expect(out.indexOf("concise")).toBeLessThan(out.indexOf("TypeScript"));
    expect(out).toContain("## Long-term facts about the user");
  });

  it("excludes reflexive memories from prompt rendering", async () => {
    repo.insertBatch([
      { id: "ref1", kind: "reflexive", content: "engine noticed slow tool", importance: 0.9 },
      { id: "sem1", kind: "semantic", content: "user likes Rust", importance: 0.5 },
    ]);
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    expect(out).toContain("Rust");
    expect(out).not.toContain("engine noticed slow tool");
  });

  it("returns empty string when no records match the kind", async () => {
    repo.insertBatch([
      { id: "s1", kind: "semantic", content: "anything", importance: 0.5 },
    ]);
    const layer = createWorkingMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    expect(out).toBe("");
  });

  it("excludes invalidated memories", async () => {
    repo.insertBatch([
      { id: "s-live", kind: "semantic", content: "still valid fact", importance: 0.5 },
      { id: "s-dead", kind: "semantic", content: "stale fact", importance: 0.5 },
    ]);
    repo.invalidate("s-dead", { reason: "expired", invalidatedBy: "test" });
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    expect(out).toContain("still valid");
    expect(out).not.toContain("stale fact");
  });

  it("falls back to triage.userMessage when req.userMessage is absent", async () => {
    repo.insertBatch([
      { id: "s1", kind: "semantic", content: "user prefers concise", importance: 0.5 },
    ]);
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, { userMessage: "anything" } as any, new Map());
    expect(out).toContain("concise");
  });

  it("getCacheKey uses session id when provided", () => {
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const key = layer.getCacheKey?.({ sessionId: "sess-42" } as any, {} as any);
    expect(key).toBe("memory.semantic:sess-42");
  });
});

describe("MemoryLayer — token budget enforcement", () => {
  let db: import("better-sqlite3").Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("truncates output to maxTokens × 4 chars", async () => {
    const longContent = "x".repeat(5000);
    repo.insertBatch([{ id: "a", kind: "semantic", content: longContent, importance: 0.9 }]);
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    // 800 tokens × 4 chars = 3200 max
    expect(out.length).toBeLessThanOrEqual(3200);
    expect(out.endsWith("...")).toBe(true);
  });

  it("does not truncate short output", async () => {
    repo.insertBatch([{ id: "a", kind: "semantic", content: "short fact", importance: 0.5 }]);
    const layer = createSemanticMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    expect(out.endsWith("...")).toBe(false);
  });

  it("working layer applies its own (smaller) budget", async () => {
    const longContent = "y".repeat(5000);
    repo.insertBatch([{ id: "w", kind: "working", content: longContent, importance: 0.5 }]);
    const layer = createWorkingMemoryLayer({ repo });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await layer.build({} as any, {} as any, new Map());
    // 400 tokens × 4 chars = 1600 max
    expect(out.length).toBeLessThanOrEqual(1600);
  });
});
