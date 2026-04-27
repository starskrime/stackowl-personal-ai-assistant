import type { BatchState, TrendAnalysis } from './types.js';

export interface EvolutionTrendAnalyzerConfig {
  includePatterns?: boolean;
  includeErrorPatterns?: boolean;
  maxPatterns?: number;
}

const DEFAULT_CONFIG: EvolutionTrendAnalyzerConfig = {
  includePatterns: true,
  includeErrorPatterns: true,
  maxPatterns: 10,
};

export class EvolutionTrendAnalyzer {
  private config: EvolutionTrendAnalyzerConfig;

  constructor(config: Partial<EvolutionTrendAnalyzerConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  analyze(batchState: BatchState): TrendAnalysis {
    const patterns: string[] = [];
    const errorPatterns: string[] = [];
    const recommendations: string[] = [];

    const toolUsageMap = new Map<string, number>();
    const taskTypeUsageMap = new Map<string, number>();
    const errorByTool = new Map<string, number>();
    const errorByTaskType = new Map<string, number>();

    for (const record of batchState.records) {
      toolUsageMap.set(record.toolName, (toolUsageMap.get(record.toolName) ?? 0) + 1);

      if (record.taskType) {
        taskTypeUsageMap.set(record.taskType, (taskTypeUsageMap.get(record.taskType) ?? 0) + 1);
      }

      if (record.status === 'failure') {
        errorByTool.set(record.toolName, (errorByTool.get(record.toolName) ?? 0) + 1);
        if (record.taskType) {
          errorByTaskType.set(record.taskType, (errorByTaskType.get(record.taskType) ?? 0) + 1);
        }
        if (record.errorMessage) {
          errorPatterns.push(`Error with ${record.toolName}: ${record.errorMessage}`);
        }
      }
    }

    if (this.config.includePatterns) {
      for (const [tool, count] of toolUsageMap.entries()) {
        if (count >= 3) {
          patterns.push(`Frequent tool usage: ${tool} (${count} times)`);
        }
      }

      for (const [taskType, count] of taskTypeUsageMap.entries()) {
        if (count >= 3) {
          patterns.push(`Frequent task type: ${taskType} (${count} times)`);
        }
      }
    }

    if (this.config.includeErrorPatterns) {
      for (const [tool, errorCount] of errorByTool.entries()) {
        const totalUsage = toolUsageMap.get(tool) ?? 1;
        const errorRate = errorCount / totalUsage;
        if (errorRate > 0.3) {
          recommendations.push(`High error rate for ${tool}: ${(errorRate * 100).toFixed(1)}% - consider fallback`);
        }
      }

      for (const [taskType, errorCount] of errorByTaskType.entries()) {
        const totalUsage = taskTypeUsageMap.get(taskType) ?? 1;
        const errorRate = errorCount / totalUsage;
        if (errorRate > 0.3) {
          recommendations.push(`High error rate for task type ${taskType}: ${(errorRate * 100).toFixed(1)}%`);
        }
      }
    }

    if (batchState.errorCount > batchState.successCount) {
      recommendations.push('Error rate exceeds success rate - DNA adaptation recommended');
    }

    const truncatedPatterns = patterns.slice(0, this.config.maxPatterns ?? 10);
    const truncatedErrorPatterns = errorPatterns.slice(0, this.config.maxPatterns ?? 10);

    return {
      patterns: truncatedPatterns,
      errorPatterns: truncatedErrorPatterns,
      recommendations,
    };
  }

  toGuardPrompt(batchState: BatchState): string {
    const analysis = this.analyze(batchState);

    const lines: string[] = [
      '# Evolution Analysis Report',
      '',
      '## Batch Summary',
      `- Total Records: ${batchState.records.length}`,
      `- Errors: ${batchState.errorCount}`,
      `- Successes: ${batchState.successCount}`,
      `- Evolution Trigger Count: ${batchState.counter}`,
      '',
    ];

    if (analysis.patterns.length > 0) {
      lines.push('## Behavioral Patterns');
      for (const pattern of analysis.patterns) {
        lines.push(`- ${pattern}`);
      }
      lines.push('');
    }

    if (analysis.errorPatterns.length > 0) {
      lines.push('## Error Patterns');
      for (const errorPattern of analysis.errorPatterns) {
        lines.push(`- ${errorPattern}`);
      }
      lines.push('');
    }

    if (analysis.recommendations.length > 0) {
      lines.push('## Mutation Recommendations');
      for (const recommendation of analysis.recommendations) {
        lines.push(`- ${recommendation}`);
      }
      lines.push('');
    }

    lines.push('## Current DNA Trait State');
    lines.push('Please review the above analysis and suggest DNA trait mutations:');
    lines.push('');
    lines.push('Consider adjusting:');
    lines.push('- humor: If interactions feel too robotic or too casual');
    lines.push('- formality: If communication style needs tuning');
    lines.push('- proactivity: If assistant is too passive or too aggressive');
    lines.push('- riskTolerance: If assistant takes too many or too few risks');
    lines.push('- teachingStyle: If explanations are too simple or too complex');
    lines.push('- delegationPreference: If assistant should delegate more or less');

    return lines.join('\n');
  }
}

export const evolutionTrendAnalyzer = new EvolutionTrendAnalyzer();
