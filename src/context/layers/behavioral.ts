import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";
import type { MemoryDatabase } from "../../memory/db.js";

export class BehavioralPatchLayer implements ContextLayer {
  name = "BehavioralPatchLayer";
  priority = 80;
  maxTokens = 500;
  produces = ["behavioral_rules"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "behavioral_v1");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const patches = (req.session as any).behavioralPatches as string[] | undefined;
    if (!patches?.length) return "";
    return `<behavioral_rules>\n${patches.map((p) => `  - ${p}`).join("\n")}\n</behavioral_rules>`;
  }
}

export class ActiveIntentsLayer implements ContextLayer {
  name = "ActiveIntentsLayer";
  priority = 90;
  maxTokens = 300;
  produces = ["intents"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasActiveItems; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const intents = (req.session as any).activeIntents as string[] | undefined;
    if (!intents?.length) return "";
    return `<active_intents>\n${intents.map((i) => `  - ${i}`).join("\n")}\n</active_intents>`;
  }
}

export class OwlLearningsLayer implements ContextLayer {
  name = "OwlLearningsLayer";
  priority = 95;
  maxTokens = 400;
  produces = ["learnings"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational || t.isReturningUser; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "learnings_v2");
  }

  constructor(private readonly db?: MemoryDatabase) {}

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    let learnings: string[] | undefined;

    if (this.db) {
      const owlName =
        req.session.metadata.activeOwlName ?? req.session.metadata.owlName;
      learnings = this.db.owlLearnings.getForOwlSorted(owlName);
    } else {
      learnings = (req.session as any).owlLearnings as string[] | undefined;
    }

    if (!learnings?.length) return "";
    return `<owl_learnings>\n${learnings.slice(0, 6).map((l) => `  - ${l}`).join("\n")}\n</owl_learnings>`;
  }
}
