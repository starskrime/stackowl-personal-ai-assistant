import type {
  ContextLayer,
  ContextRequest,
  TriageSignals,
  LayerResults,
} from "../layer.js";

export class CollabContextLayer implements ContextLayer {
  name = "CollabContextLayer";
  priority = 140;
  maxTokens = 300;
  produces = ["collab"];
  dependsOn = [];
  getCacheKey(): string | null {
    return null;
  }
  shouldFire(_t: TriageSignals): boolean {
    return true;
  }

  async build(
    req: ContextRequest,
    _t: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    const collab = (req.session as any).collabContext as string | undefined;
    if (!collab) return "";
    return `<collab_context>\n${collab}\n</collab_context>`;
  }
}
