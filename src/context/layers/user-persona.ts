import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { UserPersonaSynthesizer } from "../user-persona-synthesizer.js";
import { hash } from "../utils.js";

export class UserPersonaLayer implements ContextLayer {
  name = "UserPersonaLayer";
  priority = 50;
  maxTokens = 400;
  produces = ["user_persona"];
  dependsOn = [];

  constructor(private synthesizer: UserPersonaSynthesizer) {}

  shouldFire(t: TriageSignals): boolean { return !!t.effectiveUserId; }

  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "persona");
  }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const facts = (req.session as any).userFacts ?? [];
    const episodes = (req.session as any).userEpisodes ?? [];
    const prefs = (req.session as any).preferenceContext ?? "";

    const persona = await this.synthesizer.getPersona(t.effectiveUserId, facts, episodes, prefs);
    if (!persona) return "";

    return [
      "<user_persona>",
      `Communication: ${persona.communicationStyle}, ${persona.expertiseLevel}`,
      persona.currentProjects.length ? `Current focus: ${persona.currentProjects.join(", ")}` : "",
      persona.recurringPatterns.length ? `Patterns: ${persona.recurringPatterns.join(", ")}` : "",
      `Approach: ${persona.preferredApproach}`,
      persona.emotionalTrajectory.length ? `Emotional arc: ${persona.emotionalTrajectory.slice(-2).join(" → ")}` : "",
      "</user_persona>",
    ].filter(Boolean).join("\n");
  }
}
