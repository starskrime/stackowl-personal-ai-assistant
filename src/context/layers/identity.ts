import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class SynthesisIdentityLayer implements ContextLayer {
  name = "SynthesisIdentityLayer";
  priority = 10;
  maxTokens = 500;
  produces = ["identity"];
  dependsOn = [];
  alwaysInclude = true;

  shouldFire(_t: TriageSignals): boolean { return true; }

  getCacheKey(req: ContextRequest, _t: TriageSignals): string | null {
    const owlName = (req.session as any).owlName ?? "default";
    return hash(owlName + "v1");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const session = req.session as any;
    const owlPersonality = session.owlPersonality ?? "";
    if (!owlPersonality) return "";
    return `<owl_identity>\n${owlPersonality}\n</owl_identity>`;
  }
}
