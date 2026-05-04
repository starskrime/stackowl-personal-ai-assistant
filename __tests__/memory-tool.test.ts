import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";
import { createMemoryTool } from "../src/tools/memory-unified.js";

interface TestSetup {
  db: import("better-sqlite3").Database;
  repo: MemoryRepository;
  bus: GatewayEventBus;
  hitlCreate: ReturnType<typeof vi.fn>;
  tool: ReturnType<typeof createMemoryTool>;
}

function setup(): TestSetup {
  const db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  applyV25Migration(db);
  const bus = new GatewayEventBus();
  const repo = new MemoryRepository(db, bus);
  const hitlCreate = vi.fn().mockResolvedValue("ckpt-123");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tool = createMemoryTool({ repo, bus, hitl: { create: hitlCreate } as any });
  return { db, repo, bus, hitlCreate, tool };
}

describe("memory tool — search action", () => {
  let s: TestSetup;
  beforeEach(() => {
    s = setup();
    s.repo.insertBatch([
      { id: "a", kind: "semantic", content: "user prefers TypeScript", importance: 0.7 },
      { id: "b", kind: "episodic", content: "shipped Element 14 yesterday", importance: 0.5 },
    ]);
  });

  it("returns matching records", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "search", query: "TypeScript" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    const ids = parsed.data.results.map((r: { id: string }) => r.id);
    expect(ids).toContain("a");
  });

  it("filters by kind when supplied", async () => {
    const out = await s.tool.execute(
      { action: "search", query: "anything", kinds: ["episodic"] },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    const kinds = parsed.data.results.map((r: { kind: string }) => r.kind);
    expect(kinds.every((k: string) => k === "episodic")).toBe(true);
  });

  it("respects topK", async () => {
    const out = await s.tool.execute(
      { action: "search", query: "anything", topK: 1 },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.data.count).toBe(1);
    expect(parsed.data.results).toHaveLength(1);
  });
});

describe("memory tool — get action", () => {
  it("returns record by id", async () => {
    const s = setup();
    s.repo.insertBatch([
      { id: "x", kind: "semantic", content: "fact", importance: 0.5 },
    ]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "get", id: "x" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.record.id).toBe("x");
  });

  it("returns NOT_FOUND error for unknown id", async () => {
    const s = setup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "get", id: "nope" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });

  it("returns MISSING_ID error when id is absent", async () => {
    const s = setup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "get" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.error.code).toBe("MISSING_ID");
  });
});

describe("memory tool — invalidate action with approval gate", () => {
  it("invalidates immediately when importance < 0.8", async () => {
    const s = setup();
    s.repo.insertBatch([
      { id: "low", kind: "semantic", content: "low-importance fact", importance: 0.3 },
    ]);
    const out = await s.tool.execute(
      { action: "invalidate", id: "low", reason: "stale" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.invalidated).toBe(1);
    expect(s.repo.getById("low")?.invalid_at).not.toBeNull();
    expect(s.hitlCreate).not.toHaveBeenCalled();
  });

  it("routes invalidations of importance ≥ 0.8 through HitlCheckpointStore", async () => {
    const s = setup();
    s.repo.insertBatch([
      { id: "high", kind: "semantic", content: "critical fact", importance: 0.9 },
    ]);
    const out = await s.tool.execute(
      { action: "invalidate", id: "high", reason: "stale" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { engineContext: { sessionId: "s1" } } as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.requiresApproval).toBe(true);
    expect(parsed.data.checkpointId).toBe("ckpt-123");
    expect(parsed.data.importance).toBeCloseTo(0.9, 5);
    expect(s.hitlCreate).toHaveBeenCalledTimes(1);
    // Memory not yet invalidated until approved.
    expect(s.repo.getById("high")?.invalid_at).toBeNull();
  });

  it("invalidations of exactly importance=0.8 trigger approval gate (>=)", async () => {
    const s = setup();
    s.repo.insertBatch([
      { id: "edge", kind: "semantic", content: "edge fact", importance: 0.8 },
    ]);
    const out = await s.tool.execute(
      { action: "invalidate", id: "edge", reason: "x" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { engineContext: { sessionId: "s1" } } as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.data.requiresApproval).toBe(true);
    expect(s.repo.getById("edge")?.invalid_at).toBeNull();
  });

  it("returns NOT_FOUND for unknown id", async () => {
    const s = setup();
    const out = await s.tool.execute(
      { action: "invalidate", id: "nope", reason: "x" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });

  it("returns MISSING_ID for invalidate without id", async () => {
    const s = setup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "invalidate" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.error.code).toBe("MISSING_ID");
  });
});

describe("memory tool — unsupported action", () => {
  it("returns ACTION_NOT_SUPPORTED for unknown action", async () => {
    const s = setup();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await s.tool.execute({ action: "frobnicate" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACTION_NOT_SUPPORTED");
  });
});
