import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export interface SpecialistSummary {
  name: string;
  role: string;
  expertise: string[];
}

export type ClassifyFn = (
  message: string,
  specialists: SpecialistSummary[],
) => Promise<string | null>;

export function buildClassifyFn(
  provider: ModelProvider,
  model: string,
): ClassifyFn {
  return async (message: string, specialists: SpecialistSummary[]): Promise<string | null> => {
    const lines = specialists
      .map((s) => `- ${s.name} (${s.role}): ${s.expertise.join(", ") || s.role}`)
      .join("\n");

    const prompt = `You are a routing assistant. Given a list of specialists and a user message, decide which specialist should handle the message.

Specialists:
${lines}

User message: "${message}"

Reply with ONLY the specialist name that should handle this message, or "none" if no specialist is appropriate. Do not explain.`;

    try {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { maxTokens: 30 },
      );
      const raw = response.content.trim();
      const match = specialists.find(
        (s) => s.name.toLowerCase() === raw.toLowerCase(),
      );
      return match ? match.name : null;
    } catch (err) {
      log.engine.warn(
        `[LLMClassifier] classify failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  };
}
