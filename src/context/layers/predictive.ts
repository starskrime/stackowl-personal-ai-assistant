import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class PredictiveContextLayer implements ContextLayer {
  name = "PredictiveContextLayer";
  priority = 90;
  maxTokens = 200;
  produces = ["predicted_tasks"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(): string | null { return null; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const queue = req.deps.predictiveQueue;
    if (!queue) return "";
    const ready = queue.getReadyTasks()
      .sort((a: { confidence: number }, b: { confidence: number }) => b.confidence - a.confidence)
      .slice(0, 3);
    if (ready.length === 0) return "";
    const lines = ["<predicted_next>"];
    for (const t of ready) {
      lines.push(`  <task confidence="${t.confidence}">${t.action}</task>`);
    }
    lines.push("</predicted_next>");
    return lines.join("\n");
  }
}
