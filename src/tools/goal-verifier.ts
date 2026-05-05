import type { SubGoal } from "../engine/types.js";
import type { ChatMessage, ChatOptions, ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";

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
// Accepts both: a real IntelligenceRouter wrapped with Map<string, ModelProvider>
// (via GoalVerifier.create()) or a test mock that returns {chat} directly.

interface ClassificationProvider {
  chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<{ content: string }>;
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
- NEUTRAL: tool succeeded but result is unrelated to the sub-goal

If the tool reports success:false with error.code: BLOCKED_BY_ANTI_BOT or ALL_TIERS_UNAVAILABLE, classify as BLOCKED.
If error.code: TIMEOUT, classify as PARTIAL.
If success:true, classify based on whether data answers the goal.`;

// ─── GoalVerifier ─────────────────────────────────────────────────

export class GoalVerifier {
  /**
   * Create a GoalVerifier wired to the real IntelligenceRouter + provider map.
   *
   * Calls router.resolve("classification") at verify-time to pick the cheapest
   * tier, then delegates .chat() to the resolved provider with the resolved model.
   * The constructor's ClassificationRouter interface is satisfied by an adapter
   * closure — the constructor and existing tests remain unchanged.
   *
   * Uses Map<string, ModelProvider> (simpler than ProviderRegistry — no throws on
   * missing providers; fail-open is explicit via the null-check below).
   */
  static create(
    router: IntelligenceRouter,
    providers: Map<string, ModelProvider>,
  ): GoalVerifier {
    const adapted: ClassificationRouter = {
      resolve(_taskType: string): ClassificationProvider {
        const resolved = router.resolve("classification");
        const provider = providers.get(resolved.provider);
        if (!provider) {
          return {
            chat: async () => ({
              content: '{"verdict":"NEUTRAL","reason":"provider not found"}',
            }),
          };
        }
        return {
          chat: (messages, _model, options) =>
            provider.chat(messages, resolved.model, options),
        };
      },
    };
    return new GoalVerifier(adapted);
  }

  constructor(private readonly router: ClassificationRouter) {}

  async verify(args: VerifyArgs): Promise<VerificationResult> {
    const { toolName, toolArgs, toolResult, subGoal, userMessage } = args;

    let userContent: string;
    try {
      const env = JSON.parse(toolResult);
      if (env && typeof env === "object" && "success" in env) {
        if (env.success === false && env.error) {
          const tierSummary = Array.isArray(env.error.attemptedTiers)
            ? env.error.attemptedTiers.map((t: { name: string; outcome: string }) => `${t.name}:${t.outcome}`).join(", ")
            : "(none)";
          userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool error.code: ${env.error.code}
Tool error.message: ${env.error.message}
Tiers attempted: [${tierSummary}]`;
        } else if (env.success === true && env.data) {
          userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool result data (first 500 chars): ${JSON.stringify(env.data).slice(0, 500)}`;
        } else {
          throw new Error("not envelope");
        }
      } else {
        throw new Error("not envelope");
      }
    } catch {
      userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool result (first 500 chars): ${toolResult.slice(0, 500)}`;
    }

    try {
      const provider = await this.router.resolve("classification");
      const response = await provider.chat(
        [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userContent },
        ],
        undefined, // model — resolved separately via create()
        { temperature: 0 },
      );

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
