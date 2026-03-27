/**
 * StackOwl — Model Pricing Table
 *
 * Per-model token pricing for cost estimation.
 * Prices in USD per 1 million tokens.
 */

export interface ModelPrice {
  inputPer1M: number;
  outputPer1M: number;
}

/**
 * Known pricing per 1M tokens (input/output).
 * Updated: March 2026. Add new models as needed.
 */
export const MODEL_PRICING: Record<string, ModelPrice> = {
  // Anthropic
  "claude-opus-4-6": { inputPer1M: 15.0, outputPer1M: 75.0 },
  "claude-sonnet-4-6": { inputPer1M: 3.0, outputPer1M: 15.0 },
  "claude-sonnet-4-5-20241022": { inputPer1M: 3.0, outputPer1M: 15.0 },
  "claude-3-5-sonnet-latest": { inputPer1M: 3.0, outputPer1M: 15.0 },
  "claude-haiku-4-5-20251001": { inputPer1M: 0.8, outputPer1M: 4.0 },
  "claude-3-5-haiku-latest": { inputPer1M: 0.8, outputPer1M: 4.0 },

  // OpenAI
  "gpt-4o": { inputPer1M: 2.5, outputPer1M: 10.0 },
  "gpt-4o-mini": { inputPer1M: 0.15, outputPer1M: 0.6 },
  "gpt-4-turbo": { inputPer1M: 10.0, outputPer1M: 30.0 },
  o1: { inputPer1M: 15.0, outputPer1M: 60.0 },
  "o1-mini": { inputPer1M: 3.0, outputPer1M: 12.0 },
  "o3-mini": { inputPer1M: 1.1, outputPer1M: 4.4 },

  // DeepSeek
  "deepseek-chat": { inputPer1M: 0.14, outputPer1M: 0.28 },
  "deepseek-reasoner": { inputPer1M: 0.55, outputPer1M: 2.19 },

  // Groq
  "llama-3.3-70b-versatile": { inputPer1M: 0.59, outputPer1M: 0.79 },

  // Local (free)
  "llama3.2": { inputPer1M: 0, outputPer1M: 0 },
  "llama3.1": { inputPer1M: 0, outputPer1M: 0 },
  "qwen2.5": { inputPer1M: 0, outputPer1M: 0 },
  mistral: { inputPer1M: 0, outputPer1M: 0 },
};

/**
 * Estimate cost in USD for a given model and token count.
 * Returns 0 for unknown models (assumes free/local).
 */
export function estimateCost(
  model: string,
  promptTokens: number,
  completionTokens: number,
): number {
  // Try exact match first, then prefix match
  const price =
    MODEL_PRICING[model] ??
    Object.entries(MODEL_PRICING).find(([key]) => model.startsWith(key))?.[1];

  if (!price) return 0;

  return (
    (promptTokens / 1_000_000) * price.inputPer1M +
    (completionTokens / 1_000_000) * price.outputPer1M
  );
}
