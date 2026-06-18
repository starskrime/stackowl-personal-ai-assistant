import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class UserBehaviorProfileLayer implements ContextLayer {
  name = "UserBehaviorProfileLayer";
  priority = 120;
  maxTokens = 300;
  produces = ["user_profile"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "profile");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const profile = (req.session as any).userBehaviorProfile as string | undefined;
    if (!profile) return "";
    return `<user_behavior_profile>\n${profile}\n</user_behavior_profile>`;
  }
}

export class InferredPreferencesLayer implements ContextLayer {
  name = "InferredPreferencesLayer";
  priority = 125;
  maxTokens = 300;
  produces = ["preferences"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const prefs = (req.session as any).inferredPreferences as string[] | undefined;
    if (!prefs?.length) return "";
    return `<inferred_preferences>\n${prefs.map((p) => `  - ${p}`).join("\n")}\n</inferred_preferences>`;
  }
}

export class PredictedNeedsLayer implements ContextLayer {
  name = "PredictedNeedsLayer";
  priority = 130;
  maxTokens = 300;
  produces = ["predictions"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const predicted = (req.session as any).predictedNeeds as Array<{ need: string; confidence: number }> | undefined;
    if (!predicted?.length) return "";
    const high = predicted.filter((p) => p.confidence >= 0.7);
    if (!high.length) return "";
    return `<predicted_needs>\n${high.map((p) => `  - ${p.need}`).join("\n")}\n</predicted_needs>`;
  }
}
