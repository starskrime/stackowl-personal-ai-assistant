import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class WorkingMemoryDigestLayer implements ContextLayer {
  name = "WorkingMemoryDigestLayer";
  priority = 20;
  maxTokens = 600;
  produces = ["digest"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }

  shouldFire(t: TriageSignals): boolean { return t.sessionDepth > 0; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    if (!req.digest) return "";
    const d = req.digest;
    const lines = ["<conversation_digest>"];
    if (d.task) lines.push(`  <current_task>${d.task}</current_task>`);
    if (d.artifacts?.length) {
      lines.push("  <artifacts_from_last_response>");
      for (const a of d.artifacts.slice(0, 6)) {
        lines.push(`    <artifact type="${a.type}">${a.value}</artifact>`);
      }
      lines.push("  </artifacts_from_last_response>");
    }
    if (d.decisions?.length) {
      lines.push("  <decisions_made>");
      for (const dec of d.decisions) lines.push(`    <decision>${dec}</decision>`);
      lines.push("  </decisions_made>");
    }
    if (d.failed?.length) {
      lines.push("  <already_tried>");
      for (const f of d.failed) lines.push(`    <attempt>${f}</attempt>`);
      lines.push("  </already_tried>");
    }
    lines.push("</conversation_digest>");
    return lines.join("\n");
  }
}

export class ContinuityPriorResponseLayer implements ContextLayer {
  name = "ContinuityPriorResponseLayer";
  priority = 25;
  maxTokens = 2000;
  produces = ["continuity"];
  dependsOn = ["digest"];
  getCacheKey(): string | null { return null; }

  shouldFire(t: TriageSignals): boolean {
    return t.continuityClass === "CONTINUATION" || t.continuityClass === "FOLLOW_UP";
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const lastResponse = req.digest?.lastAssistantResponse;
    if (!lastResponse) return "";
    return `<prior_response>\n${lastResponse.slice(0, 1800)}\n</prior_response>`;
  }
}

export class CompressionSummaryLayer implements ContextLayer {
  name = "CompressionSummaryLayer";
  priority = 30;
  maxTokens = 800;
  produces = ["compression"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }

  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const summary = (req.session as any).compressionSummary as string | undefined;
    if (!summary) return "";
    return `<session_summary>\n${summary}\n</session_summary>`;
  }
}
