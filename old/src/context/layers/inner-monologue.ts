import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

const STALENESS_MS = 10 * 60 * 1000; // 10 minutes

export class InnerMonologueLayer implements ContextLayer {
  name = "InnerMonologueLayer";
  priority = 15;
  maxTokens = 300;
  produces = ["inner_voice"];
  dependsOn = [];

  shouldFire(_t: TriageSignals): boolean { return true; }

  getCacheKey(): string | null { return null; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const monologue = req.digest?.lastInnerMonologue;
    if (!monologue) return "";

    const age = Date.now() - new Date(monologue.storedAt).getTime();
    if (age > STALENESS_MS) return "";

    return [
      "<owl_inner_voice>",
      `My approach this turn: ${monologue.responseIntent}`,
      monologue.moodCurrent ? `My current disposition: ${monologue.moodCurrent}` : "",
      "</owl_inner_voice>",
    ].filter(Boolean).join("\n");
  }
}
