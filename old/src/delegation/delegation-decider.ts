/**
 * StackOwl — Delegation Decider
 *
 * Decides when delegation is more effective than direct execution,
 * based on task complexity assessment.
 */

import { log } from "../logger.js";

export type ExecutionMode = "direct" | "delegated";

export interface DelegationDecision {
  mode: ExecutionMode;
  reasoning: string;
  estimatedParallelTasks?: number;
  complexityScore: number;
}

export interface ComplexityIndicators {
  hasMultipleSteps: boolean;
  hasDependencyChains: boolean;
  requiresDifferentDomains: boolean;
  estimatedSubtasks: number;
  hasUncertainty: boolean;
}

export class DelegationDecider {
  private readonly COMPLEXITY_THRESHOLD = 0.6;
  private readonly SUBTASK_THRESHOLD = 3;

  assessComplexity(
    task: string,
    indicators: Partial<ComplexityIndicators> = {},
  ): number {
    let score = 0;

    if (
      indicators.estimatedSubtasks &&
      indicators.estimatedSubtasks >= this.SUBTASK_THRESHOLD
    ) {
      score += 0.3;
    }
    if (indicators.hasDependencyChains) {
      score += 0.2;
    }
    if (indicators.requiresDifferentDomains) {
      score += 0.2;
    }
    if (indicators.hasUncertainty) {
      score += 0.15;
    }
    if (task.length > 500) {
      score += 0.15;
    }

    return Math.min(1, score);
  }

  decide(
    task: string,
    indicators: Partial<ComplexityIndicators> = {},
  ): DelegationDecision {
    const complexityScore = this.assessComplexity(task, indicators);

    const shouldDelegate =
      complexityScore >= this.COMPLEXITY_THRESHOLD ||
      (indicators.estimatedSubtasks ?? 0) >= this.SUBTASK_THRESHOLD;

    const decision: DelegationDecision = {
      mode: shouldDelegate ? "delegated" : "direct",
      reasoning: shouldDelegate
        ? `High complexity (${complexityScore.toFixed(2)}) suggests delegation`
        : `Low complexity (${complexityScore.toFixed(2)}) - direct execution preferred`,
      complexityScore,
    };

    if (shouldDelegate) {
      decision.estimatedParallelTasks = Math.min(
        indicators.estimatedSubtasks ?? 2,
        5,
      );
    }

    log.engine.debug(
      `[DelegationDecider] Task complexity=${complexityScore.toFixed(2)} → ${decision.mode}`,
    );

    return decision;
  }
}
