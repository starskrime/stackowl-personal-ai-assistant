import { describe, it, expect, beforeEach } from "vitest";
import { HealthMonitor } from "../src/engine/health-monitor.js";
import type { TurnResult, TaskLedger } from "../src/engine/types.js";

const makeTurn = (overrides: Partial<TurnResult> = {}): TurnResult => ({
  content: "thinking...",
  toolCalls: [],
  toolResults: [],
  tokensUsed: 100,
  doneSignal: false,
  budgetExhausted: false,
  failedTools: [],
  providerUsed: "anthropic",
  modelUsed: "claude-sonnet-4-6",
  ...overrides,
});

const makeLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "medium", estimatedTurns: 5, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});

describe("HealthMonitor", () => {
  let monitor: HealthMonitor;
  beforeEach(() => { monitor = new HealthMonitor(1000); });

  it("shouldContinue returns true when healthy", () => {
    expect(monitor.shouldContinue()).toBe(true);
  });

  it("detects budget_critical signal at 85% tokens", () => {
    const turn = makeTurn({ tokensUsed: 860 });
    monitor.observe(turn, makeLedger(), 0);
    expect(monitor.getHealth().signals.some(s => s.kind === "budget_critical")).toBe(true);
  });

  it("shouldContinue returns false when budget exhausted", () => {
    const turn = makeTurn({ budgetExhausted: true });
    monitor.observe(turn, makeLedger(), 0);
    expect(monitor.shouldContinue()).toBe(false);
  });

  it("detects stall when same subgoal stuck for 3 turns", () => {
    const ledger = makeLedger();
    ledger.subGoals = [{ id: "sg1", description: "do x", status: "in_progress", dependsOn: [] }];
    const turn = makeTurn();
    monitor.observe(turn, ledger, 0);
    monitor.observe(turn, ledger, 1);
    monitor.observe(turn, ledger, 2);
    expect(monitor.getHealth().signals.some(s => s.kind === "stall")).toBe(true);
  });
});
