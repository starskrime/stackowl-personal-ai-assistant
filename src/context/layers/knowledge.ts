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

export class RelevantPelletsLayer implements ContextLayer {
  name = "RelevantPelletsLayer";
  priority = 115;
  maxTokens = 500;
  produces = ["pellets"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const pelletStore = req.deps.pelletStore;
    if (!pelletStore) return "";
    try {
      const pellets = await pelletStore.search(t.userMessage);
      if (!pellets.length) return "";
      const lines = ["<relevant_pellets>"];
      for (const p of pellets.slice(0, 3)) {
        lines.push(`  <pellet title="${p.title}">${p.content.slice(0, 300)}</pellet>`);
      }
      lines.push("</relevant_pellets>");
      return lines.join("\n");
    } catch {
      return "";
    }
  }
}
