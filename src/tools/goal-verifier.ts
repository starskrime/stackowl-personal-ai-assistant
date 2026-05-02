import type { SubGoal } from "../engine/types.js";
import type { ChatMessage } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ProviderRegistry } from "../providers/registry.js";

// ─── Public Types ─────────────────────────────────────────────────

export type VerificationVerdict = "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";

export interface VerificationResult {
  verdict: VerificationVerdict;
  reason: string;
  suggestion?: string;
}

export interface VerifyArgs {
  toolName: string;
  toolArgs: Record<string, unknown>;
  toolResult: string;
  subGoal: SubGoal;
  userMessage: string;
}

// ─── Duck-typed router interface ──────────────────────────────────
// Accepts both: a real IntelligenceRouter wrapped with ProviderRegistry
// (via GoalVerifier.create()) or a test mock that returns {chat} directly.

interface ClassificationProvider {
  chat(messages: ChatMessage[]): Promise<{ content: string }>;
}

interface ClassificationRouter {
  resolve(taskType: string): ClassificationProvider | Promise<ClassificationProvider>;
}

// ─── Prompt ───────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are a tool execution verifier. Given a tool's result and the active sub-goal, classify whether the result advances the goal.

Respond with JSON only:
{"verdict": "ADVANCES"|"PARTIAL"|"BLOCKED"|"NEUTRAL", "reason": "one sentence", "suggestion": "optional, only for BLOCKED"}

- ADVANCES: result clearly provides information that moves toward the sub-goal
- PARTIAL: result provides some relevant information but is incomplete
- BLOCKED: tool failed, hit a paywall, returned irrelevant content, or actively cannot help
- NEUTRAL: tool succeeded but result is unrelated to the sub-goal`;

// ─── GoalVerifier ─────────────────────────────────────────────────

export class GoalVerifier {
  /**
   * Create a GoalVerifier wired to the real IntelligenceRouter + ProviderRegistry.
   *
   * Calls router.resolve("classification") at verify-time to pick the cheapest
   * tier, then delegates .chat() to the resolved provider with the resolved model.
   * The constructor's ClassificationRouter interface is satisfied by an adapter
   * closure — the constructor and existing tests remain unchanged.
   */
  static create(
    router: IntelligenceRouter,
    registry: ProviderRegistry,
  ): GoalVerifier {
    const classificationRouter: ClassificationRouter = {
      resolve(_taskType: string) {
        const resolved = router.resolve("classification");
        const provider = registry.get(resolved.provider);
        return {
          chat(messages: ChatMessage[]) {
            return provider.chat(messages, resolved.model);
          },
        };
      },
    };
    return new GoalVerifier(classificationRouter);
  }

  constructor(private readonly router: ClassificationRouter) {}

  async verify(args: VerifyArgs): Promise<VerificationResult> {
    const { toolName, toolArgs, toolResult, subGoal, userMessage } = args;

    const userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool result (first 500 chars): ${toolResult.slice(0, 500)}`;

    try {
      const provider = await this.router.resolve("classification");
      const response = await provider.chat([
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userContent },
      ]);

      return this.parseResponse(response.content);
    } catch {
      return { verdict: "NEUTRAL", reason: "Verifier unavailable, defaulting to NEUTRAL" };
    }
  }

  private parseResponse(content: string): VerificationResult {
    try {
      const match = content.match(/\{[\s\S]*\}/);
      if (!match) return { verdict: "NEUTRAL", reason: "Unparseable verifier response" };

      const parsed = JSON.parse(match[0]) as {
        verdict?: string;
        reason?: string;
        suggestion?: string;
      };

      const validVerdicts: VerificationVerdict[] = ["ADVANCES", "PARTIAL", "BLOCKED", "NEUTRAL"];
      const verdict = validVerdicts.includes(parsed.verdict as VerificationVerdict)
        ? (parsed.verdict as VerificationVerdict)
        : "NEUTRAL";

      return {
        verdict,
        reason: parsed.reason ?? "No reason provided",
        suggestion: parsed.suggestion,
      };
    } catch {
      return { verdict: "NEUTRAL", reason: "Failed to parse verifier response" };
    }
  }
}
