import type { RunHealth, TurnResult, TaskLedger, Decision } from "./types.js";

interface DnaThresholds {
  riskTolerance: "cautious" | "balanced" | "aggressive";
  challengeLevel: "low" | "medium" | "high";
}

export function decide(
  health: RunHealth,
  turn: TurnResult,
  ledger: TaskLedger,
  dna: DnaThresholds,
): Decision {
  const maxReplans = dna.challengeLevel === "high" ? 3
    : dna.challengeLevel === "low" ? 1
    : 2;

  if (turn.doneSignal) return "SYNTHESIZE";
  if (turn.budgetExhausted) return "SYNTHESIZE";

  const hasStall = health.signals.some(s => s.kind === "stall");
  const hasBudgetCritical = health.signals.some(s => s.kind === "budget_critical");
  const hasToolBlackout = health.signals.some(s => s.kind === "tool_blackout");

  if (hasStall) {
    if (ledger.revisions.length < maxReplans) return "REPLAN";
    return _synthesizeOrDegrade(ledger);
  }
  if (hasToolBlackout) return _synthesizeOrDegrade(ledger);
  if (hasBudgetCritical) {
    if (dna.riskTolerance === "cautious") return "HITL";
    return "SYNTHESIZE";
  }
  if (turn.pendingCapabilityGap) return "HITL";
  return "CONTINUE";
}

function _synthesizeOrDegrade(ledger: TaskLedger): Decision {
  return ledger.subGoals.some(sg => sg.status === "done" && sg.result)
    ? "SYNTHESIZE" : "DEGRADE";
}
