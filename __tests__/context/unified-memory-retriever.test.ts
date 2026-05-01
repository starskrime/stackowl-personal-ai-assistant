import { describe, it, expect, vi } from "vitest";
import { UnifiedMemoryRetriever } from "../../src/context/unified-memory-retriever.js";

function mockBus(results: any[] = []) {
  return { recall: vi.fn(async () => results) } as any;
}
function mockFactStore(facts: any[] = []) {
  return { search: vi.fn(() => facts) } as any;
}
function mockEpisodic(episodes: any[] = []) {
  return { search: vi.fn(async () => episodes) } as any;
}

describe("UnifiedMemoryRetriever", () => {
  it("returns empty string when all stores empty", async () => {
    const retriever = new UnifiedMemoryRetriever(mockBus(), mockFactStore(), mockEpisodic());
    const result = await retriever.retrieve("anything", "u1");
    expect(result).toBe("");
  });

  it("returns labeled XML with facts tier", async () => {
    const facts = [{ id: "f1", fact: "User prefers Python", confidence: 0.9, userId: "u1" }];
    const retriever = new UnifiedMemoryRetriever(mockBus(), mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("python", "u1");
    expect(result).toContain("<memory>");
    expect(result).toContain('tier="long_term"');
    expect(result).toContain("User prefers Python");
  });

  it("deduplicates near-identical results across stores", async () => {
    const facts = [{ id: "f1", fact: "User builds trading bots", confidence: 0.9, userId: "u1" }];
    const busResults = [{ id: "bus1", content: "User builds trading bots", source: "reflexion", relevance: 0.8, category: "fact", timestamp: "" }];
    const retriever = new UnifiedMemoryRetriever(mockBus(busResults), mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("trading", "u1");
    // Should only appear once
    const count = (result.match(/trading bots/gi) ?? []).length;
    expect(count).toBe(1);
  });

  it("isolates store failure — other stores still return", async () => {
    const badBus = { recall: vi.fn(async () => { throw new Error("bus down"); }) } as any;
    const facts = [{ id: "f1", fact: "resilience test", confidence: 0.9, userId: "u1" }];
    const retriever = new UnifiedMemoryRetriever(badBus, mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("resilience", "u1");
    expect(result).toContain("resilience test");
  });

  it("preserves long_term tier when episode content overlaps with a fact", async () => {
    const facts = [{ id: "f1", fact: "User builds trading bots", confidence: 0.9, userId: "u1" }];
    const episodes = [{ id: "e1", summary: "User builds trading bots", importance: 0.99 }];
    const retriever = new UnifiedMemoryRetriever(mockBus(), mockFactStore(facts), mockEpisodic(episodes));
    const result = await retriever.retrieve("trading", "u1");
    expect(result).toContain('tier="long_term"');
    expect(result).not.toContain('tier="episodic"');
  });
});
