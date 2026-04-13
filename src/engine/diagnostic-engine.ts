/**
 * StackOwl — Diagnostic Engine
 *
 * Multi-hypothesis error diagnosis for the ReAct loop.
 * When a tool fails, instead of blindly retrying or picking the first alternative,
 * the engine:
 *   1. Analyzes the error in context (tool, args, result, history)
 *   2. Generates 3-5 candidate fixes with structured reasoning
 *   3. Scores each on feasibility, risk, and likelihood of success
 *   4. Returns the best fix as a directive for the model
 *
 * This replaces the simple "error → hint → retry" pattern with genuine
 * multi-hypothesis reasoning, so the assistant doesn't blindly fix and move on.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

export interface CandidateFix {
  /** Short label for the fix */
  label: string;
  /** What this fix does and why it should work */
  reasoning: string;
  /** The concrete action to take (tool call, different args, different approach) */
  action: string;
  /** 0-1: how likely this actually fixes the problem */
  likelihood: number;
  /** 0-1: how easy/safe this is to execute */
  feasibility: number;
  /** 0-1: chance of making things worse (lower is better) */
  risk: number;
  /** Composite score = likelihood * feasibility * (1 - risk) */
  score: number;
}

export interface DiagnosticResult {
  /** Root cause analysis */
  rootCause: string;
  /** Error classification */
  errorClass:
    | "retryable"
    | "non-retryable"
    | "environmental"
    | "logic"
    | "unknown";
  /** All candidate fixes, sorted by score descending */
  candidates: CandidateFix[];
  /** The recommended fix (highest score) */
  recommended: CandidateFix;
  /** Why other candidates were rejected */
  rejectionReasons: string[];
  /** Time spent diagnosing (ms) */
  diagnosisTimeMs: number;
}

export interface DiagnosticInput {
  /** The tool that failed */
  toolName: string;
  /** Arguments passed to the tool */
  toolArgs: Record<string, unknown>;
  /** The error output from the tool */
  toolResult: string;
  /** How many times this tool has failed consecutively */
  failStreak: number;
  /** Whether this is a hard failure (exception) or soft failure (non-zero exit) */
  failureType: "hard" | "soft";
  /** Error classification from classifyToolError */
  errorClass: "NON-RETRYABLE" | "TRANSIENT";
  /** Recent conversation messages for context */
  recentMessages: ChatMessage[];
  /** What the user originally asked for */
  userIntent?: string;
}

// ─── Diagnostic Engine ──────────────────────────────────────────

export class DiagnosticEngine {
  constructor(private provider: ModelProvider) {}

  /**
   * Run multi-hypothesis diagnosis on a tool failure.
   * Returns structured candidates with scores, plus a formatted
   * directive string ready for injection into the conversation.
   */
  async diagnose(input: DiagnosticInput): Promise<DiagnosticResult> {
    const startTime = Date.now();

    try {
      const prompt = this.buildDiagnosticPrompt(input);
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content:
              "You are a diagnostic reasoning engine. You analyze tool failures " +
              "and generate multiple candidate fixes ranked by quality. " +
              "Output ONLY valid JSON. Be precise and practical.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.3, maxTokens: 1500 },
      );

      const parsed = this.parseResponse(response.content);
      parsed.diagnosisTimeMs = Date.now() - startTime;

      log.engine.info(
        `[DiagnosticEngine] Diagnosed "${input.toolName}" failure in ${parsed.diagnosisTimeMs}ms — ` +
          `${parsed.candidates.length} candidates, recommended: "${parsed.recommended.label}" (score: ${parsed.recommended.score.toFixed(2)})`,
      );

      return parsed;
    } catch (err) {
      log.engine.warn(
        `[DiagnosticEngine] Diagnosis failed, falling back to heuristic: ${err instanceof Error ? `${err.message}\n${err.stack}` : String(err)}`,
      );
      return this.heuristicFallback(input, Date.now() - startTime);
    }
  }

  /**
   * Format a DiagnosticResult into a system message for injection
   * into the ReAct conversation. Shows the reasoning transparently
   * so the model understands WHY a particular fix was chosen.
   */
  formatDirective(result: DiagnosticResult, input: DiagnosticInput): string {
    const lines: string[] = [
      `[SYSTEM: DIAGNOSTIC ANALYSIS — failure #${input.failStreak} on "${input.toolName}"]`,
      "",
      `ROOT CAUSE: ${result.rootCause}`,
      `ERROR CLASS: ${result.errorClass.toUpperCase()}`,
      "",
      `CANDIDATE FIXES (${result.candidates.length} analyzed):`,
    ];

    for (let i = 0; i < result.candidates.length; i++) {
      const c = result.candidates[i];
      const marker = c === result.recommended ? "→ RECOMMENDED" : "  rejected";
      lines.push(
        `  ${i + 1}. [${c.score.toFixed(2)}] ${c.label} (${marker})`,
        `     Action: ${c.action}`,
        `     Likelihood: ${(c.likelihood * 100).toFixed(0)}% | Feasibility: ${(c.feasibility * 100).toFixed(0)}% | Risk: ${(c.risk * 100).toFixed(0)}%`,
      );
      if (c !== result.recommended && result.rejectionReasons[i]) {
        lines.push(`     Why rejected: ${result.rejectionReasons[i]}`);
      }
      lines.push(`     Reasoning: ${c.reasoning}`);
    }

    lines.push(
      "",
      `DIRECTIVE: Execute fix #${result.candidates.indexOf(result.recommended) + 1}: "${result.recommended.label}"`,
      `Action: ${result.recommended.action}`,
      "",
      `You MUST follow this diagnostic recommendation. Do NOT retry the same failed approach.`,
    );

    if (input.failStreak >= 3) {
      lines.push(
        `⚠️ CRITICAL: ${input.failStreak} consecutive failures. If this fix also fails, ` +
          `STOP and tell the user what's wrong instead of trying more fixes.`,
      );
    }

    return lines.join("\n");
  }

  // ─── Private ──────────────────────────────────────────────────

  private buildDiagnosticPrompt(input: DiagnosticInput): string {
    // Extract the last few user/assistant messages for intent context
    const contextMessages = input.recentMessages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-4)
      .map((m) => `[${m.role}]: ${m.content.slice(0, 200)}`)
      .join("\n");

    const truncatedResult =
      input.toolResult.length > 1500
        ? input.toolResult.slice(0, 1500) + "\n... [truncated]"
        : input.toolResult;

    return (
      `A tool call has failed. Analyze the failure and generate 3-5 candidate fixes.\n\n` +
      `FAILED TOOL: "${input.toolName}"\n` +
      `ARGUMENTS: ${JSON.stringify(input.toolArgs, null, 2).slice(0, 500)}\n` +
      `FAILURE TYPE: ${input.failureType === "hard" ? "exception thrown" : "non-zero exit / error output"}\n` +
      `ERROR CLASS: ${input.errorClass}\n` +
      `CONSECUTIVE FAILURES: ${input.failStreak}\n` +
      (input.userIntent ? `USER INTENT: ${input.userIntent}\n` : "") +
      `\nTOOL OUTPUT:\n${truncatedResult}\n` +
      `\nRECENT CONVERSATION:\n${contextMessages}\n` +
      `\nGenerate exactly this JSON structure:\n` +
      `{\n` +
      `  "rootCause": "one sentence: what actually went wrong",\n` +
      `  "errorClass": "retryable|non-retryable|environmental|logic|unknown",\n` +
      `  "candidates": [\n` +
      `    {\n` +
      `      "label": "short name for the fix",\n` +
      `      "reasoning": "why this fix addresses the root cause",\n` +
      `      "action": "concrete next step (which tool to call, with what args, or what approach to take)",\n` +
      `      "likelihood": 0.0-1.0,\n` +
      `      "feasibility": 0.0-1.0,\n` +
      `      "risk": 0.0-1.0\n` +
      `    }\n` +
      `  ]\n` +
      `}\n\n` +
      `RULES:\n` +
      `- Generate 3-5 candidates, not fewer\n` +
      `- "action" must be specific enough to execute (not vague like "try something else")\n` +
      `- If errorClass is NON-RETRYABLE, no candidate should retry the same tool with similar args\n` +
      `- Include at least one "give up gracefully" candidate if the error looks fundamental\n` +
      `- Scores must reflect reality: a risky hack should have high risk even if likely to work\n` +
      `- likelihood * feasibility * (1 - risk) = composite score (you don't need to calculate this)`
    );
  }

  private parseResponse(raw: string): DiagnosticResult {
    let jsonStr = raw.trim();
    if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr
        .replace(/^```json?/, "")
        .replace(/```$/, "")
        .trim();
    }
    // Strip trailing commas (but NOT JS-style comments — they'd break URLs like https://)
    jsonStr = jsonStr.replace(/,\s*([}\]])/g, "$1");

    const jsonMatch = jsonStr.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      throw new Error("Diagnostic response contained no JSON");
    }

    const parsed = JSON.parse(jsonMatch[0]) as {
      rootCause: string;
      errorClass: string;
      candidates: Array<{
        label: string;
        reasoning: string;
        action: string;
        likelihood: number;
        feasibility: number;
        risk: number;
      }>;
    };

    if (!parsed.candidates || parsed.candidates.length === 0) {
      throw new Error("Diagnostic response had no candidates");
    }

    // Calculate composite scores
    const candidates: CandidateFix[] = parsed.candidates.map((c) => ({
      label: c.label,
      reasoning: c.reasoning,
      action: c.action,
      likelihood: clamp(c.likelihood),
      feasibility: clamp(c.feasibility),
      risk: clamp(c.risk),
      score: clamp(c.likelihood) * clamp(c.feasibility) * (1 - clamp(c.risk)),
    }));

    // Sort by score descending
    candidates.sort((a, b) => b.score - a.score);

    const recommended = candidates[0];

    // Generate rejection reasons for non-recommended candidates
    const rejectionReasons = candidates.map((c) => {
      if (c === recommended) return "";
      if (c.risk > recommended.risk + 0.2)
        return `Too risky (${(c.risk * 100).toFixed(0)}% risk)`;
      if (c.likelihood < recommended.likelihood - 0.2)
        return `Low likelihood (${(c.likelihood * 100).toFixed(0)}%)`;
      if (c.feasibility < recommended.feasibility - 0.2)
        return `Hard to execute (${(c.feasibility * 100).toFixed(0)}% feasibility)`;
      return `Lower composite score (${c.score.toFixed(2)} vs ${recommended.score.toFixed(2)})`;
    });

    const validClasses = [
      "retryable",
      "non-retryable",
      "environmental",
      "logic",
      "unknown",
    ] as const;
    const errorClass = validClasses.includes(
      parsed.errorClass as (typeof validClasses)[number],
    )
      ? (parsed.errorClass as DiagnosticResult["errorClass"])
      : "unknown";

    return {
      rootCause: parsed.rootCause,
      errorClass,
      candidates,
      recommended,
      rejectionReasons,
      diagnosisTimeMs: 0,
    };
  }

  /**
   * When LLM diagnosis fails (provider down, parse error, etc.),
   * fall back to heuristic-based diagnosis using error patterns.
   */
  private heuristicFallback(
    input: DiagnosticInput,
    elapsed: number,
  ): DiagnosticResult {
    const result = input.toolResult.toLowerCase();
    const candidates: CandidateFix[] = [];

    // Heuristic 1: Permission / access errors
    if (result.includes("permission denied") || result.includes("eacces")) {
      candidates.push({
        label: "Fix permissions",
        reasoning:
          "The tool lacks filesystem permissions. Try with elevated access or a different path.",
        action: `Check file permissions for the target path, or use a path the assistant has write access to.`,
        likelihood: 0.7,
        feasibility: 0.6,
        risk: 0.2,
        score: 0.7 * 0.6 * 0.8,
      });
    }

    // Heuristic 2: File not found
    if (
      result.includes("no such file") ||
      result.includes("enoent") ||
      result.includes("not found")
    ) {
      candidates.push({
        label: "Verify path exists",
        reasoning:
          "Target file or directory doesn't exist. Search for the correct path first.",
        action: `Use a file search tool to find the correct path before retrying.`,
        likelihood: 0.8,
        feasibility: 0.9,
        risk: 0.05,
        score: 0.8 * 0.9 * 0.95,
      });
    }

    // Heuristic 3: Network / API errors
    if (
      result.includes("fetch failed") ||
      result.includes("econnrefused") ||
      result.includes("timeout")
    ) {
      candidates.push({
        label: "Network issue — try alternative",
        reasoning:
          "Network request failed. Use a different tool or approach that doesn't require network.",
        action: `Switch to an offline approach or a different network tool. If fetching a URL, try web_crawl or duckduckgo_search instead.`,
        likelihood: 0.6,
        feasibility: 0.8,
        risk: 0.1,
        score: 0.6 * 0.8 * 0.9,
      });
    }

    // Heuristic 4: Command not found / wrong tool
    if (
      result.includes("command not found") ||
      result.includes("not recognized")
    ) {
      candidates.push({
        label: "Use different tool",
        reasoning:
          "The command isn't available in this environment. Use a built-in tool instead.",
        action: `Check available tools and use a built-in alternative (e.g., web_crawl instead of curl).`,
        likelihood: 0.85,
        feasibility: 0.9,
        risk: 0.05,
        score: 0.85 * 0.9 * 0.95,
      });
    }

    // Heuristic 5: JSON parse errors
    if (
      result.includes("json") &&
      (result.includes("parse") || result.includes("syntax"))
    ) {
      candidates.push({
        label: "Fix malformed input",
        reasoning: "Tool received invalid JSON. Fix the arguments and retry.",
        action: `Review the tool arguments for JSON syntax errors, fix them, and retry with valid JSON.`,
        likelihood: 0.75,
        feasibility: 0.85,
        risk: 0.1,
        score: 0.75 * 0.85 * 0.9,
      });
    }

    // Always include a "tell user" fallback
    candidates.push({
      label: "Report to user",
      reasoning:
        "If the error is fundamental, honestly tell the user what failed and why.",
      action: `Stop retrying and explain to the user: the tool "${input.toolName}" failed because of "${input.toolResult.slice(0, 100)}". Ask if they want to try a different approach.`,
      likelihood: 1.0,
      feasibility: 1.0,
      risk: 0.0,
      score: input.failStreak >= 3 ? 0.95 : 0.3, // High score after 3 failures
    });

    // If no specific heuristics matched, add a generic "try different approach"
    if (candidates.length <= 1) {
      candidates.unshift({
        label: "Try different approach",
        reasoning:
          "The current approach failed. Rethink the strategy entirely.",
        action: `Abandon "${input.toolName}" and use a completely different tool or method to achieve the user's goal.`,
        likelihood: 0.5,
        feasibility: 0.7,
        risk: 0.15,
        score: 0.5 * 0.7 * 0.85,
      });
    }

    candidates.sort((a, b) => b.score - a.score);

    const recommended = candidates[0];
    const rejectionReasons = candidates.map((c) =>
      c === recommended ? "" : `Lower score (${c.score.toFixed(2)})`,
    );

    return {
      rootCause: `Heuristic diagnosis: tool "${input.toolName}" failed (${input.failureType})`,
      errorClass:
        input.errorClass === "NON-RETRYABLE" ? "non-retryable" : "unknown",
      candidates,
      recommended,
      rejectionReasons,
      diagnosisTimeMs: elapsed,
    };
  }
}

// ─── Helpers ──────────────────────────────────────────────────────

function clamp(n: number): number {
  return Math.max(0, Math.min(1, n));
}
