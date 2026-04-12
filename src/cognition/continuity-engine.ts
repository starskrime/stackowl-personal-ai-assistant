/**
 * StackOwl — Conversation Continuity Engine
 *
 * 3-layer classification of each incoming message:
 *   Layer 1: Temporal gap analysis (instant, no LLM)
 *   Layer 2: Linguistic marker scan (instant, no LLM)
 *   Layer 3: Semantic coherence (1 fast LLM call, only when ambiguous)
 *
 * Output drives context strategy and intent lifecycle.
 */

import type { Session } from "../memory/store.js";
import type { TemporalSnapshot } from "./temporal-context.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export type ContinuityClass =
  | "CONTINUATION"
  | "FOLLOW_UP"
  | "TOPIC_SWITCH"
  | "FRESH_START";

export interface ContinuityResult {
  classification: ContinuityClass;
  confidence: number;
  reason: string;
  layerUsed: 1 | 2 | 3;
  priorTopicSummary?: string;
}

// ─── Layer 1: Temporal Signal ────────────────────────────────────

interface TemporalSignal {
  bias: ContinuityClass;
  confidence: number;
}

function temporalLayer(
  snapshot: TemporalSnapshot,
  hasMessages: boolean,
): TemporalSignal {
  const gapStr = snapshot.lastMessageGap;
  if (!gapStr) {
    // No measurable gap — either first message or very recent (< 30s)
    if (hasMessages) {
      // Messages exist but gap is tiny (< 30s, not shown) → strong continuation
      return { bias: "CONTINUATION", confidence: 0.8 };
    }
    // First message in session
    if (snapshot.isReturningUser) {
      return { bias: "FRESH_START", confidence: 0.7 };
    }
    return { bias: "FRESH_START", confidence: 0.9 };
  }

  // Parse gap from formatted string back to approximate ms
  const gapMs = parseGapMs(gapStr);

  if (gapMs < 5 * 60 * 1000) {
    // < 5 min
    return { bias: "CONTINUATION", confidence: 0.7 };
  }
  if (gapMs < 30 * 60 * 1000) {
    // 5-30 min
    return { bias: "CONTINUATION", confidence: 0.4 };
  }
  if (gapMs < 4 * 60 * 60 * 1000) {
    // 30min-4h
    return { bias: "TOPIC_SWITCH", confidence: 0.5 };
  }
  if (gapMs < 72 * 60 * 60 * 1000) {
    // 4h-72h — same/next day return; let Layer 3 decide, don't assume fresh start
    return { bias: "TOPIC_SWITCH", confidence: 0.5 };
  }
  // > 72h — genuinely stale
  return { bias: "FRESH_START", confidence: 0.8 };
}

function parseGapMs(gapStr: string): number {
  const seconds = gapStr.match(/(\d+)\s*second/);
  if (seconds) return parseInt(seconds[1]) * 1000;

  const minutes = gapStr.match(/(\d+)\s*minute/);
  const hours = gapStr.match(/(\d+)\s*hour/);
  const days = gapStr.match(/(\d+)\s*day/);

  let ms = 0;
  if (days) ms += parseInt(days[1]) * 24 * 60 * 60 * 1000;
  if (hours) ms += parseInt(hours[1]) * 60 * 60 * 1000;
  if (minutes) ms += parseInt(minutes[1]) * 60 * 1000;
  return ms || 60 * 1000; // Default to 1 min if unparseable
}

// ─── Layer 2: Linguistic Markers ─────────────────────────────────

interface LinguisticSignal {
  bias: ContinuityClass | null;
  markers: string[];
}

const CONTINUATION_PATTERNS: Array<{ pattern: RegExp; label: string }> = [
  // Anaphora — references to prior context
  { pattern: /^(?:it|that|this|those|the thing)\b/i, label: "anaphora" },
  {
    pattern: /\b(?:what (?:we|you) (?:discussed|said|mentioned|talked about))\b/i,
    label: "reference",
  },
  // Sequence markers
  {
    pattern: /^(?:also|and|next|another|plus|additionally|furthermore)\b/i,
    label: "sequence",
  },
  // Explicit continuation
  {
    pattern:
      /\b(?:continue|continuing|where were we|as I was saying|back to|going back|regarding that)\b/i,
    label: "explicit-continue",
  },
  // Topic reference
  {
    pattern: /\b(?:about that|regarding|as for|on that note|speaking of)\b/i,
    label: "topic-ref",
  },
  // Pronoun-heavy starts suggesting continuation
  {
    pattern: /^(?:so|ok so|right so|anyway)\b/i,
    label: "continuation-starter",
  },
];

const BREAK_PATTERNS: Array<{ pattern: RegExp; label: string }> = [
  // Greetings (standalone, not mid-sentence)
  {
    pattern: /^(?:hi|hello|hey|good morning|good afternoon|good evening|yo|sup)[\s,.!]?$/i,
    label: "greeting",
  },
  // Explicit topic change
  {
    pattern:
      /\b(?:new topic|different question|unrelated|something else|forget that|start over|fresh start|new task|change of subject)\b/i,
    label: "explicit-break",
  },
  // "By the way" — softer break
  { pattern: /^(?:btw|by the way)\b/i, label: "btw" },
];

function linguisticLayer(message: string): LinguisticSignal {
  const contMatches: string[] = [];
  const breakMatches: string[] = [];

  for (const { pattern, label } of CONTINUATION_PATTERNS) {
    if (pattern.test(message)) contMatches.push(label);
  }
  for (const { pattern, label } of BREAK_PATTERNS) {
    if (pattern.test(message)) breakMatches.push(label);
  }

  if (contMatches.length > 0 && breakMatches.length === 0) {
    return { bias: "CONTINUATION", markers: contMatches };
  }
  if (breakMatches.length > 0 && contMatches.length === 0) {
    // Greetings after a gap = FRESH_START; "btw" = TOPIC_SWITCH
    const isHardBreak = breakMatches.some(
      (m) => m === "greeting" || m === "explicit-break",
    );
    return {
      bias: isHardBreak ? "FRESH_START" : "TOPIC_SWITCH",
      markers: breakMatches,
    };
  }

  return { bias: null, markers: [...contMatches, ...breakMatches] };
}

// ─── Layer 3: Semantic Coherence (LLM) ──────────────────────────

async function semanticLayer(
  message: string,
  recentMessages: Array<{ role: string; content: string }>,
  provider: ModelProvider,
): Promise<ContinuityClass> {
  const last3 = recentMessages
    .filter((m) => m.role === "user" || m.role === "assistant")
    .slice(-6)
    .map((m) => `${m.role}: ${m.content.slice(0, 150)}`)
    .join("\n");

  const prompt = `Given these recent messages and a new message, classify the relationship.

Recent conversation:
${last3}

New message: ${message.slice(0, 200)}

Classify as exactly one letter:
A) CONTINUATION — same topic/intent as recent messages
B) FOLLOW_UP — related but new angle on same topic
C) TOPIC_SWITCH — entirely new topic, keep history for reference
D) FRESH_START — new topic, history is irrelevant

Return ONLY one letter (A, B, C, or D):`;

  try {
    const result = await Promise.race([
      provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 5 },
      ),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("timeout")), 2000),
      ),
    ]);

    const letter = result.content.trim().charAt(0).toUpperCase();
    const map: Record<string, ContinuityClass> = {
      A: "CONTINUATION",
      B: "FOLLOW_UP",
      C: "TOPIC_SWITCH",
      D: "FRESH_START",
    };
    return map[letter] ?? "FOLLOW_UP";
  } catch (err) {
    log.engine.warn(
      `[ContinuityEngine] Layer 3 failed: ${err instanceof Error ? err.message : err}`,
    );
    return "FOLLOW_UP"; // Safe default — preserves context without forcing continuity
  }
}

// ─── Combined Engine ─────────────────────────────────────────────

/**
 * Classify an incoming message's relationship to the conversation.
 *
 * Layers 1+2 run instantly (no LLM). Layer 3 only triggers when
 * the first two layers disagree or have low confidence (~20% of messages).
 */
export async function classifyContinuity(
  message: string,
  session: Session,
  snapshot: TemporalSnapshot,
  provider?: ModelProvider,
): Promise<ContinuityResult> {
  // Layer 1: Temporal
  const temporal = temporalLayer(snapshot, session.messages.length > 0);

  // No history — definitely a fresh start
  if (session.messages.length === 0) {
    return {
      classification: "FRESH_START",
      confidence: 0.95,
      reason: "No prior messages in session",
      layerUsed: 1,
    };
  }

  // Layer 2: Linguistic
  const linguistic = linguisticLayer(message);

  // Combine signals
  let finalClass: ContinuityClass;
  let finalConfidence: number;
  let reason: string;

  if (linguistic.bias !== null) {
    // Linguistic markers are the strongest signal
    if (linguistic.bias === temporal.bias || temporal.confidence < 0.6) {
      // Agree or temporal is weak — trust linguistic
      finalClass = linguistic.bias;
      finalConfidence = Math.min(
        0.95,
        temporal.confidence + 0.2,
      );
      reason = `Linguistic markers: ${linguistic.markers.join(", ")}`;
    } else {
      // Disagree — need Layer 3
      finalClass = linguistic.bias;
      finalConfidence = 0.5;
      reason = `Temporal says ${temporal.bias} but linguistic says ${linguistic.bias}`;
    }
  } else {
    // No linguistic markers — trust temporal
    finalClass = temporal.bias;
    finalConfidence = temporal.confidence;
    reason = `Temporal gap: ${snapshot.lastMessageGap ?? "unknown"}`;
  }

  // If confident enough, return without Layer 3
  if (finalConfidence >= 0.7) {
    return {
      classification: finalClass,
      confidence: finalConfidence,
      reason,
      layerUsed: linguistic.bias !== null ? 2 : 1,
    };
  }

  // Layer 3: Semantic coherence (only when ambiguous)
  if (provider) {
    log.engine.info(
      `[ContinuityEngine] Layers 1+2 ambiguous (conf=${finalConfidence.toFixed(2)}), invoking Layer 3`,
    );
    const semanticResult = await semanticLayer(
      message,
      session.messages,
      provider,
    );
    return {
      classification: semanticResult,
      confidence: 0.8,
      reason: `LLM classification: ${semanticResult} (temporal: ${temporal.bias}, linguistic: ${linguistic.bias ?? "none"})`,
      layerUsed: 3,
    };
  }

  // No provider available — return best guess from Layers 1+2
  return {
    classification: finalClass,
    confidence: finalConfidence,
    reason: reason + " (no LLM fallback available)",
    layerUsed: linguistic.bias !== null ? 2 : 1,
  };
}
