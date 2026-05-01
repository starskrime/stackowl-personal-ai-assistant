import type { ModelProvider } from "../providers/base.js";
import type { InstinctRegistry } from "./registry.js";
import type { InstinctSpec } from "./types.js";
import { log } from "../logger.js";

export class InstinctEngine {
  constructor(
    private readonly provider: ModelProvider,
    private readonly model: string,
    private readonly registry: InstinctRegistry,
  ) {}

  async evaluate(owlName: string, userMessage: string): Promise<InstinctSpec[]> {
    const candidates = this.registry.get(owlName);
    if (candidates.length === 0) return [];

    const descriptions = candidates
      .map((c, i) => `${i}: ${c.description}`)
      .join("\n");

    const prompt =
      `You are a classifier. Given a user message and a list of behavioral instincts, ` +
      `return a JSON array of the indices (numbers only) of instincts that apply.\n\n` +
      `User message: "${userMessage}"\n\n` +
      `Instincts:\n${descriptions}\n\n` +
      `Reply with ONLY a JSON array, e.g. [0,2]. Empty array [] if none apply.`;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        this.model,
      );

      const raw = response.content.trim();
      const match = raw.match(/\[[\d,\s]*\]/);
      if (!match) return [];

      const indices: number[] = JSON.parse(match[0]);
      return indices
        .filter((i) => Number.isInteger(i) && i >= 0 && i < candidates.length)
        .map((i) => candidates[i]);
    } catch (err) {
      log.engine.warn(`[InstinctEngine] Classification failed: ${err instanceof Error ? err.message : String(err)}`);
      return [];
    }
  }

  static buildConstraintBlock(instincts: InstinctSpec[]): string {
    if (instincts.length === 0) return "";
    const lines = instincts.map((i) => `- ${i.constraint}`).join("\n");
    return `\n\n[Active instincts]\n${lines}`;
  }
}

/**
 * Heuristic-first instinct evaluator.
 * Keyword scoring (0ms) → cache. Falls back to LLM only when no keyword match.
 */
export class InstinctEngineV2 {
  private cache = new Map<string, InstinctSpec[]>();

  evaluateHeuristic(instincts: InstinctSpec[], userMessage: string): InstinctSpec[] {
    const lower = userMessage.toLowerCase();
    const matched = instincts.filter(inst =>
      inst.keywords?.some(kw => lower.includes(kw.toLowerCase()))
    );
    this.cache.set(userMessage, matched);
    return matched;
  }

  getCached(userMessage: string): InstinctSpec[] | null {
    return this.cache.get(userMessage) ?? null;
  }

  clearCache(): void {
    this.cache.clear();
  }

  /** Bullet-only lines — no header block. Caller injects into TaskLedger.behavioralConstraints. */
  buildConstraintBlock(instincts: InstinctSpec[]): string {
    if (instincts.length === 0) return "";
    return instincts.map(i => `- ${i.constraint}`).join("\n");
  }
}
