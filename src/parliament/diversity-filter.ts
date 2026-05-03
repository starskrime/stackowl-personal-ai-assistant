import type { OwlPosition } from "./protocol.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export class DiversityFilter {
  constructor(
    private readonly router: IntelligenceRouter,
    private readonly providers: Map<string, ModelProvider>,
  ) {}

  async selectDivergingPair(
    positions: OwlPosition[],
  ): Promise<[OwlPosition, OwlPosition]> {
    const fallback: [OwlPosition, OwlPosition] = [
      positions[0],
      positions[positions.length - 1],
    ];

    if (positions.length <= 2) return fallback;

    try {
      const resolved = this.router.resolve("classification");
      const provider = this.providers.get(resolved.provider);
      if (!provider) return fallback;

      const positionList = positions
        .map((p, i) => `${i}: [${p.owlName}] ${p.argument.slice(0, 200)}`)
        .join("\n");

      const prompt =
        `Given these ${positions.length} positions on a debate topic, identify the two that most ` +
        `fundamentally disagree with each other.\n\n` +
        `Positions:\n${positionList}\n\n` +
        `Reply with ONLY valid JSON: {"indices": [<first_index>, <second_index>]}`;

      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        resolved.model,
        { temperature: 0, maxTokens: 50 },
      );

      const match = response.content.match(/\{[\s\S]*?\}/);
      if (!match) return fallback;

      const parsed = JSON.parse(match[0]) as { indices?: unknown };
      const indices = parsed.indices;

      if (
        !Array.isArray(indices) ||
        indices.length < 2 ||
        typeof indices[0] !== "number" ||
        typeof indices[1] !== "number" ||
        indices[0] < 0 || indices[0] >= positions.length ||
        indices[1] < 0 || indices[1] >= positions.length ||
        indices[0] === indices[1]
      ) {
        return fallback;
      }

      return [positions[indices[0]], positions[indices[1]]];
    } catch (err) {
      log.parliament.debug(
        `[DiversityFilter] Error selecting diverging pair: ${err instanceof Error ? err.message : String(err)} — using fallback`,
      );
      return fallback;
    }
  }
}
