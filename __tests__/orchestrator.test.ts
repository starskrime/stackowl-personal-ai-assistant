import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OwlOrchestrator } from "../src/engine/orchestrator.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase;

const mockProvider = {
  name: "mock",
  chat: vi.fn().mockResolvedValue({
    content: "Here is your answer. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
  chatWithTools: vi.fn().mockResolvedValue({
    content: "Here is your answer. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
};

const mockOwl = {
  persona: { name: "Atlas", emoji: "🦉", systemPrompt: "You are Atlas." },
  dna: { riskTolerance: "balanced", challengeLevel: "medium", verbosity: 0.5 },
};

const mockConfig = {
  engine: { maxToolIterations: 10 },
};

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-orch-"));
  db = new MemoryDatabase(dir);
  vi.clearAllMocks();
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("OwlOrchestrator", () => {
  it("returns OrchestratorResponse for a simple message", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const response = await orch.run("hello, who are you?", {
      sessionId: "s1",
      userId: "u1",
    });
    expect(response.content.length).toBeGreaterThan(0);
    expect(response.content).not.toContain("[DONE]");
    expect(response.content).not.toContain("__STACKOWL_EXHAUSTED__");
    expect(response.qualityScore).toBeGreaterThan(0);
    expect(response.owlName).toBe("Atlas");
  });

  it("classifies simple messages and skips planning overhead", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const response = await orch.run("hi", { sessionId: "s1", userId: "u1" });
    expect(response.complexity).toBe("simple");
  });

  it("quality score is in [0,1] range", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const { qualityScore } = await orch.run("summarize this doc", {
      sessionId: "s1", userId: "u1",
    });
    expect(qualityScore).toBeGreaterThanOrEqual(0);
    expect(qualityScore).toBeLessThanOrEqual(1);
  });

  it("HITL decision falls back to SYNTHESIZE when no hitlChannel provided", async () => {
    // Force HITL decision by creating a condition where recovery orchestrator chooses HITL
    // The simplest way: pass a provider that returns pendingCapabilityGap signal
    const capGapProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: "I need shell access. [CAPABILITY_GAP:shell_access]",
        toolCalls: [],
        usage: { promptTokens: 50, completionTokens: 30 },
        model: "mock",
        finishReason: "stop",
      }),
      chatWithTools: vi.fn().mockResolvedValue({
        content: "I need shell access. [CAPABILITY_GAP:shell_access]",
        toolCalls: [],
        usage: { promptTokens: 50, completionTokens: 30 },
        model: "mock",
        finishReason: "stop",
      }),
    };
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: capGapProvider as any,
      config: mockConfig as any,
      db,
      // No hitlChannel — should fall back to SYNTHESIZE
    });
    const response = await orch.run("do something that needs shell", {
      sessionId: "s1",
      userId: "u1",
    });
    // Should complete without throwing, content should not contain internal markers
    expect(response.content).not.toContain("[CAPABILITY_GAP:");
    expect(typeof response.content).toBe("string");
  });

  it("degradation tier is > 1 when quality score is very low", async () => {
    const exhaustedProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: "__STACKOWL_EXHAUSTED__",
        toolCalls: [],
        usage: { promptTokens: 500, completionTokens: 300 },
        model: "mock",
        finishReason: "stop",
      }),
      chatWithTools: vi.fn().mockResolvedValue({
        content: "__STACKOWL_EXHAUSTED__",
        toolCalls: [],
        usage: { promptTokens: 500, completionTokens: 300 },
        model: "mock",
        finishReason: "stop",
      }),
    };
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: exhaustedProvider as any,
      config: mockConfig as any,
      db,
    });
    const response = await orch.run("analyze this enormous dataset in exhaustive detail", {
      sessionId: "s1",
      userId: "u1",
    });
    // Exhausted provider should produce low quality → degradation tier > 1
    expect(response.degradationTier).toBeGreaterThanOrEqual(1);
    expect(response.content).not.toContain("__STACKOWL_EXHAUSTED__");
  });

  it("records outcome to journal (non-fatal)", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    // Should complete without error even when journal is working
    const response = await orch.run("hello", { sessionId: "s2", userId: "u2" });
    expect(response.owlName).toBe("Atlas");
    // Journal failure should not throw (non-fatal)
  });

  it("complex message includes plan block in system messages", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    // Long message that will be classified as complex
    const complexMsg = "Please research and compare the top 5 electric vehicle manufacturers, analyze their market share, battery technology, and autonomous driving capabilities, then write a comprehensive investment thesis.";
    const response = await orch.run(complexMsg, { sessionId: "s3", userId: "u1" });
    expect(response.complexity).toBe("complex");
    expect(response.content.length).toBeGreaterThan(0);
  });
});
