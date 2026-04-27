/**
 * StackOwl — Tool Selector
 *
 * Selects tools based on learned effectiveness from ApproachLibrary,
 * not just recency. Provides weighted tool selection that improves
 * over time based on past outcomes.
 */

import type { ToolDefinition } from "../providers/base.js";
import type { ApproachPattern } from "../learning/approach-library.js";
import { log } from "../logger.js";

export interface ToolSelectionContext {
  taskType: string;
  availableTools: ToolDefinition[];
  owlName: string;
}

export interface ToolSelectionResult {
  selectedTool: ToolDefinition;
  effectivenessScore: number;
  alternatives: Array<{ tool: ToolDefinition; score: number }>;
}

export class ToolSelector {
  constructor(
    private getEffectivenessScore: (
      owlName: string,
      toolName: string,
      taskType: string,
    ) => number,
    private getPatterns: (
      owlName: string,
      toolName: string,
      taskType: string,
    ) => ApproachPattern | undefined,
  ) {}

  selectTool(context: ToolSelectionContext): ToolSelectionResult {
    const { taskType, availableTools, owlName } = context;

    const scored = availableTools.map((tool) => {
      const score = this.getEffectivenessScore(owlName, tool.name, taskType);
      const pattern = this.getPatterns(owlName, tool.name, taskType);

      let finalScore = score;

      if (pattern && (pattern.successCount + pattern.failureCount) > 0) {
        const recencyBonus = this.calculateRecencyBonus(pattern);
        finalScore = Math.min(0.95, score + recencyBonus);
      }

      return { tool, score: finalScore };
    });

    scored.sort((a, b) => b.score - a.score);

    const selectedTool = scored[0].tool;
    const alternatives = scored.slice(1).map((s) => ({
      tool: s.tool,
      score: s.score,
    }));

    log.engine.debug(
      `[ToolSelector] Selected ${selectedTool.name} (score=${scored[0].score.toFixed(3)}) for ${taskType}`,
    );

    return {
      selectedTool,
      effectivenessScore: scored[0].score,
      alternatives,
    };
  }

  private calculateRecencyBonus(pattern: ApproachPattern): number {
    if (!pattern.lastSuccessAt && !pattern.lastFailureAt) return 0;

    const lastTime = pattern.lastSuccessAt
      ? new Date(pattern.lastSuccessAt)
      : pattern.lastFailureAt
        ? new Date(pattern.lastFailureAt)
        : new Date();

    const daysElapsed =
      (Date.now() - lastTime.getTime()) / (1000 * 60 * 60 * 24);

    if (daysElapsed < 1) return 0.1;
    if (daysElapsed < 7) return 0.05;
    return 0;
  }
}
