// __tests__/learning-orchestrator-proactive.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LearningOrchestrator } from "../src/learning/orchestrator.js";
import type { ProactiveContext } from "../src/learning/orchestrator.js";

function makeMockOrchestrator() {
  const mockSynthesize = vi.fn().mockResolvedValue({
    pelletsCreated: 1,
    insightsGenerated: 0,
    connectionsFormed: 0,
  });

  const mockGetStudyQueue = vi.fn().mockReturnValue([]);
  const mockTouchDomain = vi.fn();

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
    "/tmp",
    undefined,
  );

  // Inject mock graph manager
  (orch as any).graphManager = {
    getStudyQueue: mockGetStudyQueue,
    touchDomain: mockTouchDomain,
    getGraph: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
  };
  // Inject mock synthesizer
  (orch as any).synthesizer = { synthesize: mockSynthesize };

  return { orch, mockSynthesize, mockGetStudyQueue };
}

describe("LearningOrchestrator.runProactiveSession", () => {
  it("returns zeroed cycle when no context and empty KG queue", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([]);
    const cycle = await orch.runProactiveSession();
    expect(mockSynthesize).not.toHaveBeenCalled();
    expect(cycle.topicsPrioritized).toBe(0);
  });

  it("calls synthesizer with failureDensityTopics when present", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([]);
    const ctx: ProactiveContext = { failureDensityTopics: ["web_fetch", "shell"] };
    await orch.runProactiveSession(ctx);
    expect(mockSynthesize).toHaveBeenCalledOnce();
  });

  it("calls synthesizer with KG topics when no failure topics", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "TypeScript generics", priority: 0.8 },
    ]);
    await orch.runProactiveSession({});
    expect(mockSynthesize).toHaveBeenCalledOnce();
  });

  it("failure topics take priority over KG topics when both present", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "TypeScript generics", priority: 0.8 },
    ]);
    const ctx: ProactiveContext = {
      failureDensityTopics: ["web_fetch"],
      maxTopics: 3,
    };
    const cycle = await orch.runProactiveSession(ctx);
    // Synthesizer called with failure topic, not KG topic
    const synthesizeArg = mockSynthesize.mock.calls[0][0] as string[];
    expect(synthesizeArg).toContain("web_fetch");
  });

  it("respects maxTopics cap", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "topic1", priority: 0.9 },
      { normalizedName: "topic2", priority: 0.8 },
      { normalizedName: "topic3", priority: 0.7 },
    ]);
    await orch.runProactiveSession({ maxTopics: 1 });
    const topics = mockSynthesize.mock.calls[0][0] as string[];
    expect(topics).toHaveLength(1);
  });
});
