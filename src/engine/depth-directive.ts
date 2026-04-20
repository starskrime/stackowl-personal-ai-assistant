/**
 * StackOwl — Depth Directive
 *
 * Computes HOW DEEP the owl should go in its response BEFORE the LLM call,
 * so the model calibrates its response correctly from the start rather than
 * getting trimmed after the fact.
 *
 * Three levels:
 *   minimal   — Quick answer. 1-3 sentences. User sent < 15 words or asked
 *               a simple factual question. Fast path.
 *   standard  — Balanced. 1-3 paragraphs. Default for most interactions.
 *   thorough  — Full treatment. User sent a long detailed message, asked
 *               for explanation/analysis, or triage classified as DELEGATE.
 *
 * Decision factors (in priority order):
 *   1. Explicit user preference (conciseness from PreferenceModel) — highest weight
 *   2. Triage decision (DELEGATE/PARLIAMENT → thorough, DIRECT → minimal)
 *   3. Message word count (< 10 → minimal, > 60 → thorough)
 *
 * Output is a `<depth>` XML tag injected into the system prompt.
 * The LLM treats this as a formatting directive.
 */

import type { TriageDecision } from "../triage/index.js";

// ─── Types ────────────────────────────────────────────────────────

export type Depth = "minimal" | "standard" | "thorough";

export interface DepthDirectiveResult {
  depth: Depth;
  reason: string;
  systemPromptBlock: string;
}

// ─── Depth prompts ────────────────────────────────────────────────

const DEPTH_PROMPTS: Record<Depth, string> = {
  minimal:
    "Be brief. 1-3 sentences only. Get straight to the answer — no preamble, " +
    "no bullet points unless strictly necessary, no summary at the end.",

  standard:
    "Answer clearly and directly. 1-3 paragraphs. Include key context but " +
    "avoid padding. Use structure (bullets, headers) only when it genuinely helps.",

  thorough:
    "Give a complete, thorough answer. Cover all relevant aspects. " +
    "Use structure (headers, bullets, code blocks) to make it scannable. " +
    "Include examples, tradeoffs, and nuance. This is a question that deserves depth.",
};

// ─── computeDepthDirective ────────────────────────────────────────

export function computeDepthDirective(
  messageWordCount: number,
  triage?: TriageDecision,
  verbosityPref?: string,
): DepthDirectiveResult {

  // ── Factor 1: Explicit user preference ───────────────────────
  if (verbosityPref) {
    const v = verbosityPref.toLowerCase();
    if (v === "concise" || v === "brief" || v === "short" || v === "minimal") {
      return build("minimal", "user preference: concise");
    }
    if (v === "verbose" || v === "detailed" || v === "thorough") {
      return build("thorough", "user preference: verbose");
    }
  }

  // ── Factor 2: Triage decision ─────────────────────────────────
  if (triage === "DELEGATE" || triage === "PARLIAMENT") {
    return build("thorough", `triage: ${triage}`);
  }
  if (triage === "DIRECT") {
    return build("minimal", "triage: DIRECT");
  }

  // ── Factor 3: Message length ──────────────────────────────────
  if (messageWordCount < 10) {
    return build("minimal", `short message (${messageWordCount} words)`);
  }
  if (messageWordCount > 60) {
    return build("thorough", `long message (${messageWordCount} words)`);
  }

  return build("standard", "default");
}

function build(depth: Depth, reason: string): DepthDirectiveResult {
  return {
    depth,
    reason,
    systemPromptBlock:
      `\n<depth>${depth}</depth>\n` +
      `Response length guidance: ${DEPTH_PROMPTS[depth]}\n`,
  };
}
