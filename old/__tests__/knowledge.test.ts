import { describe, it, expect, beforeEach, vi } from "vitest";
import { KnowledgeGraph } from "../src/knowledge/graph.js";
import { KnowledgeReasoner } from "../src/knowledge/reasoner.js";
import type {
  KnowledgeNode,
  EdgeType,
  ReasoningChain,
} from "../src/knowledge/types.js";
import type { ModelProvider } from "../src/providers/base.js";

const createMockProvider = (): ModelProvider => {
  const mockChat = vi.fn() as ReturnType<typeof vi.fn>;
  return {
    name: "mock",
    chat: mockChat,
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn(),
    healthCheck: vi.fn(),
  };
};

describe("KnowledgeGraph", () => {
  const workspace = "/tmp/test-knowledge-workspace";
  let graph: KnowledgeGraph;

  beforeEach(() => {
    graph = new KnowledgeGraph(workspace);
  });

  describe("addNode", () => {
    it("should add a node with generated id and timestamps", () => {
      const id = graph.addNode({
        title: "Test Node",
        content: "Test content",
        domain: "testing",
        source: "test",
        confidence: 0.8,
      });

      expect(id).toBeDefined();
      expect(typeof id).toBe("string");

      const node = graph.getNode(id);
      expect(node).toBeDefined();
      expect(node!.title).toBe("Test Node");
      expect(node!.content).toBe("Test content");
      expect(node!.domain).toBe("testing");
      expect(node!.confidence).toBe(0.8);
      expect(node!.accessCount).toBe(0);
      expect(node!.createdAt).toBeDefined();
      expect(node!.updatedAt).toBeDefined();
    });

    it("should add a node with embedding when provided", () => {
      const embedding = [0.1, 0.2, 0.3];
      const id = graph.addNode(
        {
          title: "Embedded Node",
          content: "Content with embedding",
          domain: "ai",
          source: "test",
          confidence: 0.9,
        },
        embedding,
      );

      const node = graph.getNode(id);
      expect(node!.embedding).toEqual(embedding);
    });
  });

  describe("addEdge", () => {
    it("should add an edge between two existing nodes", () => {
      const nodeA = graph.addNode({
        title: "Node A",
        content: "Content A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "Node B",
        content: "Content B",
        domain: "test",
        source: "test",
        confidence: 0.7,
      });

      const edgeId = graph.addEdge(
        nodeA,
        nodeB,
        "supports",
        0.8,
        "Test evidence",
      );

      expect(edgeId).toBeDefined();
      const edges = graph.getEdges(nodeA);
      expect(edges).toHaveLength(1);
      expect(edges[0].type).toBe("supports");
      expect(edges[0].weight).toBe(0.8);
      expect(edges[0].evidence).toBe("Test evidence");
    });

    it("should throw error when adding edge from non-existent node", () => {
      const nodeA = graph.addNode({
        title: "Node A",
        content: "Content A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      expect(() => graph.addEdge(nodeA, "non-existent-id", "supports")).toThrow(
        "Node not found: non-existent-id",
      );
    });
  });

  describe("search", () => {
    it("should find nodes matching query terms", () => {
      graph.addNode({
        title: "JavaScript Functions",
        content: "Functions are first-class citizens in JavaScript",
        domain: "programming",
        source: "test",
        confidence: 0.9,
      });
      graph.addNode({
        title: "Python Basics",
        content: "Python is a versatile language",
        domain: "programming",
        source: "test",
        confidence: 0.8,
      });

      const results = graph.search("javascript");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].title).toBe("JavaScript Functions");
    });

    it("should score title matches higher than content matches", () => {
      graph.addNode({
        title: "React Guide",
        content: "React is a library for building UIs",
        domain: "frontend",
        source: "test",
        confidence: 0.9,
      });
      graph.addNode({
        title: "UI Patterns",
        content: "React patterns for components",
        domain: "frontend",
        source: "test",
        confidence: 0.7,
      });

      const results = graph.search("React");
      expect(results[0].title).toBe("React Guide");
    });

    it("should increment accessCount on search", () => {
      const id = graph.addNode({
        title: "Test Node",
        content: "Test content for search",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const initialNode = graph.getNode(id);
      expect(initialNode!.accessCount).toBe(0);

      graph.search("Test");
      const afterSearch = graph.getNode(id);
      expect(afterSearch!.accessCount).toBe(1);
    });

    it("should respect limit parameter", () => {
      for (let i = 0; i < 20; i++) {
        graph.addNode({
          title: `Node ${i}`,
          content: `Content for node ${i} searchable term`,
          domain: "test",
          source: "test",
          confidence: 0.5,
        });
      }

      const results = graph.search("searchable", 5);
      expect(results).toHaveLength(5);
    });

    it("should return empty array when no matches found", () => {
      const results = graph.search("nonexistentterm123");
      expect(results).toHaveLength(0);
    });
  });

  describe("semanticSearch", () => {
    it("should fall back to keyword search when no embedder provided", async () => {
      graph.addNode({
        title: "Test Node",
        content: "Some test content",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const results = await graph.semanticSearch("test", 10);
      expect(results.length).toBeGreaterThan(0);
    });

    it("should use embedder for semantic search when provided", async () => {
      const mockEmbedder = vi.fn().mockResolvedValue([0.5, 0.5, 0.5]);

      graph.addNode(
        {
          title: "Semantic Node",
          content: "Content for semantic search",
          domain: "ai",
          source: "test",
          confidence: 0.8,
        },
        [0.6, 0.6, 0.6],
      );

      const results = await graph.semanticSearch(
        "test query",
        10,
        mockEmbedder,
      );
      expect(mockEmbedder).toHaveBeenCalledWith("test query");
      expect(results.length).toBeGreaterThan(0);
    });

    it("should fall back to keyword search when embedder returns empty array", async () => {
      const mockEmbedder = vi.fn().mockResolvedValue([]);

      graph.addNode({
        title: "Fallback Node",
        content: "Content",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const results = await graph.semanticSearch("Fallback", 10, mockEmbedder);
      expect(results.length).toBeGreaterThan(0);
    });

    it("should fall back to keyword search when embedder throws", async () => {
      const mockEmbedder = vi
        .fn()
        .mockRejectedValue(new Error("Embedding failed"));

      graph.addNode({
        title: "Error Fallback Node",
        content: "Content",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const results = await graph.semanticSearch("Error", 10, mockEmbedder);
      expect(results.length).toBeGreaterThan(0);
    });

    it("should boost scores for keyword-matched nodes in hybrid search", async () => {
      const mockEmbedder = vi.fn().mockResolvedValue([0.1, 0.2, 0.3]);

      graph.addNode(
        {
          title: "JavaScript",
          content: "Programming language",
          domain: "code",
          source: "test",
          confidence: 0.9,
        },
        [0.5, 0.5, 0.5],
      );
      graph.addNode(
        {
          title: "Python",
          content: "Also a programming language",
          domain: "code",
          source: "test",
          confidence: 0.8,
        },
        [0.3, 0.3, 0.3],
      );

      const results = await graph.semanticSearch(
        "JavaScript",
        10,
        mockEmbedder,
      );
      expect(results[0].title).toBe("JavaScript");
    });
  });

  describe("findByDomain", () => {
    it("should find all nodes in a domain", () => {
      graph.addNode({
        title: "Node 1",
        content: "C1",
        domain: "science",
        source: "test",
        confidence: 0.5,
      });
      graph.addNode({
        title: "Node 2",
        content: "C2",
        domain: "science",
        source: "test",
        confidence: 0.6,
      });
      graph.addNode({
        title: "Node 3",
        content: "C3",
        domain: "arts",
        source: "test",
        confidence: 0.7,
      });

      const scienceNodes = graph.findByDomain("science");
      expect(scienceNodes).toHaveLength(2);
    });

    it("should be case insensitive", () => {
      graph.addNode({
        title: "Node",
        content: "Content",
        domain: "Science",
        source: "test",
        confidence: 0.5,
      });

      const results = graph.findByDomain("SCIENCE");
      expect(results).toHaveLength(1);
    });
  });

  describe("getEdges", () => {
    it("should return all edges connected to a node", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeC = graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");
      graph.addEdge(nodeA, nodeC, "extends");

      const edges = graph.getEdges(nodeA);
      expect(edges).toHaveLength(2);
    });
  });

  describe("getNeighbors", () => {
    it("should return neighboring nodes", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");

      const neighbors = graph.getNeighbors(nodeA);
      expect(neighbors).toHaveLength(1);
      expect(neighbors[0].id).toBe(nodeB);
    });

    it("should filter by edge type when specified", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeC = graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");
      graph.addEdge(nodeA, nodeC, "contradicts");

      const supportsNeighbors = graph.getNeighbors(nodeA, "supports");
      expect(supportsNeighbors).toHaveLength(1);
      expect(supportsNeighbors[0].title).toBe("B");
    });
  });

  describe("findContradictions", () => {
    it("should find all edges of type contradicts", () => {
      const nodeA = graph.addNode({
        title: "Fact A",
        content: "A is true",
        domain: "test",
        source: "test",
        confidence: 0.8,
      });
      const nodeB = graph.addNode({
        title: "Fact B",
        content: "B is false",
        domain: "test",
        source: "test",
        confidence: 0.7,
      });

      graph.addEdge(nodeA, nodeB, "contradicts");

      const contradictions = graph.findContradictions();
      expect(contradictions).toHaveLength(1);
      expect(contradictions[0].nodeA.id).toBe(nodeA);
      expect(contradictions[0].nodeB.id).toBe(nodeB);
      expect(contradictions[0].edge.type).toBe("contradicts");
    });

    it("should return empty array when no contradictions exist", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");

      const contradictions = graph.findContradictions();
      expect(contradictions).toHaveLength(0);
    });
  });

  describe("getStats", () => {
    it("should return correct graph statistics", () => {
      graph.addNode({
        title: "Node 1",
        content: "C1",
        domain: "science",
        source: "test",
        confidence: 0.8,
      });
      graph.addNode({
        title: "Node 2",
        content: "C2",
        domain: "science",
        source: "test",
        confidence: 0.6,
      });
      graph.addNode({
        title: "Node 3",
        content: "C3",
        domain: "arts",
        source: "test",
        confidence: 0.9,
      });

      const allNodes = graph.getAllNodes();
      const nodeA = allNodes[0];
      const nodeB = allNodes[1];
      graph.addEdge(nodeA.id, nodeB.id, "supports");

      const stats = graph.getStats();
      expect(stats.totalNodes).toBe(3);
      expect(stats.totalEdges).toBe(1);
      expect(stats.domains).toContain("science");
      expect(stats.domains).toContain("arts");
      expect(stats.avgConfidence).toBeCloseTo(0.766, 2);
      expect(stats.topNodes).toHaveLength(3);
    });

    it("should handle empty graph", () => {
      const stats = graph.getStats();
      expect(stats.totalNodes).toBe(0);
      expect(stats.totalEdges).toBe(0);
      expect(stats.domains).toHaveLength(0);
      expect(stats.avgConfidence).toBe(0);
    });
  });

  describe("removeNode", () => {
    it("should remove node and its connected edges", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeC = graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");
      graph.addEdge(nodeA, nodeC, "extends");

      graph.removeNode(nodeA);

      expect(graph.getNode(nodeA)).toBeUndefined();
      expect(graph.getEdges(nodeA)).toHaveLength(0);
      expect(graph.getEdges(nodeB)).toHaveLength(0);
      expect(graph.getEdges(nodeC)).toHaveLength(0);
    });
  });

  describe("mergeNodes", () => {
    it("should merge content and preserve highest confidence", () => {
      const keepId = graph.addNode({
        title: "Keep Node",
        content: "Original content",
        domain: "test",
        source: "test",
        confidence: 0.7,
      });
      const removeId = graph.addNode({
        title: "Remove Node",
        content: "Additional content",
        domain: "test",
        source: "test",
        confidence: 0.9,
      });

      graph.mergeNodes(keepId, removeId);

      const merged = graph.getNode(keepId);
      expect(merged!.content).toContain("Original content");
      expect(merged!.content).toContain("Additional content");
      expect(merged!.confidence).toBe(0.9);
      expect(graph.getNode(removeId)).toBeUndefined();
    });

    it("should throw error when keep node not found", () => {
      const removeId = graph.addNode({
        title: "Remove",
        content: "Content",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      expect(() => graph.mergeNodes("nonexistent", removeId)).toThrow(
        "Cannot merge: node not found",
      );
    });

    it("should redirect edges from removed node to kept node", () => {
      const keepId = graph.addNode({
        title: "Keep",
        content: "K",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const removeId = graph.addNode({
        title: "Remove",
        content: "R",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const otherId = graph.addNode({
        title: "Other",
        content: "O",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(removeId, otherId, "supports");
      graph.mergeNodes(keepId, removeId);

      const edges = graph.getEdges(keepId);
      expect(edges.some((e) => e.from === keepId && e.to === otherId)).toBe(
        true,
      );
    });
  });

  describe("getAllNodes and getAllEdges", () => {
    it("should return all nodes", () => {
      graph.addNode({
        title: "N1",
        content: "C1",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      graph.addNode({
        title: "N2",
        content: "C2",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const nodes = graph.getAllNodes();
      expect(nodes).toHaveLength(2);
    });

    it("should return all edges", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");

      const edges = graph.getAllEdges();
      expect(edges).toHaveLength(1);
    });
  });
});

describe("KnowledgeReasoner", () => {
  let graph: KnowledgeGraph;
  let mockProvider: ModelProvider;

  beforeEach(() => {
    graph = new KnowledgeGraph("/tmp/test-reasoner");
    mockProvider = createMockProvider();
  });

  describe("reason", () => {
    it("should return empty chain when no matching nodes", async () => {
      const reasoner = new KnowledgeReasoner(graph, mockProvider);

      const chain = await reasoner.reason("nonexistent query");

      expect(chain.steps).toHaveLength(0);
      expect(chain.conclusion).toBe(
        "No relevant knowledge found in the graph.",
      );
      expect(chain.confidence).toBe(0);
    });

    it("should build reasoning chain from matching nodes", async () => {
      const nodeId = graph.addNode({
        title: "Test Fact",
        content: "This is a test fact",
        domain: "testing",
        source: "test",
        confidence: 0.8,
      });

      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          steps: [{ nodeId, contribution: "Used the test fact" }],
          conclusion: "Test conclusion",
          confidence: 0.7,
        }),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const chain = await reasoner.reason("test");

      expect(chain.query).toBe("test");
      expect(chain.steps.length).toBeGreaterThan(0);
      expect(chain.conclusion).toBe("Test conclusion");
      expect(chain.confidence).toBe(0.7);
      expect(chain.timestamp).toBeDefined();
    });

    it("should fall back to raw matches on LLM error", async () => {
      const nodeId = graph.addNode({
        title: "Fallback Node",
        content: "Fallback content",
        domain: "test",
        source: "test",
        confidence: 0.6,
      });

      mockProvider.chat = vi.fn().mockRejectedValue(new Error("LLM error"));

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const chain = await reasoner.reason("Fallback");

      expect(chain.steps).toHaveLength(1);
      expect(chain.conclusion).toContain("could not be constructed via LLM");
      expect(chain.confidence).toBe(0.3);
    });

    it("should use BFS to collect subgraph up to maxDepth", async () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeC = graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");
      graph.addEdge(nodeB, nodeC, "extends");

      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          steps: [{ nodeId: nodeA, contribution: "A" }],
          conclusion: "Conclusion",
          confidence: 0.5,
        }),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      await reasoner.reason("A", 2);

      const chatMock = mockProvider.chat as unknown as ReturnType<typeof vi.fn>;
      const calls = chatMock.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const messagesArg = calls[0][0] as { content: string }[];
      const prompt = messagesArg[messagesArg.length - 1]?.content || "";
      expect(prompt).toContain("A");
      expect(prompt).toContain("B");
    });

    it("should clamp confidence to [0, 1]", async () => {
      const nodeId = graph.addNode({
        title: "Clamp Test",
        content: "Content",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          steps: [{ nodeId, contribution: "Test" }],
          conclusion: "Test",
          confidence: 1.5,
        }),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const chain = await reasoner.reason("Clamp");

      expect(chain.confidence).toBe(1);
    });
  });

  describe("extractFromConversation", () => {
    it("should return empty array when LLM fails", async () => {
      mockProvider.chat = vi.fn().mockRejectedValue(new Error("API error"));

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const nodeIds = await reasoner.extractFromConversation([
        { role: "user", content: "Tell me about TypeScript" },
      ]);

      expect(nodeIds).toHaveLength(0);
    });

    it("should extract facts and create nodes from conversation", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            title: "TypeScript is typed",
            content: "TypeScript adds static types to JavaScript",
            domain: "programming",
            confidence: 0.9,
            relations: [],
          },
          {
            title: "TypeScript compiles to JS",
            content: "TypeScript is compiled to plain JavaScript",
            domain: "programming",
            confidence: 0.8,
            relations: [{ to_title: "TypeScript is typed", type: "extends" }],
          },
        ]),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const nodeIds = await reasoner.extractFromConversation(
        [{ role: "user", content: "What about TypeScript?" }],
        "programming",
      );

      expect(nodeIds).toHaveLength(2);
      expect(graph.getNode(nodeIds[0])).toBeDefined();
      expect(graph.getNode(nodeIds[1])).toBeDefined();
    });

    it("should create edges between extracted facts based on relations", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            title: "Fact A",
            content: "Content A",
            domain: "test",
            confidence: 0.8,
            relations: [{ to_title: "Fact B", type: "supports" }],
          },
          {
            title: "Fact B",
            content: "Content B",
            domain: "test",
            confidence: 0.7,
            relations: [],
          },
        ]),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      await reasoner.extractFromConversation([
        { role: "user", content: "Test" },
      ]);

      const factANode = graph.getAllNodes().find((n) => n.title === "Fact A");
      const factBNode = graph.getAllNodes().find((n) => n.title === "Fact B");

      const edgesFromA = graph.getEdges(factANode!.id);
      expect(
        edgesFromA.some((e) => e.to === factBNode!.id && e.type === "supports"),
      ).toBe(true);
    });

    it("should use default domain when none provided", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            title: "Test Fact",
            content: "Content",
            domain: "general",
            confidence: 0.5,
            relations: [],
          },
        ]),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      await reasoner.extractFromConversation([
        { role: "user", content: "Test" },
      ]);

      const node = graph.getAllNodes()[0];
      expect(node.domain).toBe("general");
    });

    it("should clamp confidence values", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            title: "High Confidence",
            content: "Content",
            domain: "test",
            confidence: 1.5,
            relations: [],
          },
        ]),
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      await reasoner.extractFromConversation([
        { role: "user", content: "Test" },
      ]);

      const node = graph.getAllNodes()[0];
      expect(node.confidence).toBe(1);
    });
  });

  describe("findPath", () => {
    it("should return empty array when no path exists", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const path = reasoner.findPath(nodeA, nodeB);

      expect(path).toHaveLength(0);
    });

    it("should return path between connected nodes", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const path = reasoner.findPath(nodeA, nodeB);

      expect(path).toHaveLength(1);
      expect(path[0].type).toBe("supports");
    });

    it("should return empty array for same node", () => {
      const nodeId = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const path = reasoner.findPath(nodeId, nodeId);

      expect(path).toHaveLength(0);
    });

    it("should find multi-hop path", () => {
      const nodeA = graph.addNode({
        title: "A",
        content: "A",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeB = graph.addNode({
        title: "B",
        content: "B",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });
      const nodeC = graph.addNode({
        title: "C",
        content: "C",
        domain: "test",
        source: "test",
        confidence: 0.5,
      });

      graph.addEdge(nodeA, nodeB, "supports");
      graph.addEdge(nodeB, nodeC, "extends");

      const reasoner = new KnowledgeReasoner(graph, mockProvider);
      const path = reasoner.findPath(nodeA, nodeC);

      expect(path).toHaveLength(2);
    });
  });

  describe("formatChainForContext", () => {
    it("should format reasoning chain as XML", () => {
      const reasoner = new KnowledgeReasoner(graph, mockProvider);

      const chain: ReasoningChain = {
        query: "test query",
        steps: [
          {
            nodeId: "1",
            nodeTitle: "Step 1",
            contribution: "First step contribution",
            edgeType: "supports",
          },
          {
            nodeId: "2",
            nodeTitle: "Step 2",
            contribution: "Second step contribution",
          },
        ],
        conclusion: "Final conclusion",
        confidence: 0.85,
        timestamp: new Date().toISOString(),
      };

      const formatted = reasoner.formatChainForContext(chain);

      expect(formatted).toContain('<reasoning_chain query="test query">');
      expect(formatted).toContain('<step node="Step 1" relation="supports">');
      expect(formatted).toContain('<step node="Step 2">');
      expect(formatted).toContain("Second step contribution");
      expect(formatted).toContain('<conclusion confidence="0.85">');
      expect(formatted).toContain("Final conclusion");
      expect(formatted).toContain("</reasoning_chain>");
    });

    it("should handle step without edgeType", () => {
      const reasoner = new KnowledgeReasoner(graph, mockProvider);

      const chain: ReasoningChain = {
        query: "simple",
        steps: [
          { nodeId: "1", nodeTitle: "Only Step", contribution: "Content" },
        ],
        conclusion: "Done",
        confidence: 0.5,
        timestamp: new Date().toISOString(),
      };

      const formatted = reasoner.formatChainForContext(chain);
      expect(formatted).toContain('<step node="Only Step">');
      expect(formatted).not.toContain("relation=");
    });
  });
});

describe("Knowledge types", () => {
  it("should define all expected edge types", () => {
    const edgeTypes: EdgeType[] = [
      "supports",
      "contradicts",
      "extends",
      "supersedes",
      "related",
      "requires",
      "caused_by",
    ];

    edgeTypes.forEach((type) => {
      expect(type).toBeDefined();
    });
  });

  it("should have required KnowledgeNode properties", () => {
    const node: KnowledgeNode = {
      id: "test-id",
      title: "Test",
      content: "Content",
      source: "test",
      domain: "testing",
      confidence: 0.8,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      accessCount: 0,
    };

    expect(node.id).toBe("test-id");
    expect(node.title).toBe("Test");
    expect(node.embedding).toBeUndefined();
  });

  it("should allow optional embedding on KnowledgeNode", () => {
    const node: KnowledgeNode = {
      id: "test-id",
      title: "With Embedding",
      content: "Content",
      source: "test",
      domain: "test",
      confidence: 0.5,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      accessCount: 0,
      embedding: [0.1, 0.2, 0.3],
    };

    expect(node.embedding).toHaveLength(3);
  });
});
