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
