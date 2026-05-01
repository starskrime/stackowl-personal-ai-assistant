import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { UnifiedMemoryRetriever } from "../unified-memory-retriever.js";

export class UnifiedMemoryRetrievalLayer implements ContextLayer {
  name = "UnifiedMemoryRetrievalLayer";
  priority = 100;
  maxTokens = 800;
  produces = ["memory"];
  dependsOn = ["user_persona"];
  getCacheKey(): string | null { return null; }

  constructor(private retriever: UnifiedMemoryRetriever) {}

  shouldFire(t: TriageSignals): boolean {
    return !t.isConversational || t.isReturningUser || t.hasTemporalTrigger;
  }

  async build(_req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    return this.retriever.retrieve(t.userMessage, t.effectiveUserId);
  }
}
