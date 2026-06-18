import { createHash } from "node:crypto";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

const EVALUATOR_TIMEOUT_MS = 15_000;

export interface QualityVerdict {
  satisfied: boolean;
  confidence: number;   // 0..1
  reason: string;
  suggestedAlternative?: string;
}

export class ToolResultEvaluator {
  // Cache key: toolName + argsHash + resultHash (first 200 chars of result)
  private cache = new Map<string, QualityVerdict>();

  constructor(private provider: ModelProvider) {}

  private cacheKey(toolName: string, args: unknown, result: string): string {
    const argsHash = createHash("sha1").update(JSON.stringify(args ?? {})).digest("hex").slice(0, 8);
    const resultHash = createHash("sha1").update(result.slice(0, 200)).digest("hex").slice(0, 8);
    return `${toolName}:${argsHash}:${resultHash}`;
  }

  async evaluate(
    toolName: string,
    args: unknown,
    result: string,
    userIntent: string,
  ): Promise<QualityVerdict> {
    const key = this.cacheKey(toolName, args, result);
    const cached = this.cache.get(key);
    if (cached) {
      log.engine.debug("tool.evaluator.cache_hit", { tool: toolName });
      return cached;
    }

    log.engine.debug("tool.evaluator.entry", { tool: toolName, intentLen: userIntent.length, resultLen: result.length, timeoutMs: EVALUATOR_TIMEOUT_MS });

    const prompt = `You are a quality-gate evaluator. A tool was called to help answer a user request.

User intent: "${userIntent.slice(0, 300)}"

Tool called: ${toolName}
Tool arguments: ${JSON.stringify(args ?? {}).slice(0, 200)}
Tool result (first 500 chars): "${result.slice(0, 500)}"

Answer ONLY with a JSON object (no markdown):
{
  "satisfied": true or false,
  "confidence": 0.0 to 1.0,
  "reason": "one sentence explanation",
  "suggestedAlternative": "tool_name or null"
}

"satisfied" = true if the result contains information that plausibly helps answer the user intent.
"satisfied" = false if the result is empty, blocked, returns errors, or clearly doesn't contain what was asked for.`;

    try {
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("tool.evaluator timeout")), EVALUATOR_TIMEOUT_MS)
      );

      const response = await Promise.race([
        this.provider.chat(
          [{ role: "user", content: prompt }],
          undefined,
          { maxTokens: 150, temperature: 0 },
        ),
        timeoutPromise,
      ]);

      const text = response.content ?? "";
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) throw new Error(`No JSON in evaluator response: ${text.slice(0, 100)}`);

      const parsed = JSON.parse(jsonMatch[0]) as QualityVerdict;
      const verdict: QualityVerdict = {
        satisfied: Boolean(parsed.satisfied),
        confidence: Number(parsed.confidence ?? 0.5),
        reason: String(parsed.reason ?? ""),
        suggestedAlternative: parsed.suggestedAlternative && parsed.suggestedAlternative !== "null"
          ? String(parsed.suggestedAlternative)
          : undefined,
      };

      this.cache.set(key, verdict);
      log.engine.info("tool.evaluator.verdict", {
        tool: toolName,
        satisfied: verdict.satisfied,
        confidence: verdict.confidence,
        reason: verdict.reason,
        suggestedAlternative: verdict.suggestedAlternative,
      });
      return verdict;
    } catch (err) {
      log.engine.warn("tool.evaluator.failed", err, { tool: toolName });
      // On error, default to satisfied=true so we don't inject false QUALITY GATEs
      return { satisfied: true, confidence: 0, reason: "evaluator failed — defaulting to satisfied" };
    }
  }

  /** Clear cache (for testing) */
  _clearCache(): void {
    this.cache.clear();
  }
}
