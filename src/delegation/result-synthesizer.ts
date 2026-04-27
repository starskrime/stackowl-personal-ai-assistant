/**
 * StackOwl — Result Synthesizer
 *
 * Synthesizes results from multiple sub-owls into a coherent response.
 * Uses LLM-based synthesis with fallback to concatenation.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { SubOwlResult } from "./sub-owl-runner.js";
import { log } from "../logger.js";

export interface SynthesisOptions {
  includeFailedResults: boolean;
  maxResultLength: number;
}

export class ResultSynthesizer {
  constructor(private provider: ModelProvider) {}

  async synthesize(
    originalTask: string,
    results: SubOwlResult[],
    options: Partial<SynthesisOptions> = {},
  ): Promise<string> {
    const opts: SynthesisOptions = {
      includeFailedResults: false,
      maxResultLength: 2000,
      ...options,
    };

    const filteredResults = opts.includeFailedResults
      ? results
      : results.filter((r) => r.success);

    if (filteredResults.length === 0) {
      return "No successful results to synthesize.";
    }

    const resultBlock = filteredResults
      .map((r) => {
        const truncated = r.output.slice(0, opts.maxResultLength);
        return `**Subtask ${r.taskId}** (${r.success ? "✓" : "✗"}): ${r.description}\n${truncated}`;
      })
      .join("\n\n---\n\n");

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          "You are a synthesis AI. Combine multiple subtask results into a single coherent response. " +
          "Be direct. Do not repeat structure - just deliver the final answer.",
      },
      {
        role: "user",
        content:
          `Original task: "${originalTask}"\n\n` +
          `Subtask results:\n\n${resultBlock}\n\n` +
          `Synthesize into a final answer.`,
      },
    ];

    try {
      const response = await this.provider.chat(messages);
      return response.content.trim();
    } catch (err) {
      log.engine.warn(`[ResultSynthesizer] LLM synthesis failed: ${err}`);
      return this.fallbackSynthesize(filteredResults);
    }
  }

  private fallbackSynthesize(results: SubOwlResult[]): string {
    const successfulResults = results.filter((r) => r.success);
    if (successfulResults.length === 0) {
      return "No successful results to synthesize.";
    }
    return successfulResults.map((r) => r.output).join("\n\n");
  }
}
