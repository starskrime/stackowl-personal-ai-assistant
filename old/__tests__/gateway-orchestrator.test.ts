import { describe, it, expect } from "vitest";
import { OwlOrchestrator } from "../src/engine/orchestrator.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

describe("Gateway orchestrator integration", () => {
  it("OwlOrchestrator can be imported from engine/orchestrator", () => {
    expect(OwlOrchestrator).toBeDefined();
  });

  it("OwlOrchestrator.run() returns content string", async () => {
    const dir = mkdtempSync(join(tmpdir(), "owl-gw-"));
    try {
      const db = new MemoryDatabase(dir);
      const mockProvider = {
        name: "mock",
        chat: async () => ({ content: "Hello! [DONE]", toolCalls: [], usage: { promptTokens: 10, completionTokens: 10 }, model: "mock", finishReason: "stop" as const }),
        chatWithTools: async () => ({ content: "Hello! [DONE]", toolCalls: [], usage: { promptTokens: 10, completionTokens: 10 }, model: "mock", finishReason: "stop" as const }),
      };
      const mockOwl = { persona: { name: "Atlas", emoji: "🦉", systemPrompt: "" }, dna: { riskTolerance: "balanced", challengeLevel: "medium" } };
      const orch = new OwlOrchestrator({ owl: mockOwl as any, provider: mockProvider as any, config: {} as any, db });
      const result = await orch.run("hi", { sessionId: "s1", userId: "u1" });
      expect(typeof result.content).toBe("string");
      expect(result.content.length).toBeGreaterThan(0);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
