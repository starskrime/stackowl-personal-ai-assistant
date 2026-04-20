/**
 * StackOwl — Triage Classifier
 *
 * Routes each incoming message to the right execution path:
 *   DIRECT     — Simple Q&A, no tools needed. Fast-path LLM reply.
 *   AGENTIC    — Requires tool use / multi-step ReAct loop.
 *   DELEGATE   — Complex, decomposable task → SubOwlRunner.
 *   PARLIAMENT — Contested topic / tradeoff → parallel owl debate.
 *
 * Two-tier decision:
 *   1. Fast-path rules (< 1ms, no LLM) — cover 80% of cases.
 *   2. LLM triage fallback — for ambiguous messages, fires a single
 *      cheap classify call and caches the result for the session.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export type TriageDecision = "DIRECT" | "AGENTIC" | "DELEGATE" | "PARLIAMENT";

export interface TriageResult {
  decision: TriageDecision;
  confidence: number;   // 0–1
  reason: string;
  /** Filled only for PARLIAMENT — the topic to debate */
  parliamentTopic?: string;
  /** Filled only for DELEGATE — high-level task description */
  delegateTask?: string;
}

// ─── Rule tables ──────────────────────────────────────────────────

/** Messages matching any of these → DIRECT immediately */
const DIRECT_PATTERNS: RegExp[] = [
  /^(hi|hello|hey|yo|sup|howdy)[!?.]*$/i,
  /^(thanks?|thank you|cheers|thx)[!.]*$/i,
  /^(bye|goodbye|see you|later)[!.]*$/i,
  /^what(?:'s| is) (?:your )?name\??$/i,
  /^(?:who|what) are you\??$/i,
  /^how are you\??$/i,
  /^\d+[\s+\-*/]\d+/,   // arithmetic
  /^tell me a (?:joke|fact)/i,
];

/** Messages matching any of these → PARLIAMENT */
const PARLIAMENT_PATTERNS: RegExp[] = [
  /\bshould (?:i|we|you)\b/i,
  /\bwhich is better\b/i,
  /\bpros? and cons?\b/i,
  /\btradeoffs?\b/i,
  /\btrade-offs?\b/i,
  /\b(?:compare|versus|vs\.?)\b/i,
  /\brecommend\b.*\bor\b/i,
  /\balternative(?:s)? to\b/i,
  /\bbest (?:way|approach|option|practice)\b/i,
];

/** Messages matching any of these → DELEGATE */
const DELEGATE_PATTERNS: RegExp[] = [
  /\b(?:build|create|implement|write|develop|make)\b.{20,}/i,
  /\b(?:refactor|redesign|rewrite|migrate)\b/i,
  /\b(?:step[- ]by[- ]step|multiple steps?|several steps?)\b/i,
  /\b(?:plan|roadmap|strategy)\b.{10,}/i,
  /\b(?:full|complete|entire|whole)\b.{5,}(?:app|system|service|module|feature)/i,
  /(?:\n|,\s*then\s|\s+and then\s).{10,}/i, // multi-step phrasing
];

/** Messages matching any of these → AGENTIC */
const AGENTIC_PATTERNS: RegExp[] = [
  /\b(?:search|find|look up|fetch|retrieve|get)\b/i,
  /\b(?:run|execute|shell|command|script)\b/i,
  /\b(?:file|folder|directory|path)\b/i,
  /\b(?:web|url|http|website)\b/i,
  /\b(?:screenshot|capture)\b/i,
  /\b(?:remember|recall|memory|pellet)\b/i,
  /\b(?:install|update|upgrade|package)\b/i,
];

// ─── TriageClassifier ─────────────────────────────────────────────

export class TriageClassifier {
  private readonly llmTimeoutMs = 5_000;

  constructor(private provider?: ModelProvider) {}

  /**
   * Classify a user message. Returns immediately from fast-path rules
   * when possible; falls back to LLM for ambiguous messages.
   */
  async classify(userMessage: string): Promise<TriageResult> {
    const msg = userMessage.trim();

    // ── Fast-path rules ──────────────────────────────────────────
    const fastResult = this.fastPath(msg);
    if (fastResult) {
      log.engine.debug(`[Triage] Fast-path → ${fastResult.decision} (${fastResult.reason})`);
      return fastResult;
    }

    // ── LLM fallback ─────────────────────────────────────────────
    if (this.provider) {
      try {
        return await this.llmClassify(msg);
      } catch (err) {
        log.engine.warn(`[Triage] LLM classify failed, defaulting AGENTIC: ${err instanceof Error ? err.message : err}`);
      }
    }

    // Default: agentic (safe for most things)
    return { decision: "AGENTIC", confidence: 0.5, reason: "default fallback" };
  }

  // ─── Fast-path ───────────────────────────────────────────────

  private fastPath(msg: string): TriageResult | null {
    // Very short messages are almost always direct
    const words = msg.split(/\s+/).length;
    if (words <= 4 && !AGENTIC_PATTERNS.some((r) => r.test(msg))) {
      return { decision: "DIRECT", confidence: 0.85, reason: "short message" };
    }

    for (const re of DIRECT_PATTERNS) {
      if (re.test(msg)) {
        return { decision: "DIRECT", confidence: 0.95, reason: `matched direct pattern: ${re.source.slice(0, 40)}` };
      }
    }

    for (const re of PARLIAMENT_PATTERNS) {
      if (re.test(msg)) {
        return {
          decision: "PARLIAMENT",
          confidence: 0.85,
          reason: `matched parliament pattern: ${re.source.slice(0, 40)}`,
          parliamentTopic: msg,
        };
      }
    }

    for (const re of DELEGATE_PATTERNS) {
      if (re.test(msg)) {
        return {
          decision: "DELEGATE",
          confidence: 0.8,
          reason: `matched delegate pattern: ${re.source.slice(0, 40)}`,
          delegateTask: msg,
        };
      }
    }

    for (const re of AGENTIC_PATTERNS) {
      if (re.test(msg)) {
        return { decision: "AGENTIC", confidence: 0.8, reason: `matched agentic pattern: ${re.source.slice(0, 40)}` };
      }
    }

    return null; // ambiguous — fall through to LLM
  }

  // ─── LLM classify ────────────────────────────────────────────

  private async llmClassify(msg: string): Promise<TriageResult> {
    const messages: ChatMessage[] = [
      {
        role: "system",
        content: `You are a routing classifier. Classify the user message into one of:
- DIRECT: simple question/conversation, no tools needed
- AGENTIC: needs tool use (search, files, web, shell, memory)
- DELEGATE: large multi-step task that should be broken into subtasks
- PARLIAMENT: contested topic with tradeoffs that benefits from multi-perspective debate

Respond ONLY with valid JSON: {"decision":"...","confidence":0.9,"reason":"..."}`,
      },
      {
        role: "user",
        content: `Classify: "${msg.slice(0, 500)}"`,
      },
    ];

    const result = await Promise.race([
      this.provider!.chat(messages),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("triage timeout")), this.llmTimeoutMs),
      ),
    ]);

    const raw = result.content.trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/\s*```$/, "");

    const parsed = JSON.parse(raw) as {
      decision: TriageDecision;
      confidence: number;
      reason: string;
    };

    const decision = ["DIRECT", "AGENTIC", "DELEGATE", "PARLIAMENT"].includes(parsed.decision)
      ? parsed.decision
      : "AGENTIC";

    return {
      decision,
      confidence: typeof parsed.confidence === "number" ? Math.min(1, Math.max(0, parsed.confidence)) : 0.7,
      reason: parsed.reason ?? "llm classified",
      parliamentTopic: decision === "PARLIAMENT" ? msg : undefined,
      delegateTask: decision === "DELEGATE" ? msg : undefined,
    };
  }
}
