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
});
