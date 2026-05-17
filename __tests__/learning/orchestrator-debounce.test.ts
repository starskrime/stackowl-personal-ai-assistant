// __tests__/learning/orchestrator-debounce.test.ts
import { describe, it, expect, vi } from "vitest";
import { tmpdir } from "node:os";
import { LearningOrchestrator } from "../../src/learning/orchestrator.js";
import type { ChatMessage } from "../../src/providers/base.js";

const MESSAGES: ChatMessage[] = [
  { role: "user", content: "hello" },
  { role: "assistant", content: "hi" },
];

function makeMockOrchestrator() {
  const mockExtract = vi.fn().mockResolvedValue({
    topics: [],
    knowledgeGaps: [],
    timestamp: new Date().toISOString(),
  });

  const mockProvider = {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: "mock", model: "m", finishReason: "stop" }),
    chatWithTools: vi.fn(),
    stream: vi.fn(),
    listModels: vi.fn(),
    healthCheck: vi.fn(),
  };

  const mockOwl = {
    persona: { name: "test-owl", systemPrompt: "", traits: {}, dna: {} },
    config: {},
  } as any;

  const orch = new LearningOrchestrator(
    mockProvider as any,
    mockOwl,
    {} as any,
    undefined as any,
    tmpdir(),
    undefined,
  );

  // Inject mock extractor so no real LLM calls are made
  (orch as any).extractor = { extract: mockExtract };

  // Inject a minimal graphManager stub so non-debounced paths don't throw
  (orch as any).graphManager = {
    load: vi.fn().mockResolvedValue(undefined),
    getGraph: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
    save: vi.fn().mockResolvedValue(undefined),
    touchDomain: vi.fn(),
    getStats: vi.fn().mockReturnValue({ totalDomains: 0 }),
    getStudyQueue: vi.fn().mockReturnValue([]),
    getFullReport: vi.fn().mockReturnValue(""),
  };

  return { orch, mockExtract };
}

describe("LearningOrchestrator processConversation debounce", () => {
  it("skips the pipeline for the first 4 calls", async () => {
    const { orch, mockExtract } = makeMockOrchestrator();

    for (let i = 0; i < 4; i++) {
      await orch.processConversation(MESSAGES);
    }

    expect(mockExtract).not.toHaveBeenCalled();
  });

  it("runs the pipeline on the 5th call", async () => {
    const { orch, mockExtract } = makeMockOrchestrator();

    for (let i = 0; i < 5; i++) {
      await orch.processConversation(MESSAGES);
    }

    expect(mockExtract).toHaveBeenCalledTimes(1);
  });

  it("runs the pipeline again on the 10th call (and only then)", async () => {
    const { orch, mockExtract } = makeMockOrchestrator();

    for (let i = 0; i < 10; i++) {
      await orch.processConversation(MESSAGES);
    }

    expect(mockExtract).toHaveBeenCalledTimes(2);
  });

  it("debounced calls still return a LearningCycle with success=true", async () => {
    const { orch } = makeMockOrchestrator();

    const cycle = await orch.processConversation(MESSAGES);

    expect(cycle).toBeDefined();
    expect(cycle.success).toBe(true);
    expect(cycle.trigger).toBe("reactive");
  });
});
