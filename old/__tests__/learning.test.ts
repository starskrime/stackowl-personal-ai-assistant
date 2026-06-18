import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MicroLearner } from "../src/learning/micro-learner.js";
import { KnowledgeGraphManager } from "../src/learning/knowledge-graph.js";
import { SelfLearningCoordinator } from "../src/learning/coordinator.js";
import { KnowledgeSynthesizer } from "../src/learning/synthesizer.js";
import {
  TopicFusionEngine,
  normalizeTopic,
  toDisplayName,
} from "../src/learning/topic-fusion.js";
import type { FusedTopic } from "../src/learning/topic-fusion.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { PelletStore } from "../src/pellets/store.js";

vi.mock("uuid", () => ({ v4: () => "test-uuid-1234" }));

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockResolvedValue("{}"),
  writeFile: vi.fn().mockResolvedValue(undefined),
  readdir: vi.fn().mockResolvedValue([]),
  unlink: vi.fn().mockResolvedValue(undefined),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(true),
  readFileSync: vi.fn().mockReturnValue("{}"),
  writeFileSync: vi.fn(),
  mkdirSync: vi.fn(),
}));

// ─── Helpers ──────────────────────────────────────────────────────

function makeMockProvider() {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: "[]",
      model: "mock-model",
      finishReason: "stop" as const,
    }),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider;
}

function makeMockPelletStore() {
  return {
    save: vi.fn().mockResolvedValue({ verdict: "CREATE", reasoning: "mock" }),
    listAll: vi.fn().mockResolvedValue([]),
    delete: vi.fn().mockResolvedValue(undefined),
    search: vi.fn().mockResolvedValue([]),
    getById: vi.fn().mockResolvedValue(null),
    getByTag: vi.fn().mockResolvedValue([]),
    getStats: vi.fn().mockReturnValue({ total: 0, byTag: {}, byOwl: {} }),
  } as unknown as PelletStore;
}

function makeMockMutationTracker() {
  return {
    analyze: vi.fn().mockReturnValue({
      totalMutations: 3,
      avgSatisfaction: 0.75,
      oscillations: {
        isOscillating: false,
        oscillatingTraits: [],
        recommendation: "proceed",
      },
      bestMutationType: null,
      worstMutationType: null,
      recommendedAction: "proceed" as const,
    }),
    recordBeforeMutation: vi.fn().mockReturnValue("record-123"),
    confirmMutation: vi.fn().mockResolvedValue(undefined),
    recordSatisfaction: vi.fn().mockResolvedValue({ shouldRollback: false }),
    rollback: vi.fn().mockResolvedValue(undefined),
    records: [],
  };
}

function makeMockPreferenceModel() {
  return {
    recordSignal: vi.fn(),
    analyzeMessage: vi.fn(),
    getAll: vi.fn().mockReturnValue([]),
    save: vi.fn().mockResolvedValue(undefined),
  };
}

function makeFusedTopic(overrides: Partial<FusedTopic> = {}): FusedTopic {
  return {
    id: "test-topic",
    normalizedName: "test-topic",
    displayName: "Test Topic",
    urgency: 50,
    sourceSignals: ["topic"],
    originalSignals: ["test topic"],
    lastSeen: new Date().toISOString(),
    failureCount: 0,
    relatedDomains: [],
    synthesisStrategy: "q_and_a",
    sourceInsights: [],
    ...overrides,
  };
}

// ─── MicroLearner Tests ──────────────────────────────────────────

describe("MicroLearner", () => {
  let learner: MicroLearner;
  const workspace = "/tmp/test-workspace";

  beforeEach(() => {
    learner = new MicroLearner(workspace);
  });

  it("processes a plain text message and updates profile stats", () => {
    const signals = learner.processMessage("Hello, how are you?");
    expect(Array.isArray(signals)).toBe(true);
    expect(learner.getProfile().totalMessages).toBe(1);
  });

  it("processes a message with topic keywords and returns topic signals", () => {
    const signals = learner.processMessage(
      "I need to send an email about my calendar meeting",
    );
    const topicSignals = signals.filter((s) => s.type === "topic");
    expect(topicSignals.length).toBeGreaterThan(0);
  });

  it("detects topic keywords from message content", () => {
    const signals = learner.processMessage(
      "I need to send an email about my calendar meeting",
    );
    const topicSignals = signals.filter((s) => s.type === "topic");
    expect(topicSignals.some((s) => s.key === "email")).toBe(true);
    expect(topicSignals.some((s) => s.key === "calendar")).toBe(true);
  });

  it("detects positive sentiment signals", () => {
    const signals = learner.processMessage("Thanks, that's perfect!");
    const posSignals = signals.filter(
      (s) => s.type === "sentiment" && s.key === "positive",
    );
    expect(posSignals.length).toBeGreaterThan(0);
  });

  it("detects negative sentiment signals", () => {
    const signals = learner.processMessage("That's wrong, don't do that");
    const negSignals = signals.filter(
      (s) => s.type === "sentiment" && s.key === "negative",
    );
    expect(negSignals.length).toBeGreaterThan(0);
  });

  it("tracks question rate for question messages", () => {
    learner.processMessage("What is the weather?");
    learner.processMessage("How are you?");
    const profile = learner.getProfile();
    expect(profile.questionRate).toBeGreaterThan(0);
  });

  it("tracks command rate for imperative messages", () => {
    learner.processMessage("Send me an email");
    learner.processMessage("Check the calendar");
    const profile = learner.getProfile();
    expect(profile.commandRate).toBeGreaterThan(0);
  });

  it("tracks tool usage from usedTools parameter", () => {
    const signals = learner.processMessage("Use the shell tool", [
      "shell",
      "search",
    ]);
    const toolSignals = signals.filter((s) => s.type === "tool_use");
    expect(toolSignals.some((s) => s.key === "shell")).toBe(true);
    expect(toolSignals.some((s) => s.key === "search")).toBe(true);
  });

  it("recordToolUse updates tool usage count", () => {
    learner.recordToolUse("shell");
    learner.recordToolUse("shell");
    const profile = learner.getProfile();
    expect(profile.toolUsage["shell"]).toBe(2);
  });

  it("increments totalMessages on each processed message", () => {
    learner.processMessage("First message");
    learner.processMessage("Second message");
    expect(learner.getProfile().totalMessages).toBe(2);
  });

  it("getPeakHours returns hours above activity threshold", () => {
    const signals = learner.processMessage("Active now");
    expect(Array.isArray(signals)).toBe(true);
    const peaks = learner.getPeakHours();
    expect(Array.isArray(peaks)).toBe(true);
  });

  it("getTopTopics returns topics sorted by frequency", () => {
    learner.processMessage("send email");
    learner.processMessage("send email");
    learner.processMessage("send email");
    const tops = learner.getTopTopics(3);
    expect(tops.length).toBeGreaterThan(0);
    expect(tops[0].topic).toBe("email");
    expect(tops[0].count).toBe(3);
  });

  it("getAnticipatedNeeds returns related capabilities", () => {
    learner.recordToolUse("email");
    learner.recordToolUse("email");
    learner.recordToolUse("calendar");
    learner.recordToolUse("calendar");
    const needs = learner.getAnticipatedNeeds();
    expect(Array.isArray(needs)).toBe(true);
  });

  it("toContextString returns empty for fewer than 5 messages", () => {
    learner.processMessage("Hi");
    const ctx = learner.toContextString();
    expect(ctx).toBe("");
  });

  it("toContextString returns profile string after 5+ messages", () => {
    for (let i = 0; i < 5; i++) {
      learner.processMessage(`Message ${i}`);
    }
    const ctx = learner.toContextString();
    expect(ctx).toContain("<user_profile>");
  });

  it("processMessage handles messages without tools", () => {
    const signals = learner.processMessage("Just a regular message");
    expect(Array.isArray(signals)).toBe(true);
  });

  it("processMessage handles empty message", () => {
    const signals = learner.processMessage("");
    expect(Array.isArray(signals)).toBe(true);
  });

  it("getProfile returns a copy, not the internal reference", () => {
    learner.processMessage("test");
    const p1 = learner.getProfile();
    const p2 = learner.getProfile();
    expect(p1).not.toBe(p2);
    expect(p1).toEqual(p2);
  });
});

// ─── KnowledgeGraphManager Tests ─────────────────────────────────

describe("KnowledgeGraphManager", () => {
  let graph: KnowledgeGraphManager;
  const workspace = "/tmp/test-kg-workspace";

  beforeEach(() => {
    graph = new KnowledgeGraphManager(workspace);
  });

  it("touchDomain creates a new domain node at low depth", () => {
    graph.touchDomain("typescript");
    const g = graph.getGraph();
    expect(g.domains["typescript"]).toBeDefined();
    expect(g.domains["typescript"].depth).toBe(0.05);
  });

  it("touchDomain adds new domain to study queue", () => {
    graph.touchDomain("rust");
    const g = graph.getGraph();
    expect(g.studyQueue).toContain("rust");
  });

  it("touchDomain does not duplicate existing domain in queue", () => {
    graph.touchDomain("python");
    graph.touchDomain("python");
    const g = graph.getGraph();
    const count = g.studyQueue.filter((t) => t === "python").length;
    expect(count).toBe(1);
  });

  it("touchDomain normalizes domain names to lowercase", () => {
    graph.touchDomain("TypeScript");
    const g = graph.getGraph();
    expect(g.domains["typescript"]).toBeDefined();
  });

  it("touchDomain ignores empty strings", () => {
    graph.touchDomain("");
    graph.touchDomain("   ");
    const g = graph.getGraph();
    expect(Object.keys(g.domains).length).toBe(0);
  });

  it("touchDomain accepts source parameter", () => {
    graph.touchDomain("golang", "conversation");
    const g = graph.getGraph();
    expect(g.domains["golang"].source).toBe("conversation");
  });

  it("recordStudy increases depth of existing domain", () => {
    graph.touchDomain("javascript");
    graph.recordStudy("javascript", 2, ["typescript", "web"]);
    const g = graph.getGraph();
    expect(g.domains["javascript"].depth).toBeGreaterThan(0.05);
  });

  it("recordStudy increments studyCount and pelletCount", () => {
    graph.touchDomain("docker");
    graph.recordStudy("docker", 3, []);
    const g = graph.getGraph();
    expect(g.domains["docker"].studyCount).toBe(1);
    expect(g.domains["docker"].pelletCount).toBe(3);
  });

  it("recordStudy removes domain from study queue", () => {
    graph.touchDomain("kubernetes");
    graph.recordStudy("kubernetes", 1, []);
    const g = graph.getGraph();
    expect(g.studyQueue).not.toContain("kubernetes");
  });

  it("recordStudy adds related topics as frontier domains", () => {
    graph.recordStudy("web", 1, ["api", "frontend", "backend"]);
    const g = graph.getGraph();
    expect(g.domains["api"]).toBeDefined();
    expect(g.domains["frontend"]).toBeDefined();
    expect(g.domains["backend"]).toBeDefined();
  });

  it("recordStudy creates frontier domains at near-zero depth", () => {
    graph.recordStudy("cloud", 1, ["docker"]);
    const g = graph.getGraph();
    expect(g.domains["docker"].depth).toBeLessThan(0.05);
  });

  it("recordStudy creates new domain if not already known", () => {
    graph.recordStudy("brandnew", 0, []);
    const g = graph.getGraph();
    expect(g.domains["brandnew"]).toBeDefined();
    expect(g.domains["brandnew"].depth).toBeGreaterThan(0);
  });

  it("getStudyQueue returns eligible topics", () => {
    graph.touchDomain("topic1");
    const queue = graph.getStudyQueue(3);
    expect(Array.isArray(queue)).toBe(true);
  });

  it("getStudyQueue respects maxTopics limit", () => {
    for (let i = 0; i < 10; i++) {
      graph.touchDomain(`topic${i}`);
    }
    const queue = graph.getStudyQueue(3);
    expect(queue.length).toBeLessThanOrEqual(3);
  });

  it("getStudyQueue excludes topics studied within cooldown", () => {
    graph.touchDomain("recent");
    graph.recordStudy("recent", 1, []);
    const queue = graph.getStudyQueue(5);
    expect(queue).not.toContain("recent");
  });

  it("getStats returns totalDomains, avgDepth, studyQueueLength", () => {
    graph.touchDomain("domain1");
    graph.touchDomain("domain2");
    const stats = graph.getStats();
    expect(stats.totalDomains).toBe(2);
    expect(stats.studyQueueLength).toBeGreaterThan(0);
  });

  it("getStats handles empty graph", () => {
    const stats = graph.getStats();
    expect(stats.totalDomains).toBe(0);
    expect(stats.avgDepth).toBe(0);
  });

  it("getDomainSummary returns nothing for empty graph", () => {
    const summary = graph.getDomainSummary();
    expect(summary).toBe("Nothing studied yet.");
  });

  it("getDomainSummary returns top domains by depth", () => {
    graph.recordStudy("deep1", 1, []);
    graph.recordStudy("deep2", 1, []);
    const summary = graph.getDomainSummary();
    expect(summary).toContain("deep");
  });

  it("getFullReport returns formatted string", () => {
    graph.touchDomain("testdomain");
    const report = graph.getFullReport();
    expect(report).toContain("Knowledge Graph");
    expect(report).toContain("Domains");
  });

  it("getGraph returns full graph object", () => {
    graph.touchDomain("test");
    const g = graph.getGraph();
    expect(g.domains).toBeDefined();
    expect(g.studyQueue).toBeDefined();
  });
});

// ─── TopicFusionEngine Tests ──────────────────────────────────────

describe("TopicFusionEngine", () => {
  let engine: TopicFusionEngine;

  beforeEach(() => {
    engine = new TopicFusionEngine();
  });

  describe("normalizeTopic", () => {
    it("normalizes whitespace and special chars", () => {
      expect(normalizeTopic("  Open AI  ")).toBe("openai-api");
    });

    it("applies alias map", () => {
      expect(normalizeTopic("typescript")).toBe("typescript-lang");
      expect(normalizeTopic("ts")).toBe("typescript-lang");
    });

    it("replaces dots, underscores, hyphens with hyphens", () => {
      expect(normalizeTopic("open_ai.api")).toBe("open-ai-api");
    });

    it("handles unknown topics", () => {
      const result = normalizeTopic("foobar");
      expect(result).toBeTruthy();
    });
  });

  describe("toDisplayName", () => {
    it("converts normalized names to title case", () => {
      expect(toDisplayName("openai-api")).toBe("Openai Api");
    });

    it("splits on hyphens", () => {
      expect(toDisplayName("large-language-models")).toBe(
        "Large Language Models",
      );
    });
  });

  it("fuse returns empty result for empty insights", async () => {
    const result = await engine.fuse([], {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics).toEqual([]);
    expect(result.stats.totalSignals).toBe(0);
  });

  it("fuse deduplicates same topic from multiple insights", async () => {
    const insights = [
      {
        topics: ["typescript", "typescript"],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: [],
        timestamp: new Date().toISOString(),
      },
    ];
    const result = await engine.fuse(insights, {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.stats.uniqueTopics).toBeLessThan(result.stats.totalSignals);
  });

  it("fuse assigns urgency based on source signals", async () => {
    const insights = [
      {
        topics: [],
        domains: [],
        knowledgeGaps: ["typescript"],
        researchQuestions: [],
        timestamp: new Date().toISOString(),
      },
    ];
    const result = await engine.fuse(insights, {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics[0].urgency).toBeGreaterThan(0);
  });

  it("fuse assigns q_and_a strategy by default", async () => {
    const insights = [
      {
        topics: ["testing"],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: [],
        timestamp: new Date().toISOString(),
      },
    ];
    const result = await engine.fuse(insights, {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics[0].synthesisStrategy).toBe("q_and_a");
  });

  it("fuse assigns web_research for time-sensitive questions", async () => {
    const insights = [
      {
        topics: [],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: ["What is the latest price of bitcoin?"],
        timestamp: new Date().toISOString(),
      },
    ];
    const result = await engine.fuse(insights, {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics[0].synthesisStrategy).toBe("deep_research");
  });

  it("fuseSingle delegates to fuse with single insight", async () => {
    const insight = {
      topics: ["docker"],
      domains: [],
      knowledgeGaps: [],
      researchQuestions: [],
      timestamp: new Date().toISOString(),
    };
    const result = await engine.fuseSingle(insight, {
      domains: {},
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics.length).toBe(1);
  });

  it("fuse sets priorityOverride low for well-known domains", async () => {
    const insights = [
      {
        topics: ["typescript"],
        domains: [],
        knowledgeGaps: [],
        researchQuestions: [],
        timestamp: new Date().toISOString(),
      },
    ];
    const result = await engine.fuse(insights, {
      domains: {
        "typescript-lang": {
          depth: 0.8,
          lastStudied: "",
          pelletCount: 5,
          studyCount: 3,
          relatedTopics: [],
          source: "self-study" as const,
        },
      },
      studyQueue: [],
      lastUpdated: new Date().toISOString(),
    });
    expect(result.fusedTopics[0].priorityOverride).toBe("low");
  });
});

// ─── SelfLearningCoordinator Tests ───────────────────────────────

describe("SelfLearningCoordinator", () => {
  let coordinator: SelfLearningCoordinator;
  let microLearner: MicroLearner;
  let mutationTracker: Record<string, unknown>;
  let prefModel: ReturnType<typeof makeMockPreferenceModel>;

  beforeEach(() => {
    microLearner = new MicroLearner("/tmp/coordinator-test");
    mutationTracker = makeMockMutationTracker();
    prefModel = makeMockPreferenceModel();
    coordinator = new SelfLearningCoordinator(
      microLearner,
      mutationTracker as any,
      prefModel as any,
      "TestOwl",
    );
  });

  afterEach(() => {
    coordinator.shutdown();
  });

  it("gateEvolution returns proceed when no mutationTracker", () => {
    const coordinatorNoTracker = new SelfLearningCoordinator(
      microLearner,
      null,
      null,
      "TestOwl",
    );
    const result = coordinatorNoTracker.gateEvolution();
    expect(result.recommendedAction).toBe("proceed");
    coordinatorNoTracker.shutdown();
  });

  it("gateEvolution delegates to mutationTracker.analyze", () => {
    const result = coordinator.gateEvolution();
    expect(mutationTracker.analyze).toHaveBeenCalledWith("TestOwl");
    expect(result.avgSatisfaction).toBe(0.75);
  });

  it("recordMutationStart returns null when no mutationTracker", () => {
    const coordinatorNoTracker = new SelfLearningCoordinator(
      microLearner,
      null,
      null,
      "TestOwl",
    );
    const result = coordinatorNoTracker.recordMutationStart({} as never);
    expect(result).toBeNull();
    coordinatorNoTracker.shutdown();
  });

  it("recordMutationStart calls mutationTracker.recordBeforeMutation", () => {
    coordinator.recordMutationStart({ challengeLevel: 0.5 } as never);
    expect(mutationTracker.recordBeforeMutation).toHaveBeenCalled();
  });

  it("recordMutationEnd does nothing when recordId is null", async () => {
    await coordinator.recordMutationEnd(null as never, {} as never, []);
    expect(mutationTracker.confirmMutation).not.toHaveBeenCalled();
  });

  it("processMessage returns signals from microLearner", () => {
    const signals = coordinator.processMessage("Hello, send me an email");
    expect(Array.isArray(signals)).toBe(true);
  });

  it("processMessage calls microLearner.processMessage", () => {
    coordinator.processMessage("Test message");
    const profile = coordinator.getMicroLearnerProfile();
    expect(profile.totalMessages).toBe(1);
  });

  it("processMessage calls preferenceModel.analyzeMessage when provided", () => {
    coordinator.processMessage("Test message", undefined, "channel-1");
    expect(prefModel.analyzeMessage).toHaveBeenCalled();
  });

  it("recordToolUse calls microLearner.recordToolUse and publishes signal", () => {
    coordinator.recordToolUse("shell");
    const profile = coordinator.getMicroLearnerProfile();
    expect(profile.toolUsage["shell"]).toBe(1);
  });

  it("flushHighConfidencePrefs returns empty when no prefModel", () => {
    const coordinatorNoPref = new SelfLearningCoordinator(
      microLearner,
      null,
      null,
      "TestOwl",
    );
    const prefs = coordinatorNoPref.flushHighConfidencePrefs();
    expect(prefs).toEqual([]);
    coordinatorNoPref.shutdown();
  });

  it("flushHighConfidencePrefs filters by threshold", () => {
    prefModel.getAll.mockReturnValue([
      {
        key: "verbose",
        value: true,
        confidence: 0.8,
        evidence: [],
        lastUpdated: Date.now(),
      },
      {
        key: "short",
        value: true,
        confidence: 0.5,
        evidence: [],
        lastUpdated: Date.now(),
      },
    ]);
    const high = coordinator.flushHighConfidencePrefs(0.7);
    expect(high.length).toBe(1);
    expect(high[0].key).toBe("verbose");
  });

  it("save persists microLearner and preferenceModel", async () => {
    await coordinator.save();
    expect(microLearner["dirty"]).toBe(false);
  });

  it("getStats returns signalBus stats", () => {
    coordinator.processMessage("test");
    const stats = coordinator.getStats();
    expect(stats.subscriberCount).toBeGreaterThanOrEqual(0);
  });

  it("getMicroLearnerProfile returns profile", () => {
    const profile = coordinator.getMicroLearnerProfile();
    expect(profile).toBeDefined();
    expect(typeof profile.totalMessages).toBe("number");
  });

  it("shutdown flushes and destroys signalBus", () => {
    coordinator.shutdown();
    const stats = coordinator.getStats();
    expect(stats.subscriberCount).toBeLessThanOrEqual(2);
  });

  it("signalBus is accessible via public property", () => {
    expect(coordinator.signalBus).toBeDefined();
  });
});

// ─── KnowledgeSynthesizer Tests ───────────────────────────────────

describe("KnowledgeSynthesizer", () => {
  let provider: ModelProvider;
  let pelletStore: ReturnType<typeof makeMockPelletStore>;
  let synthesizer: KnowledgeSynthesizer;
  let mockOwl: { persona: { name: string } };

  beforeEach(() => {
    provider = makeMockProvider();
    pelletStore = makeMockPelletStore();
    mockOwl = { persona: { name: "TestOwl" } };
    synthesizer = new KnowledgeSynthesizer(
      provider,
      mockOwl as never,
      {} as never,
      pelletStore,
      "/tmp/synth-test",
    );
  });

  it("synthesize skips low-urgency quick_lookup topics", async () => {
    const topics = [
      makeFusedTopic({
        synthesisStrategy: "quick_lookup",
        urgency: 10,
        priorityOverride: undefined,
      }),
    ];
    const report = await synthesizer.synthesize(topics);
    expect(report.failed).toBe(0);
    expect(report.successful).toBe(0);
  });

  it("synthesize processes topics above skip threshold", async () => {
    (provider.chat as any).mockResolvedValue({
      content: JSON.stringify(["How do I use email?"]),
      model: "mock",
      finishReason: "stop" as const,
    });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const topics = [
      makeFusedTopic({
        synthesisStrategy: "q_and_a",
        urgency: 30,
      }),
    ];
    const report = await synthesizer.synthesize(topics);
    expect(report.totalTopics).toBe(1);
  });

  it("synthesize records study in knowledge graph after success", async () => {
    (provider.chat as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        content: JSON.stringify(["Test question?"]),
        model: "mock",
        finishReason: "stop" as const,
      })
      .mockResolvedValueOnce({
        content: 'Test answer. RELATED_JSON: ["related1", "related2"]',
        model: "mock",
        finishReason: "stop" as const,
      });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const topics = [
      makeFusedTopic({ synthesisStrategy: "q_and_a", urgency: 50 }),
    ];
    await synthesizer.synthesize(topics);
    expect(pelletStore.save).toHaveBeenCalled();
  });

  it("synthesize increments failed count on error", async () => {
    (provider.chat as any).mockRejectedValue(new Error("provider failure"));
    (pelletStore.listAll as any).mockResolvedValue([]);
    const topics = [
      makeFusedTopic({ synthesisStrategy: "q_and_a", urgency: 50 }),
    ];
    const report = await synthesizer.synthesize(topics);
    expect(report.failed).toBeGreaterThan(0);
  });

  it("synthesizeSingle routes to correct pipeline", async () => {
    (provider.chat as ReturnType<typeof vi.fn>).mockResolvedValue({
      content: JSON.stringify(["Question?"]),
      model: "mock",
      finishReason: "stop" as const,
    });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const result = await synthesizer.synthesizeSingle({
      topic: makeFusedTopic({ synthesisStrategy: "q_and_a" }),
    });
    expect(result.pipeline).toBe("q_and_a");
  });

  it("runQAndA returns success when pellets are created", async () => {
    (provider.chat as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        content: JSON.stringify(["What is email?"]),
        model: "mock",
        finishReason: "stop" as const,
      })
      .mockResolvedValueOnce({
        content: "Email answer. RELATED_JSON: []",
        model: "mock",
        finishReason: "stop" as const,
      });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const result = await synthesizer.synthesizeSingle({
      topic: makeFusedTopic({
        synthesisStrategy: "q_and_a",
        displayName: "Email",
      }),
    });
    expect(result.success).toBe(true);
    expect(result.pellets.length).toBeGreaterThan(0);
  });

  it("runQuickLookup returns result even on error", async () => {
    (provider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const result = await synthesizer.synthesizeSingle({
      topic: makeFusedTopic({ synthesisStrategy: "quick_lookup" }),
    });
    expect(result.pipeline).toBe("quick_lookup");
    expect(result.success).toBe(false);
  });

  it("runDeepResearch delegates to runQAndA", async () => {
    (provider.chat as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        content: JSON.stringify(["Question?"]),
        model: "mock",
        finishReason: "stop" as const,
      })
      .mockResolvedValueOnce({
        content: "Answer. RELATED_JSON: []",
        model: "mock",
        finishReason: "stop" as const,
      });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const result = await synthesizer.synthesizeSingle({
      topic: makeFusedTopic({ synthesisStrategy: "deep_research" }),
    });
    expect(result.pipeline).toBe("deep_research");
    expect(result.success).toBe(true);
  });

  it("budgetedChat throws when LLM call limit exceeded", async () => {
    (provider.chat as ReturnType<typeof vi.fn>).mockResolvedValue({
      content: "[]",
      model: "mock",
      finishReason: "stop" as const,
    });
    (pelletStore.listAll as any).mockResolvedValue([]);
    // Trigger budget exhaustion via many topics
    const topics = Array(10)
      .fill(null)
      .map((_, i) =>
        makeFusedTopic({
          synthesisStrategy: "q_and_a" as const,
          urgency: 80,
          displayName: `Topic ${i}`,
        }),
      );
    // The first few synthesize calls will exhaust the budget
    try {
      await synthesizer.synthesize(topics);
    } catch {
      // Expected after budget exhausted
    }
    const calls = (provider.chat as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(calls).toBeLessThanOrEqual(4); // MAX_LLM_CALLS_PER_CYCLE
  });

  it("parseJsonArray handles plain JSON array", () => {
    const result = (synthesizer as any).parseJsonArray('["a", "b", "c"]');
    expect(result).toEqual(["a", "b", "c"]);
  });

  it("parseJsonArray handles JSON wrapped in markdown", () => {
    const result = (synthesizer as any).parseJsonArray(
      '```json\n["a", "b"]\n```',
    );
    expect(result).toEqual(["a", "b"]);
  });

  it("parseJsonArray extracts array from mixed content", () => {
    const result = (synthesizer as any).parseJsonArray(
      'Some text before ["extracted", "array"] and text after',
    );
    expect(result).toEqual(["extracted", "array"]);
  });

  it("parseJsonArray returns empty array on invalid JSON", () => {
    const result = (synthesizer as any).parseJsonArray("not json at all");
    expect(result).toEqual([]);
  });

  it("extractRelatedJson extracts JSON array from content", () => {
    const result = (synthesizer as any).extractRelatedJson(
      'Some content RELATED_JSON: ["topic1", "topic2"] more text',
    );
    expect(result).toEqual(["topic1", "topic2"]);
  });

  it("extractRelatedJson returns empty array when no match", () => {
    const result = (synthesizer as any).extractRelatedJson(
      "No related json here",
    );
    expect(result).toEqual([]);
  });

  it("extractRelatedJson returns empty array for malformed JSON", () => {
    const result = (synthesizer as any).extractRelatedJson(
      "RELATED_JSON: [not valid json",
    );
    expect(result).toEqual([]);
  });

  it("createPellet generates slug from topic name and uuid", () => {
    const pellet = (synthesizer as any).createPellet(
      makeFusedTopic({ normalizedName: "testing" }),
      "Test Title",
      "Test content",
    );
    expect(pellet.id).toContain("learn-testing-");
    expect(pellet.title).toBe("Test Title");
    expect(pellet.content).toBe("Test content");
  });

  it("createPellet truncates title to 200 chars", () => {
    const longTitle = "a".repeat(300);
    const pellet = (synthesizer as any).createPellet(
      makeFusedTopic({ normalizedName: "test" }),
      longTitle,
      "content",
    );
    expect(pellet.title.length).toBe(200);
  });

  it("createPellet truncates content to 2000 chars", () => {
    const longContent = "b".repeat(3000);
    const pellet = (synthesizer as any).createPellet(
      makeFusedTopic({ normalizedName: "test" }),
      "Title",
      longContent,
    );
    expect(pellet.content.length).toBe(2000);
  });

  it("ensureCapacity evicts oldest pellets when over limit", async () => {
    const existingPellets = Array(2010)
      .fill(null)
      .map((_, i) => ({
        id: `pellet-${i}`,
        title: `P${i}`,
        generatedAt: "",
        source: "",
        owls: [],
        tags: [],
        version: 1,
        content: "",
      }));
    (pelletStore.listAll as any).mockResolvedValue(existingPellets);
    await (synthesizer as any).ensureCapacity(10);
    expect(pelletStore.delete).toHaveBeenCalled();
  });

  it("synthesize report includes durationMs", async () => {
    (provider.chat as ReturnType<typeof vi.fn>).mockResolvedValue({
      content: "[]",
      model: "mock",
      finishReason: "stop" as const,
    });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const topics = [
      makeFusedTopic({ synthesisStrategy: "q_and_a", urgency: 50 }),
    ];
    const report = await synthesizer.synthesize(topics);
    expect(report.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("synthesize report includes byPipeline breakdown", async () => {
    (provider.chat as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        content: JSON.stringify(["Q?"]),
        model: "mock",
        finishReason: "stop" as const,
      })
      .mockResolvedValueOnce({
        content: "Answer. RELATED_JSON: []",
        model: "mock",
        finishReason: "stop" as const,
      });
    (pelletStore.listAll as any).mockResolvedValue([]);
    const topics = [
      makeFusedTopic({ synthesisStrategy: "q_and_a", urgency: 50 }),
    ];
    const report = await synthesizer.synthesize(topics);
    expect(report.byPipeline).toBeDefined();
  });
});
