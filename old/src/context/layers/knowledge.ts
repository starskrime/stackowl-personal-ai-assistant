import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class KnowledgeGraphLayer implements ContextLayer {
  name = "KnowledgeGraphLayer";
  priority = 110;
  maxTokens = 300;
  produces = ["knowledge"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.userMessage.slice(0, 40) + "kg");
  }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const kg = req.deps.knowledgeGraph;
    if (!kg) return "";
    const ctx = kg.queryContext(t.userMessage);
    if (!ctx) return "";
    return `<knowledge_graph>\n${ctx}\n</knowledge_graph>`;
  }
}

