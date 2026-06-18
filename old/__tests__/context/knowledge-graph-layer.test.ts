// __tests__/context/knowledge-graph-layer.test.ts
import { describe, it, expect } from "vitest";
import { KnowledgeGraphLayer } from "../../src/context/layers/knowledge.js";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";

function makeReq(kg?: KnowledgeGraph) {
  return {
    session: { messages: [] },
    callbacks: {},
    continuityResult: null,
    digest: null,
    deps: { sessionStore: {} as any, config: {} as any, knowledgeGraph: kg },
  } as any;
}

const triage = {
  userMessage: "TypeScript",
  isConversational: false,
  hasFrustration: false,
  isOpinionRequest: false,
  hasTemporalTrigger: false,
  isReturningUser: false,
  sessionDepth: 1,
  hasActiveItems: false,
  effectiveUserId: "u1",
  continuityClass: null,
} as any;

describe("KnowledgeGraphLayer", () => {
  it("returns empty string when deps.knowledgeGraph is absent", async () => {
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(undefined), triage, new Map());
    expect(result).toBe("");
  });

  it("returns empty string when graph has no matching nodes", async () => {
    const kg = new KnowledgeGraph("/tmp");
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(kg), triage, new Map());
    expect(result).toBe("");
  });

  it("returns <knowledge_graph> block when nodes match", async () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "TypeScript basics", content: "TS is typed JS", domain: "prog", type: "concept", confidence: 0.9, tags: [] });
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(kg), triage, new Map());
    expect(result).toContain("<knowledge_graph>");
    expect(result).toContain("TypeScript basics");
    expect(result).toContain("</knowledge_graph>");
  });

  it("does NOT read from (req.session as any).knowledgeGraphContext", async () => {
    const layer = new KnowledgeGraphLayer();
    const req = makeReq(undefined);
    (req.session as any).knowledgeGraphContext = "STALE_CAST_DATA";
    const result = await layer.build(req, triage, new Map());
    // Should be empty because deps.knowledgeGraph is absent, not reading the cast
    expect(result).toBe("");
  });
});
