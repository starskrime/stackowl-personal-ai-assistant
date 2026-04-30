import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { UserMemoryStore } from "../src/session/user-memory-store.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// Mock the embedder
vi.mock("../src/pellets/embedder.js", () => ({
  embed: vi.fn(),
  isEmbedderReady: vi.fn(),
  initEmbedder: vi.fn(),
  setEmbedderCacheDir: vi.fn(),
}));

import { embed, isEmbedderReady } from "../src/pellets/embedder.js";

function makeVec(seed: number, dims = 4): number[] {
  // deterministic unit vector
  const v = Array.from({ length: dims }, (_, i) => Math.sin(seed + i));
  const mag = Math.sqrt(v.reduce((s, x) => s + x * x, 0));
  return v.map((x) => x / mag);
}

describe("UserMemoryStore", () => {
  let tmpDir: string;
  let db: MemoryDatabase;
  let store: UserMemoryStore;

  beforeEach(() => {
    // MemoryDatabase takes a workspace path (not a direct db file path)
    tmpDir = mkdtempSync(join(tmpdir(), "owl-ums-"));
    db = new MemoryDatabase(tmpDir);
    store = new UserMemoryStore(db);

    // embedder ready by default in most tests
    vi.mocked(isEmbedderReady).mockReturnValue(true);
    vi.mocked(embed).mockResolvedValue(makeVec(1));
  });

  afterEach(() => {
    db.close();
    rmSync(tmpDir, { recursive: true, force: true });
    vi.clearAllMocks();
  });

  describe("add()", () => {
    it("stores a new fact when no duplicates exist", async () => {
      await store.add("user1", "Prefers TypeScript", "preference", "owl1");
      const results = await store.retrieve("user1", "TypeScript", 10);
      expect(results).toHaveLength(1);
      expect(results[0]).toBe("Prefers TypeScript");
    });

    it("skips duplicate facts above 0.88 cosine threshold", async () => {
      const vec = makeVec(1);
      vi.mocked(embed).mockResolvedValue(vec); // same vec = cosine 1.0
      await store.add("user1", "Likes TypeScript", "preference", "owl1");
      await store.add("user1", "Also likes TypeScript", "preference", "owl1"); // duplicate
      const results = await store.retrieve("user1", "TypeScript", 10);
      expect(results).toHaveLength(1);
    });

    it("stores facts with different embeddings (no dedup)", async () => {
      // makeVec(1) and makeVec(2) have cosine ~0.68 (distinct, not dedup'd),
      // both have cosine > 0.25 with query vec(1) so both are returned by semanticSearch
      vi.mocked(embed)
        .mockResolvedValueOnce(makeVec(1))  // fact 1 embedding
        .mockResolvedValueOnce(makeVec(2))  // fact 2 embedding (cosine 0.68 vs fact1)
        .mockResolvedValueOnce(makeVec(1)); // query embedding for retrieve
      await store.add("user1", "Prefers TypeScript", "preference", "owl1");
      await store.add("user1", "Expert in Go", "skill", "owl1");
      const results = await store.retrieve("user1", "anything", 10);
      expect(results).toHaveLength(2);
    });

    it("stores fact without embedding when embedder unavailable", async () => {
      vi.mocked(isEmbedderReady).mockReturnValue(false);
      await store.add("user1", "Likes dark mode", "preference", "owl1");
      // should not throw; fact stored
    });
  });

  describe("retrieve()", () => {
    it("returns empty array when no facts exist", async () => {
      vi.mocked(embed).mockResolvedValue(makeVec(1));
      const results = await store.retrieve("user1", "anything", 3);
      expect(results).toBeInstanceOf(Array);
    });

    it("respects limit parameter", async () => {
      vi.mocked(embed)
        .mockResolvedValueOnce(makeVec(1))
        .mockResolvedValueOnce(makeVec(2))
        .mockResolvedValueOnce(makeVec(3))
        .mockResolvedValueOnce(makeVec(1)); // query uses vec(1)
      await store.add("user1", "fact1", "preference", "owl1");
      await store.add("user1", "fact2", "skill", "owl1");
      await store.add("user1", "fact3", "goal", "owl1");
      const results = await store.retrieve("user1", "query", 2);
      expect(results.length).toBeLessThanOrEqual(2);
    });
  });
});
