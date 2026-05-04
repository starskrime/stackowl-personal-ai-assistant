/**
 * StackOwl — Element 7 T12 — ToolPriorLayer
 *
 * Surfaces tools that historically worked on semantically similar past
 * requests as a soft nudge in the planning context. Sources its data from
 * PersonalizedRouter (T11) — cold-start safe (returns "" when fewer than
 * 50 successful trajectories exist in the window).
 *
 * Skipped for conversational turns where tool selection is irrelevant.
 * Capped at 5 suggestions to keep the nudge short and avoid flooding the
 * planner.
 */
import type {
  ContextLayer,
  ContextRequest,
  TriageSignals,
  LayerResults,
} from "../layer.js";
import type { PersonalizedRouter } from "../../tools/cortex/personalized-router.js";

const MAX_SUGGESTIONS = 5;

export class ToolPriorLayer implements ContextLayer {
  readonly name = "ToolPriorLayer";
  readonly priority = 8;
  readonly maxTokens = 80;
  readonly produces = ["tool_prior"];
  readonly dependsOn = [];

  constructor(private readonly router: PersonalizedRouter) {}

  shouldFire(triage: TriageSignals): boolean {
    return !triage.isConversational;
  }

  getCacheKey(): string | null {
    // Tool-prior is per-user-message — caching adds little and risks
    // showing yesterday's suggestions. Recompute each turn.
    return null;
  }

  async build(
    _req: ContextRequest,
    triage: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    if (!triage.userMessage) return "";
    const tools = await this.router.suggestTools(triage.userMessage, {
      topK: 3,
    });
    if (tools.length === 0) return "";
    const capped = tools.slice(0, MAX_SUGGESTIONS);
    return `Tools that worked well on similar past requests: ${capped.join(", ")}.`;
  }
}
