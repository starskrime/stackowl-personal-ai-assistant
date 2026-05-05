import type {
  ContextLayer,
  ContextRequest,
  TriageSignals,
  LayerResults,
} from "../layer.js";
import type { SignalPool } from "../../signals/pool.js";

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

export class AmbientContextLayer implements ContextLayer {
  name = "AmbientContextLayer";
  priority = 145;
  maxTokens = 400;
  produces = ["ambient"];
  dependsOn = [];
  constructor(private readonly signalPool: SignalPool) {}
  getCacheKey(): string | null {
    return null;
  }
  shouldFire(t: TriageSignals): boolean {
    return !t.isConversational && this.signalPool.hasHighPrioritySignals();
  }

  async build(
    _req: ContextRequest,
    _t: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    return this.signalPool.toContextBlock(8);
  }
}
