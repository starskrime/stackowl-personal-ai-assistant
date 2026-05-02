/**
 * StackOwl — Challenge Directive
 *
 * Builds a behavioral directive for the system prompt based on the owl's
 * `challengeLevel` DNA trait. High challenge = assertive advisor.
 * Low challenge = supportive. The directive is injected as a ContextLayer
 * so every response is shaped by the owl's current challenge posture.
 *
 * Evolution signal: the OwlEvolutionEngine reads `challenge_instances` from
 * `outcome_journal` and adjusts `evolvedTraits.challengeLevel` over time.
 */

import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../context/layer.js";
import type { OwlRegistry } from "../owls/registry.js";

// ─── Directive text ───────────────────────────────────────────────

export const CHALLENGE_DIRECTIVES = {
  low:    "Be supportive and encouraging in your responses.",
  medium: "Be honest, including when you disagree. State disagreement diplomatically with clear reasoning.",
  high:   "Challenge the user's assumptions when you have good reason to. Be direct and assertive — act as a trusted advisor, not a yes-man.",
} as const;

// ─── Numeric API (used by tests + context layer) ──────────────────

export function buildChallengeDirective(challengeLevel: number): string {
  if (challengeLevel <= 3) return CHALLENGE_DIRECTIVES.low;
  if (challengeLevel <= 6) return CHALLENGE_DIRECTIVES.medium;
  return CHALLENGE_DIRECTIVES.high;
}

// ─── ChallengeLevel string → numeric mapping ──────────────────────

const CHALLENGE_LEVEL_MAP: Record<string, number> = {
  low:       2,
  medium:    5,
  high:      8,
  relentless: 10,
};

export function challengeLevelToNumber(level: string): number {
  return CHALLENGE_LEVEL_MAP[level] ?? 6;
}

// ─── ContextLayer ─────────────────────────────────────────────────

/**
 * Injects a `<challenge_directive>` block into the system prompt based on
 * the active owl's `evolvedTraits.challengeLevel`. Reads the owl name from
 * `req.session.metadata.owlName`, looks up the OwlRegistry, and falls back
 * to "medium" (5) when the registry or owl is unavailable.
 */
export class ChallengeDirectiveLayer implements ContextLayer {
  name     = "ChallengeDirectiveLayer";
  priority = 85;        // after BehavioralPatchLayer (80), before OwlLearningsLayer (95)
  maxTokens = 60;
  produces  = ["challenge_style"];
  dependsOn: string[] = [];

  constructor(private owlRegistry?: OwlRegistry) {}

  shouldFire(_t: TriageSignals): boolean { return true; }

  getCacheKey(): string | null { return null; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    let numericLevel = 5; // default: medium

    if (this.owlRegistry) {
      const owlName: string | undefined =
        (req.session as any).metadata?.owlName ??
        (req.session as any).owlName;
      if (owlName) {
        const owl = this.owlRegistry.get(owlName);
        if (owl) {
          const rawLevel = owl.dna.evolvedTraits.challengeLevel;
          numericLevel = challengeLevelToNumber(String(rawLevel));
        }
      }
    }

    const directive = buildChallengeDirective(numericLevel);
    return `<challenge_directive>${directive}</challenge_directive>`;
  }
}
