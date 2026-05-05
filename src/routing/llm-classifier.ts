import type { ModelProvider } from "../providers/base.js";

export interface SpecialistSummary {
  name: string;
  role: string;
  expertise: string[];
}

/**
 * Build a classify function that uses an LLM to route a user message
 * to the most appropriate specialist owl by name.
 *
 * Returns the matched specialist's name, or null if none match or an
 * error occurs.
 */
export function buildClassifyFn(
  provider: ModelProvider,
  model: string,
): (message: string, specialists: SpecialistSummary[]) => Promise<string | null> {
  return async (message: string, specialists: SpecialistSummary[]): Promise<string | null> => {
    if (specialists.length === 0) return null;

    const list = specialists
      .map((s) => `- ${s.name} (${s.role}): ${s.expertise.join(", ")}`)
      .join("\n");

    const prompt =
      `You are a routing assistant. Given the user message below, decide which specialist should handle it.\n\n` +
      `Specialists:\n${list}\n\n` +
      `User message: "${message}"\n\n` +
      `Reply with ONLY the specialist's exact name from the list above, or "none" if no specialist fits.`;

    try {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { maxTokens: 30 },
      );

      const raw = (response.content ?? "").trim();

      // Case-insensitive match against known specialist names
      const match = specialists.find(
        (s) => s.name.toLowerCase() === raw.toLowerCase(),
      );
      if (match) return match.name;

      return null;
    } catch {
      return null;
    }
  };
}
