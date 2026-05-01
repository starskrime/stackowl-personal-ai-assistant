import type { TaskComplexity } from "./types.js";

interface SyncInput {
  content: string;
  loopExhausted: boolean;
  toolCallCount: number;
  toolFailureCount: number;
  taskComplexity: TaskComplexity;
  hasStructuredOutput: boolean;
}

const EXHAUSTION_MARKER = "__STACKOWL_EXHAUSTED__";
const RAW_ERROR_PATTERN = /\b(Error:|HTTP [45]\d{2}|ENOTFOUND|ECONNREFUSED|timeout|stack trace|Traceback)\b/i;
const JARGON_PATTERNS: [RegExp, string][] = [
  [/HTTP [45]\d{2}[^\n]*/gi, ""],
  [/\bAPI\b/g, "the service"],
  [/\btool (failed|error)\b/gi, "ran into a snag"],
  [/\btimeout\b/gi, "took too long to respond"],
  [/\b429\b/g, ""],
  [/\bECONNREFUSED\b/gi, "could not be reached"],
  [/__STACKOWL_EXHAUSTED__/g, ""],
];

export class QualityEvaluator {
  evaluateSync(input: SyncInput): number {
    let score = 1.0;
    if (input.loopExhausted) score -= 0.30;
    if (input.content.includes(EXHAUSTION_MARKER)) score -= 0.40;
    if (RAW_ERROR_PATTERN.test(input.content)) score -= 0.30;
    const len = input.content.length;
    if (len < 50 && input.taskComplexity !== "simple") score -= 0.25;
    if (len > 2000 && input.taskComplexity === "simple") score -= 0.15;
    if (input.toolCallCount > 0 && input.toolFailureCount === 0) score += 0.10;
    if (input.hasStructuredOutput) score += 0.10;
    return Math.max(0, Math.min(1, score));
  }

  evaluateAndStrip(input: SyncInput): { score: number; cleanContent: string } {
    const score = this.evaluateSync(input);
    let clean = input.content;
    for (const [pattern, replacement] of JARGON_PATTERNS) {
      clean = clean.replace(pattern, replacement);
    }
    clean = clean.replace(/\n{3,}/g, "\n\n").trim();
    return { score, cleanContent: clean };
  }

  stripJargon(content: string): string {
    let clean = content;
    for (const [pattern, replacement] of JARGON_PATTERNS) {
      clean = clean.replace(pattern, replacement);
    }
    return clean.replace(/\n{3,}/g, "\n\n").trim();
  }
}
