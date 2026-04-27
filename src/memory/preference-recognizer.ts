/**
 * StackOwl — Preference Recognizer
 *
 * Recognizes and stores user preferences from both explicit statements
 * and behavioral signals during conversation.
 */

import type { FactStore, FactCategory } from "./fact-store.js";
import { log } from "../logger.js";

export interface RecognizedPreference {
  key: string;
  value: unknown;
  confidence: number;
  source: "explicit" | "inferred";
  category: FactCategory;
  evidence: string[];
  createdAt: string;
}

export interface PreferenceSignal {
  type: string;
  value: unknown;
  timestamp: number;
  source: "explicit" | "behavioral";
}

interface PreferencePattern {
  pattern: RegExp;
  key: string;
  valueExtractor: (match: RegExpMatchArray) => unknown;
  category: FactCategory;
  confidence: number;
  source: "explicit" | "inferred";
}

const EXPLICIT_PATTERNS: PreferencePattern[] = [
  {
    pattern: /i prefer (\w+)/i,
    key: "preferred_response_length",
    valueExtractor: (m) => m[1].toLowerCase(),
    category: "preference",
    confidence: 0.9,
    source: "explicit",
  },
  {
    pattern: /i like (.+)/i,
    key: "likes",
    valueExtractor: (m) => m[1].trim(),
    category: "preference",
    confidence: 0.85,
    source: "explicit",
  },
  {
    pattern: /i don't like (.+)/i,
    key: "dislikes",
    valueExtractor: (m) => m[1].trim(),
    category: "preference",
    confidence: 0.85,
    source: "explicit",
  },
  {
    pattern: /call me (.+)/i,
    key: "name",
    valueExtractor: (m) => m[1].trim(),
    category: "personal",
    confidence: 0.95,
    source: "explicit",
  },
  {
    pattern: /my name is (.+)/i,
    key: "name",
    valueExtractor: (m) => m[1].trim(),
    category: "personal",
    confidence: 0.95,
    source: "explicit",
  },
  {
    pattern: /i'm in (.+ timezone|i'm in timezone)/i,
    key: "timezone",
    valueExtractor: (m) => m[1]?.trim() ?? "inferred",
    category: "personal",
    confidence: 0.6,
    source: "inferred",
  },
  {
    pattern: /use (.+)/i,
    key: "tool_preference",
    valueExtractor: (m) => m[1].trim(),
    category: "skill",
    confidence: 0.8,
    source: "explicit",
  },
  {
    pattern: /don't use (.+)/i,
    key: "tool_avoidance",
    valueExtractor: (m) => m[1].trim(),
    category: "skill",
    confidence: 0.8,
    source: "explicit",
  },
];

const IMPLICIT_PATTERNS: PreferencePattern[] = [
  {
    pattern: /[\u4e00-\u9fff]/,
    key: "language",
    valueExtractor: () => "zh",
    category: "preference",
    confidence: 0.7,
    source: "inferred",
  },
  {
    pattern: /[\u0400-\u04FF]/,
    key: "language",
    valueExtractor: () => "ru",
    category: "preference",
    confidence: 0.7,
    source: "inferred",
  },
  {
    pattern: /[\u{1F300}-\u{1F9FF}]/u,
    key: "uses_emoji",
    valueExtractor: () => true,
    category: "preference",
    confidence: 0.6,
    source: "inferred",
  },
  {
    pattern: /^(hi|hey|hello)/i,
    key: "greeting_style",
    valueExtractor: (m) => m[1].toLowerCase(),
    category: "preference",
    confidence: 0.5,
    source: "inferred",
  },
  {
    pattern: /(!{2,}|\.{3,}|\?{2,})/,
    key: "punctuation_style",
    valueExtractor: (m) => m[1].length > 2 ? "emphasis" : "normal",
    category: "preference",
    confidence: 0.4,
    source: "inferred",
  },
];

const HIGH_CONFIDENCE_THRESHOLD = 0.7;
const MEDIUM_CONFIDENCE_THRESHOLD = 0.4;

export class PreferenceRecognizer {
  private signals: PreferenceSignal[] = [];
  private factStore?: FactStore;

  constructor(factStore?: FactStore) {
    this.factStore = factStore;
  }

  /**
   * Analyze a message for preference signals
   */
  async recognizeFromMessage(message: string): Promise<RecognizedPreference[]> {
    const preferences: RecognizedPreference[] = [];

    const explicitPrefs = this.extractExplicitPreferences(message);
    preferences.push(...explicitPrefs);

    const implicitPrefs = this.extractImplicitPreferences(message);
    for (const implicit of implicitPrefs) {
      const existing = preferences.find((p) => p.key === implicit.key);
      if (!existing) {
        preferences.push(implicit);
      } else if (implicit.confidence > existing.confidence) {
        preferences[preferences.indexOf(existing)] = implicit;
      }
    }

    for (const pref of preferences) {
      this.signals.push({
        type: pref.key,
        value: pref.value,
        timestamp: Date.now(),
        source: pref.source === "explicit" ? "explicit" : "behavioral",
      });

      if (this.factStore && pref.confidence >= MEDIUM_CONFIDENCE_THRESHOLD) {
        await this.persistPreference(pref);
      }
    }

    if (preferences.length > 0) {
      log.engine.debug(
        `[PreferenceRecognizer] Recognized ${preferences.length} preferences from message`,
      );
    }

    return preferences;
  }

  /**
   * Extract explicit preferences (user stated directly)
   */
  private extractExplicitPreferences(message: string): RecognizedPreference[] {
    const preferences: RecognizedPreference[] = [];

    for (const { pattern } of EXPLICIT_PATTERNS) {
      const match = message.match(pattern);
      if (match) {
        const patternSpec = EXPLICIT_PATTERNS.find(p => p.pattern === pattern);
        preferences.push({
          key: patternSpec!.key,
          value: patternSpec!.valueExtractor(match),
          confidence: patternSpec!.confidence,
          source: patternSpec!.source,
          category: patternSpec!.category,
          evidence: [message.slice(0, 200)],
          createdAt: new Date().toISOString(),
        });
      }
    }

    return preferences;
  }

  /**
   * Extract implicit preferences (inferred from behavior)
   */
  private extractImplicitPreferences(message: string): RecognizedPreference[] {
    const preferences: RecognizedPreference[] = [];

    for (const patternSpec of IMPLICIT_PATTERNS) {
      const match = message.match(patternSpec.pattern);
      if (match) {
        preferences.push({
          key: patternSpec.key,
          value: patternSpec.valueExtractor(match),
          confidence: patternSpec.confidence,
          source: patternSpec.source,
          category: patternSpec.category,
          evidence: [message.slice(0, 200)],
          createdAt: new Date().toISOString(),
        });
      }
    }

    return preferences;
  }

  /**
   * Persist a preference to FactStore
   */
  private async persistPreference(pref: RecognizedPreference): Promise<void> {
    if (!this.factStore) return;

    try {
      await this.factStore.add({
        userId: "default",
        fact: `${pref.key}: ${String(pref.value)}`,
        category: pref.category,
        confidence: pref.confidence,
        source: pref.source === "explicit" ? "explicit" : "inferred",
      });
    } catch (err) {
      log.engine.warn(
        `[PreferenceRecognizer] Failed to persist preference: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Get all recognized signals
   */
  getSignals(): PreferenceSignal[] {
    return [...this.signals];
  }

  /**
   * Get signals by type
   */
  getSignalsByType(type: string): PreferenceSignal[] {
    return this.signals.filter((s) => s.type === type);
  }

  /**
   * Build context string for high-confidence preferences
   */
  buildContextString(minConfidence = MEDIUM_CONFIDENCE_THRESHOLD): string {
    const byKey = new Map<string, RecognizedPreference>();

    for (const signal of this.signals) {
      const existing = byKey.get(signal.type);
      if (!existing) {
        byKey.set(signal.type, {
          key: signal.type,
          value: signal.value,
          confidence: signal.timestamp > 0 ? 0.5 : 0.3,
          source: signal.source === "explicit" ? "explicit" : "inferred",
          category: "preference",
          evidence: [],
          createdAt: new Date(signal.timestamp).toISOString(),
        });
      }
    }

    const highConf = [...byKey.values()].filter(
      (p) => p.confidence >= minConfidence,
    );

    if (highConf.length === 0) return "";

    const lines = highConf.map((p) => {
      const conf = Math.round(p.confidence * 100);
      return `- ${p.key}: ${String(p.value)} (${conf}% confidence, ${p.source})`;
    });

    return `## Recognized Preferences\n${lines.join("\n")}\n`;
  }

  /**
   * Get preference summary by category
   */
  getPreferenceSummary(): {
    high: number;
    medium: number;
    low: number;
    categories: Record<string, number>;
  } {
    const byKey = new Map<string, RecognizedPreference>();

    for (const signal of this.signals) {
      const existing = byKey.get(signal.type);
      if (existing) {
        existing.confidence = Math.max(existing.confidence, 0.3);
      } else {
        byKey.set(signal.type, {
          key: signal.type,
          value: signal.value,
          confidence: 0.3,
          source: signal.source === "explicit" ? "explicit" : "inferred",
          category: "preference",
          evidence: [],
          createdAt: new Date().toISOString(),
        });
      }
    }

    const prefs = [...byKey.values()];
    let high = 0;
    let medium = 0;
    let low = 0;
    const categories: Record<string, number> = {};

    for (const p of prefs) {
      if (p.confidence >= HIGH_CONFIDENCE_THRESHOLD) high++;
      else if (p.confidence >= MEDIUM_CONFIDENCE_THRESHOLD) medium++;
      else low++;

      categories[p.category] = (categories[p.category] ?? 0) + 1;
    }

    return { high, medium, low, categories };
  }
}
