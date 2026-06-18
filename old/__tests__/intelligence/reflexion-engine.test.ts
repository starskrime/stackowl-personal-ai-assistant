import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { ReflexionEngine } from "../../src/intelligence/reflexion-engine.js";

describe("ReflexionEngine", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("writes a critique row after task failure", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "I searched too broadly. Next time use specific terms.", finishReason: "stop", model: "test" }),
    };
    const mockEmbedFn = vi.fn().mockResolvedValue(new Array(4).fill(0.1));

    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);
    await engine.onTaskFailed({
      userId: "u1",
      taskDescription: "Find TypeScript docs",
      toolSequence: ["web", "web"],
      errorSummary: "all fetches returned 404",
      category: "research",
      complexityTier: "medium",
    });

    const rows = db.prepare("SELECT * FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
    expect((rows[0] as any).critique_text).toContain("searched too broadly");
  });

  it("deduplicates identical tool sequence + category", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "same critique", finishReason: "stop", model: "test" }),
    };
    const mockEmbedFn = vi.fn().mockResolvedValue(new Array(4).fill(0.1));
    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);

    const args = { userId: "u1", taskDescription: "find docs", toolSequence: ["web"], errorSummary: "404", category: "research", complexityTier: "medium" };
    await engine.onTaskFailed(args);
    await engine.onTaskFailed(args); // duplicate

    const rows = db.prepare("SELECT * FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
  });

  it("skips writing when qualityScore too low", async () => {
    const mockProvider = { chat: vi.fn() };
    const mockEmbedFn = vi.fn();
    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);

    await engine.onTaskFailed({
      userId: "u1", taskDescription: "x", toolSequence: [], errorSummary: "", category: "research", complexityTier: "low", qualityScore: 0.2,
    });

    expect(mockProvider.chat).not.toHaveBeenCalled();
  });
});
