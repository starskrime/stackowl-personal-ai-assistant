import type { TurnResult, TaskLedger, RunHealth, HealthSignal, HealthSignalKind } from "./types.js";

export class HealthMonitor {
  private health: RunHealth;
  private readonly tokenBudget: number;
  private consecutiveSameSubGoal = 0;
  private lastActiveSubGoalId: string | null = null;
  private budgetExhaustedSeen = false;

  constructor(tokenBudget: number) {
    this.tokenBudget = tokenBudget;
    this.health = {
      iteration: 0,
      tokensConsumed: 0,
      tokenBudget,
      consecutiveFailures: 0,
      uniqueToolsAttempted: new Set(),
      allToolsFailed: false,
      spinningDetected: false,
      providerSwitchCount: 0,
      stuckOnSubGoalId: null,
      signals: [],
    };
  }

  observe(turn: TurnResult, ledger: TaskLedger, iteration: number): void {
    this.health.iteration = iteration;
    this.health.tokensConsumed += turn.tokensUsed;

    for (const tc of turn.toolCalls) {
      this.health.uniqueToolsAttempted.add(tc.name);
    }

    if (turn.failedTools.length > 0 && turn.toolCalls.length > 0) {
      const allFailed = turn.failedTools.length === turn.toolCalls.length;
      if (allFailed) this.health.consecutiveFailures++;
      else this.health.consecutiveFailures = 0;
    } else if (turn.toolCalls.length > 0) {
      this.health.consecutiveFailures = 0;
    }

    const activeSubGoal = ledger.subGoals.find(sg => sg.status === "in_progress");
    if (activeSubGoal) {
      if (activeSubGoal.id === this.lastActiveSubGoalId) {
        this.consecutiveSameSubGoal++;
      } else {
        this.consecutiveSameSubGoal = 1;
        this.lastActiveSubGoalId = activeSubGoal.id;
      }
      if (this.consecutiveSameSubGoal >= 3) {
        this.health.stuckOnSubGoalId = activeSubGoal.id;
      }
    }

    if (this.health.uniqueToolsAttempted.size > 0) {
      const failedNames = new Set(turn.failedTools.map(f => f.name));
      this.health.allToolsFailed = [...this.health.uniqueToolsAttempted].every(
        n => failedNames.has(n)
      );
    }

    if (turn.budgetExhausted) this.budgetExhaustedSeen = true;

    this.health.signals = [];
    this._checkSignals(turn);
  }

  shouldContinue(): boolean {
    if (this.budgetExhaustedSeen) return false;
    if (this.health.tokensConsumed >= this.tokenBudget) return false;
    if (this.health.allToolsFailed && this.health.consecutiveFailures >= 3) return false;
    return true;
  }

  getHealth(): RunHealth {
    return { ...this.health, uniqueToolsAttempted: new Set(this.health.uniqueToolsAttempted) };
  }

  private _checkSignals(turn: TurnResult): void {
    const pct = this.health.tokensConsumed / this.tokenBudget;
    if (pct >= 0.85) this._emit("budget_critical", `${Math.round(pct * 100)}% budget consumed`);
    if (this.health.stuckOnSubGoalId) this._emit("stall", `SubGoal ${this.health.stuckOnSubGoalId} stuck for ${this.consecutiveSameSubGoal} turns`);
    if (this.health.allToolsFailed && this.health.uniqueToolsAttempted.size > 1) this._emit("tool_blackout", `All ${this.health.uniqueToolsAttempted.size} tools failed`);
    if (turn.budgetExhausted) this._emit("budget_critical", "Engine reported budget exhausted");
    if (this.health.providerSwitchCount > 1) this._emit("provider_unstable", `${this.health.providerSwitchCount} provider switches`);
  }

  private _emit(kind: HealthSignalKind, detail: string): void {
    this.health.signals.push({ kind, detail, iteration: this.health.iteration });
  }
}
