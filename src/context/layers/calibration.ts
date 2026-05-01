import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class DepthDirectiveLayer implements ContextLayer {
  name = "DepthDirectiveLayer";
  priority = 150;
  maxTokens = 150;
  produces = ["depth"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const depth = (req.session as any).depthDirective as string | undefined;
    if (!depth) return "";
    return `<depth_directive>${depth}</depth_directive>`;
  }
}

export class OpinionInjectionLayer implements ContextLayer {
  name = "OpinionInjectionLayer";
  priority = 155;
  maxTokens = 200;
  produces = ["opinion"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.isOpinionRequest; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const opinion = (req.session as any).owlOpinionContext as string | undefined;
    if (!opinion) return "";
    return `<owl_opinion_context>${opinion}</owl_opinion_context>`;
  }
}

export class UserMentalModelLayer implements ContextLayer {
  name = "UserMentalModelLayer";
  priority = 160;
  maxTokens = 200;
  produces = ["mental_model"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasFrustration; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const model = (req.session as any).userMentalModel as string | undefined;
    if (!model) return "";
    return `<user_mental_model>${model}</user_mental_model>`;
  }
}

export class EchoChamberGuardLayer implements ContextLayer {
  name = "EchoChamberGuardLayer";
  priority = 165;
  maxTokens = 150;
  produces = ["echo_guard"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.isOpinionRequest; }

  async build(_req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    return "<echo_chamber_guard>When asked for opinions, offer a balanced perspective and gently challenge assumptions when appropriate. Avoid pure validation loops.</echo_chamber_guard>";
  }
}

export class GroundStateLayer implements ContextLayer {
  name = "GroundStateLayer";
  priority = 170;
  maxTokens = 500;
  produces = ["ground_state"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.sessionDepth >= 5; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const groundState = (req.session as any).groundStateContext as string | undefined;
    if (!groundState) return "";
    return `<ground_state>\n${groundState}\n</ground_state>`;
  }
}
