// __tests__/knowledge/graph-query-context.test.ts
import { describe, it, expect } from "vitest";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";

describe("KnowledgeGraph.queryContext()", () => {
  it("returns empty string when graph has no nodes", () => {
    const kg = new KnowledgeGraph("/tmp");
    expect(kg.queryContext("anything")).toBe("");
  });

  it("returns top-3 matching nodes as formatted string", () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "TypeScript basics", content: "TypeScript is a typed superset of JavaScript", domain: "programming", type: "concept", confidence: 0.9, tags: [] });
    kg.addNode({ title: "React hooks", content: "React hooks allow state in function components", domain: "programming", type: "concept", confidence: 0.8, tags: [] });
    kg.addNode({ title: "Cooking pasta", content: "Boil water, add salt, cook pasta", domain: "cooking", type: "fact", confidence: 0.7, tags: [] });
    kg.addNode({ title: "TypeScript generics", content: "Generics provide type parameters", domain: "programming", type: "concept", confidence: 0.85, tags: [] });

    const result = kg.queryContext("TypeScript");
    expect(result).toContain("TypeScript basics");
    expect(result).toContain("TypeScript generics");
    expect(result).not.toContain("Cooking pasta");
    // At most 3 results
    const titleMatches = (result.match(/title=/g) ?? []).length;
    expect(titleMatches).toBeLessThanOrEqual(3);
  });

  it("returns empty string when no nodes match the query", () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "React hooks", content: "hooks", domain: "programming", type: "concept", confidence: 0.8, tags: [] });
    expect(kg.queryContext("completely unrelated xyz")).toBe("");
  });
});
