import { describe, it, expect, vi } from "vitest";
import { RelevantPelletsLayer } from "../../src/context/layers/knowledge.js";

const makePellet = (id: string, successCount: number, failureCount: number) => ({
  id, title: id, content: "x".repeat(600), tags: ["t"], owls: ["Noctua"],
  source: "s", generatedAt: new Date().toISOString(), version: 1,
  successCount, failureCount, provenance: [],
});

describe("RelevantPelletsLayer", () => {
  it("calls searchWithGraphScored", async () => {
    const layer = new RelevantPelletsLayer();
    const mockStore = {
      searchWithGraphScored: vi.fn().mockResolvedValue([
        { p: makePellet("p1", 0, 0), score: 0.9 },
      ]),
    };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    const triage = { userMessage: "hello", isConversational: false } as any;
    await layer.build(req, triage, new Map());
    expect(mockStore.searchWithGraphScored).toHaveBeenCalledWith("hello", 5);
  });

  it("writes IDs to req.retrievedPelletIds", async () => {
    const layer = new RelevantPelletsLayer();
    const mockStore = {
      searchWithGraphScored: vi.fn().mockResolvedValue([
        { p: makePellet("a", 1, 0), score: 0.8 },
        { p: makePellet("b", 0, 0), score: 0.6 },
      ]),
    };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    await layer.build(req, { userMessage: "q", isConversational: false } as any, new Map());
    expect(req.retrievedPelletIds).toEqual(["a", "b"]);
  });

  it("truncates content to 500 chars", async () => {
    const layer = new RelevantPelletsLayer();
    const longPellet = makePellet("p", 0, 0);
    const mockStore = { searchWithGraphScored: vi.fn().mockResolvedValue([{ p: longPellet, score: 0.9 }]) };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    const output = await layer.build(req, { userMessage: "q", isConversational: false } as any, new Map());
    // content is "x".repeat(600), truncated to 500
    expect(output).toContain("x".repeat(500));
    expect(output).not.toContain("x".repeat(501));
  });
});
