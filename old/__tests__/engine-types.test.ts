import { describe, it, expect } from "vitest";
import type {
  TurnRequest, TurnResult, TaskLedger, SubGoal,
  RunHealth, HealthSignal, Decision, TokenBudget,
  OrchestratorResponse, FailedToolCall,
} from "../src/engine/types.js";

describe("engine types compile", () => {
  it("TurnResult has typed signals (no text markers)", () => {
    const r: TurnResult = {
      content: "hello",
      toolCalls: [],
      toolResults: [],
      tokensUsed: 100,
      doneSignal: false,
      budgetExhausted: false,
      failedTools: [],
      providerUsed: "anthropic",
      modelUsed: "claude-sonnet-4-6",
    };
    expect(r.budgetExhausted).toBe(false);
    expect(r.doneSignal).toBe(false);
  });

  it("TaskLedger has all required fields", () => {
    const ledger: TaskLedger = {
      id: "l1",
      goal: "research EVs",
      subGoals: [],
      expectedOutput: "comparison table",
      complexity: "medium",
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
      createdAt: Date.now(),
    };
    expect(ledger.complexity).toBe("medium");
  });

  it("Decision is one of five values", () => {
    const d: Decision = "CONTINUE";
    expect(["CONTINUE","REPLAN","HITL","SYNTHESIZE","DEGRADE"]).toContain(d);
  });

  it("DegradationTier is 1-4", () => {
    const t1: import("../src/engine/types.js").DegradationTier = 1;
    const t4: import("../src/engine/types.js").DegradationTier = 4;
    expect(t1).toBe(1);
    expect(t4).toBe(4);
  });

  it("HitlChannel interface shape", () => {
    const ch: import("../src/engine/types.js").HitlChannel = {
      pause: async (_req) => ({ approved: true, timedOut: false }),
    };
    expect(ch.pause).toBeDefined();
  });
});
