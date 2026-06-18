import { describe, it, expect } from "vitest";
import { decide } from "../src/engine/recovery-orchestrator.js";
import type { RunHealth, TurnResult, TaskLedger } from "../src/engine/types.js";

const baseHealth = (): RunHealth => ({
  iteration: 0, tokensConsumed: 100, tokenBudget: 8000,
  consecutiveFailures: 0, uniqueToolsAttempted: new Set(["web_search"]),
  allToolsFailed: false, spinningDetected: false, providerSwitchCount: 0,
  stuckOnSubGoalId: null, signals: [],
});
const baseTurn = (): TurnResult => ({
  content: "thinking", toolCalls: [], toolResults: [],
  tokensUsed: 100, doneSignal: false, budgetExhausted: false,
  failedTools: [], providerUsed: "anthropic", modelUsed: "claude-sonnet-4-6",
});
const baseLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "medium", estimatedTurns: 5, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});
const baseDna = { riskTolerance: "balanced" as const, challengeLevel: "medium" as const };

describe("decide()", () => {
  it("CONTINUE when healthy", () => { expect(decide(baseHealth(), baseTurn(), baseLedger(), baseDna)).toBe("CONTINUE"); });
  it("SYNTHESIZE on doneSignal", () => { expect(decide(baseHealth(), { ...baseTurn(), doneSignal: true }, baseLedger(), baseDna)).toBe("SYNTHESIZE"); });
  it("SYNTHESIZE on budgetExhausted", () => { expect(decide(baseHealth(), { ...baseTurn(), budgetExhausted: true }, baseLedger(), baseDna)).toBe("SYNTHESIZE"); });
  it("REPLAN on stall", () => {
    const h = baseHealth();
    h.signals = [{ kind: "stall", detail: "", iteration: 3 }];
    h.stuckOnSubGoalId = "sg1";
    expect(decide(h, baseTurn(), baseLedger(), baseDna)).toBe("REPLAN");
  });
  it("DEGRADE on tool_blackout with no results", () => {
    const h = baseHealth();
    h.signals = [{ kind: "tool_blackout", detail: "", iteration: 5 }];
    h.allToolsFailed = true;
    expect(decide(h, baseTurn(), baseLedger(), baseDna)).toBe("DEGRADE");
  });
  it("SYNTHESIZE on tool_blackout with partial results", () => {
    const h = baseHealth();
    h.signals = [{ kind: "tool_blackout", detail: "", iteration: 5 }];
    const ledger = baseLedger();
    ledger.subGoals = [{ id: "sg1", description: "done", status: "done", dependsOn: [], result: "data" }];
    expect(decide(h, baseTurn(), ledger, baseDna)).toBe("SYNTHESIZE");
  });
});
