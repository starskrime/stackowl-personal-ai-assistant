import { describe, it, expect, vi } from "vitest";

describe("PelletDeduplicator — IntelligenceRouter path", () => {
  it("uses router.resolve when router is provided", async () => {
    const { PelletDeduplicator } = await import("../../src/pellets/dedup.js");
    const mockRouter = { resolve: vi.fn().mockResolvedValue('{"verdict":"CREATE","reasoning":"unique"}') };
    const mockSearch = vi.fn().mockResolvedValue([
      { pellet: { id: "existing", title: "Existing", content: "content", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 5, failureCount: 0, provenance: [] }, score: 0.95 }
    ]);
    const dedup = new (PelletDeduplicator as any)(mockSearch, undefined, { useLlm: true, similarityThreshold: 0.5 }, mockRouter);
    const incoming = { id: "new", title: "New", content: "content", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    await dedup.evaluate(incoming);
    expect(mockRouter.resolve).toHaveBeenCalledWith("classification", expect.any(String));
  });

  it("falls back to provider.chat when no router", async () => {
    const { PelletDeduplicator } = await import("../../src/pellets/dedup.js");
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"verdict":"CREATE","reasoning":"ok"}' }) };
    const mockSearch = vi.fn().mockResolvedValue([
      { pellet: { id: "ex", title: "Ex", content: "x", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] }, score: 0.92 }
    ]);
    const dedup = new (PelletDeduplicator as any)(mockSearch, mockProvider, { useLlm: true, similarityThreshold: 0.5 });
    const incoming = { id: "n", title: "N", content: "x", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    await dedup.evaluate(incoming);
    expect(mockProvider.chat).toHaveBeenCalled();
  });
});

describe("KnowledgeBase.computeCoverageGaps — IntelligenceRouter path", () => {
  it("uses router.resolve when router is provided", async () => {
    const { KnowledgeBase } = await import("../../src/pellets/knowledge-base.js");
    const mockStore = { listAll: vi.fn().mockResolvedValue([]) };
    const mockRouter = { resolve: vi.fn().mockResolvedValue('["api","security"]') };
    const kb = new (KnowledgeBase as any)(mockStore, mockRouter);
    const gaps = await kb.findCoverageGaps();
    expect(mockRouter.resolve).toHaveBeenCalled();
    expect(Array.isArray(gaps)).toBe(true);
  });

  it("uses hardcoded array fallback when no router", async () => {
    const { KnowledgeBase } = await import("../../src/pellets/knowledge-base.js");
    const mockStore = { listAll: vi.fn().mockResolvedValue([]) };
    const kb = new KnowledgeBase(mockStore as any);
    const gaps = await kb.findCoverageGaps();
    expect(Array.isArray(gaps)).toBe(true);
  });
});
