import type { DegradationTier } from "./types.js";

type InternalState = "tool_executing" | "tool_failed_retrying" | "switching_approach" | "provider_switching" | "compiling_results";

const STATUS_MESSAGES: Record<InternalState, string[]> = {
  tool_executing: ["Looking into this...", "On it...", "Checking that now..."],
  tool_failed_retrying: ["Let me try another way...", "Checking a different source..."],
  switching_approach: ["Taking a fresh approach...", "Trying something different..."],
  provider_switching: ["Just a moment...", "One second..."],
  compiling_results: ["Putting this together...", "Almost there...", "Finishing up..."],
};

const STRIP_PATTERNS: RegExp[] = [
  /__STACKOWL_EXHAUSTED__/g,
  /\[CAPABILITY_GAP:[^\]]*\]/g,
  /\[SYSTEM:[^\]]*\]/g,
  /\[DONE\]/g,
  /\[DEEPER\]/gi,
  /\[LOOP GUARD\][^\n]*/g,
  /\[RISK GATE\][^\n]*/g,
];

const JARGON_MAP: [RegExp, string][] = [
  [/HTTP [45]\d{2}[^\n]*/gi, ""],
  [/\b429\b/g, ""],
  [/\bECONNREFUSED\b/gi, "could not be reached"],
  [/\bENOTFOUND\b/gi, "could not be found"],
  [/\btimeout\b/gi, "took too long to respond"],
  [/\btool (failed|error)\b/gi, "ran into a snag"],
  [/\bAPI\b/g, "the service"],
  [/\bprovider\b/gi, "assistant"],
  [/\bstack trace\b/gi, ""],
  [/\bTraceback[^)]*\)/gi, ""],
];

const DEGRADATION_TEMPLATES: Record<DegradationTier, (partial: string, gap: string | undefined, next: string | undefined) => string> = {
  1: (partial) => partial,
  2: (partial, gap) => [partial, gap ? `\n\nI wasn't able to ${gap}.` : "", "\nLet me know if you'd like me to try a different approach."].join(""),
  3: (_, gap, next) => ["I understood what you're looking for, but I need a bit more to complete this.", gap ? `\nSpecifically: ${gap}.` : "", next ? `\n\nHere's what would help: ${next}` : ""].join(""),
  4: (_, gap, next) => ["I wasn't able to complete this with what I currently have access to.", gap ? `\nThe blocker was: ${gap}.` : "", next ? `\n\nHere's what you can do instead:\n${next}` : ""].join(""),
};

/**
 * Classify a thrown LLM provider error and return a user-friendly message.
 * Returns null if the error is not a recognized quota/limit/auth error.
 *
 * @param err      The caught error
 * @param provider Display name of the active provider (e.g. "Anthropic", "OpenAI")
 */
export function classifyLlmError(err: unknown, provider = "the AI provider"): string | null {
  const raw = err instanceof Error ? err.message : String(err);
  const low = raw.toLowerCase();

  // Token / credit quota exhaustion (weekly / monthly plan limits)
  if (
    low.includes("usage_limit_exceeded") ||
    low.includes("usage limit") ||
    low.includes("monthly token") ||
    low.includes("weekly token") ||
    low.includes("token quota") ||
    low.includes("credit balance") ||
    low.includes("insufficient_quota") ||
    low.includes("out of credits")
  ) {
    return (
      `Your ${provider} token quota is exhausted for this billing period. ` +
      `Check your ${provider} account to review usage or upgrade your plan. ` +
      "Background jobs have been paused automatically."
    );
  }

  // Context window exceeded
  if (
    low.includes("context_length_exceeded") ||
    low.includes("context window") ||
    low.includes("maximum context") ||
    (low.includes("too long") && (low.includes("token") || low.includes("input")))
  ) {
    return (
      "The conversation is too long for the model to process. " +
      "Use /clear to start a fresh session — your memory and knowledge are preserved."
    );
  }

  // Rate limit (per-minute / per-day)
  if (
    low.includes("rate_limit_error") ||
    low.includes("rate limit") ||
    low.includes("too many requests") ||
    low.includes("429")
  ) {
    return (
      `Rate limit reached — ${provider} rejected the request due to too many calls in a short window. ` +
      "Wait a moment, then try again. Background tasks have been paused automatically."
    );
  }

  // Server overloaded
  if (low.includes("overloaded_error") || low.includes("overloaded")) {
    return `${provider}'s servers are currently overloaded. Please try again in a moment.`;
  }

  // Auth / API key errors
  if (
    low.includes("authentication_error") ||
    low.includes("invalid x-api-key") ||
    low.includes("invalid api key") ||
    (low.includes("401") && low.includes("http"))
  ) {
    return `The ${provider} API key is invalid or expired. Check your API key environment variable.`;
  }

  return null;
}

export class UserFacingStatusNarrator {
  postProcess(content: string, _qualityScore: number): string {
    let clean = content;
    for (const p of STRIP_PATTERNS) clean = clean.replace(p, "");
    for (const [p, r] of JARGON_MAP) clean = clean.replace(p, r);
    return clean.replace(/\n{3,}/g, "\n\n").trim();
  }

  statusMessage(state: InternalState): string {
    const opts = STATUS_MESSAGES[state];
    return opts[Math.floor(Math.random() * opts.length)];
  }

  buildDegradation(tier: DegradationTier, partialResult: string, obstacle: string | undefined, nextStep: string | undefined): string {
    return DEGRADATION_TEMPLATES[tier](partialResult, obstacle, nextStep).trim();
  }
}
