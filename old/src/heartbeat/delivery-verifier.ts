import type { ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import { log } from "../logger.js";

export type DeliveryVerdict = "ADVANCES" | "NEUTRAL" | "NOISE";

export interface VerifyParams {
  jobType: string;
  messagePreview: string;
  activeGoals: string[];
  goalId?: string;
  idleSeconds?: number;
  priority?: number;
}

export interface DeliveryVerification {
  verdict: DeliveryVerdict;
  reason: string;
  suppressUntil?: Date;
}

/**
 * Filters proactive messages before delivery via a single cheap-tier LLM call.
 *
 * Provider constraint: the injected provider MUST host the model that
 * router.resolve("classification") returns. We consume only resolved.model;
 * we do NOT dispatch across providers. If the cheap-tier model lives on a
 * different provider (e.g. Haiku while main is Ollama), pass that provider
 * explicitly. A future iteration can take a Record<string, ModelProvider>.
 */
export class DeliveryVerifier {
  constructor(
    private readonly provider: ModelProvider,
    private readonly router?: IntelligenceRouter,
  ) {}

  async verify(params: VerifyParams): Promise<DeliveryVerification> {
    // Skip rule 1: job already has a verified goalId
    if (params.goalId) {
      return { verdict: "ADVANCES", reason: "goal-linked job, skipping verification" };
    }

    // Skip rule 2: morning_brief always delivers in its window
    if (params.jobType === "morning_brief") {
      return { verdict: "ADVANCES", reason: "morning_brief always delivers" };
    }

    // Skip rule 3: high-priority message during long idle period
    const idleHours = (params.idleSeconds ?? 0) / 3600;
    if (idleHours > 4 && (params.priority ?? 0) >= 70) {
      return { verdict: "ADVANCES", reason: "high-priority idle delivery, skipping verification" };
    }

    const model = this.router?.resolve("classification").model ?? undefined;
    const goalsText =
      params.activeGoals.length > 0
        ? `Active user goals:\n${params.activeGoals.map(g => `- ${g}`).join("\n")}`
        : "No active goals on record.";

    const prompt =
      `You are a proactive message quality filter for an AI assistant.\n\n` +
      `${goalsText}\n\n` +
      `The assistant wants to send this proactive message:\n"${params.messagePreview}"\n\n` +
      `Job type: ${params.jobType}\n\n` +
      `Classify this message as one of:\n` +
      `- ADVANCES: directly relevant to an active goal or clearly useful right now\n` +
      `- NEUTRAL: potentially useful but not tied to any active goal\n` +
      `- NOISE: generic, off-topic, or adds no value\n\n` +
      `Respond with JSON only:\n{"verdict":"ADVANCES|NEUTRAL|NOISE","reason":"one sentence"}`;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { maxTokens: 60 },
      );
      const raw = (response.content ?? "").trim();
      const parsed = JSON.parse(raw) as { verdict: string; reason: string };
      const verdict = ["ADVANCES", "NEUTRAL", "NOISE"].includes(parsed.verdict)
        ? (parsed.verdict as DeliveryVerdict)
        : "ADVANCES";

      const suppressUntil =
        verdict === "NEUTRAL"
          ? new Date(Date.now() + 2 * 60 * 60 * 1000)
          : undefined;

      log.engine.debug(`[DeliveryVerifier] ${params.jobType} → ${verdict}: ${parsed.reason}`);
      return { verdict, reason: parsed.reason ?? "", suppressUntil };
    } catch {
      return { verdict: "ADVANCES", reason: "verification error — defaulting to deliver" };
    }
  }
}
