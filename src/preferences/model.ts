/**
 * StackOwl — User Preference Model
 *
 * Infers user preferences from behavioral signals (not explicit statements).
 * Examples:
 *   - User sends short messages → concise response preference
 *   - User speaks Chinese → language inference
 *   - User active mornings → morning person pattern
 *   - User uses emojis → emoji-friendly preference
 *
 * Inferences use exponential moving average confidence — repeated signals
 * increase confidence, contradicting signals decrease it.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export interface InferredPreference {
  key: string;
  value: unknown;
  confidence: number;
  evidence: string[];
  lastUpdated: number;
}

export interface BehavioralSignal {
  type: string;
  value: unknown;
  timestamp: number;
}

export class UserPreferenceModel {
  private prefs: Map<string, InferredPreference> = new Map();
  private signals: BehavioralSignal[] = [];
  private filePath: string;
  private loaded = false;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "preferences", "inferred.json");
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data);
        if (parsed.prefs) {
          for (const [k, v] of Object.entries(parsed.prefs)) {
            this.prefs.set(k, v as InferredPreference);
          }
        }
        if (parsed.signals) {
          this.signals = parsed.signals;
        }
        log.engine.info(
          `[PreferenceModel] Loaded ${this.prefs.size} inferred preferences, ${this.signals.length} signals`,
        );
      }
    } catch (err) {
      log.engine.warn(
        `[PreferenceModel] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    const data = JSON.stringify(
      {
        prefs: Object.fromEntries(this.prefs),
        signals: this.signals.slice(-1000),
      },
      null,
      2,
    );
    await writeFile(this.filePath, data, "utf-8");
  }

  /**
   * Record a behavioral signal. Called after every user message.
   */
  recordSignal(type: string, value: unknown): void {
    this.signals.push({ type, value, timestamp: Date.now() });
    this.reevaluate(type, value);
  }

  /**
   * Update an inference based on a new signal.
   * Uses exponential moving average for confidence.
   */
  private reevaluate(type: string, value: unknown): void {
    const key = type;
    const existing = this.prefs.get(key);

    if (existing) {
      if (existing.value === value) {
        existing.confidence = Math.min(0.95, existing.confidence * 0.95 + 0.05);
      } else {
        existing.confidence = Math.max(0.1, existing.confidence * 0.7 - 0.05);
        if (existing.confidence < 0.15) {
          existing.value = value;
          existing.confidence = 0.3;
        }
      }
      existing.evidence.push(`${value} @ ${new Date().toISOString()}`);
      if (existing.evidence.length > 20) existing.evidence.shift();
      existing.lastUpdated = Date.now();
    } else {
      this.prefs.set(key, {
        key,
        value,
        confidence: 0.3,
        evidence: [`${value} @ ${new Date().toISOString()}`],
        lastUpdated: Date.now(),
      });
    }
  }

  get(key: string, defaultVal?: unknown): unknown {
    return this.prefs.get(key)?.value ?? defaultVal;
  }

  getWithConfidence(key: string): InferredPreference | undefined {
    return this.prefs.get(key);
  }

  getAll(): InferredPreference[] {
    return [...this.prefs.values()];
  }

  /**
   * Called after each user message to record behavioral signals.
   */
  analyzeMessage(
    userMessage: string,
    _channelId: string,
    timestamp: number = Date.now(),
  ): void {
    // Message length
    const wordCount = userMessage.trim().split(/\s+/).length;
    this.recordSignal("msg_length_avg", wordCount);

    // Language detection (simple heuristic + char analysis)
    const hasChinese = /[\u4e00-\u9fff]/.test(userMessage);
    const hasEmoji = /[\u{1F300}-\u{1F9FF}]/u.test(userMessage);
    const hasRussian = /[\u0400-\u04FF]/.test(userMessage);
    if (hasChinese) this.recordSignal("language", "zh");
    else if (hasRussian) this.recordSignal("language", "ru");
    else this.recordSignal("language", "en");

    if (hasEmoji) this.recordSignal("uses_emoji", true);

    // Time-of-day pattern
    const hour = new Date(timestamp).getHours();
    if (hour >= 5 && hour < 12)
      this.recordSignal("time_of_day_pattern", "morning");
    else if (hour >= 12 && hour < 17)
      this.recordSignal("time_of_day_pattern", "afternoon");
    else if (hour >= 17 && hour < 21)
      this.recordSignal("time_of_day_pattern", "evening");
    else this.recordSignal("time_of_day_pattern", "night");

    // Question vs command
    const isQuestion =
      /[吗？?]$/.test(userMessage.trim()) || userMessage.includes("?");
    if (isQuestion) this.recordSignal("message_type", "question");
    else if (
      /^(帮我|帮我做|帮我查|帮我预订|can you|please |help me|帮我安排)/.test(
        userMessage.toLowerCase(),
      )
    ) {
      this.recordSignal("message_type", "task");
    }

    // Message style estimate based on length
    if (wordCount <= 5)
      this.recordSignal("preferred_response_length", "concise");
    else if (wordCount <= 20)
      this.recordSignal("preferred_response_length", "normal");
    else this.recordSignal("preferred_response_length", "detailed");
  }

  /**
   * Returns high-confidence inferred preferences for system prompt injection.
   */
  toContextString(minConfidence = 0.4): string {
    const highConf = [...this.prefs.values()].filter(
      (p) => p.confidence >= minConfidence,
    );
    if (highConf.length === 0) return "";

    const lines = highConf.map((p) => {
      const conf = Math.round(p.confidence * 100);
      return `- ${p.key}: ${p.value} (${conf}% confidence)`;
    });
    return `## Inferred User Preferences (from behavior)\n${lines.join("\n")}\n`;
  }

  /**
   * Returns a summary of the user's communication style.
   */
  getCommunicationStyle(): string {
    const lang = this.get("language", "en") as string;
    const respLen = this.get("preferred_response_length", "normal") as string;
    const usesEmoji = this.get("uses_emoji", false) as boolean;
    const timePattern = this.get("time_of_day_pattern", "unknown") as string;

    return `Language: ${lang} | Response style: ${respLen} | Uses emojis: ${usesEmoji} | Most active: ${timePattern}`;
  }
}
