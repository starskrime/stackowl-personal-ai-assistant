/**
 * StackOwl — Micro-Learner
 *
 * Lightweight per-message signal extraction. Runs on EVERY user message
 * without any LLM calls — purely heuristic. Captures:
 *   - Topic mentions (what the user talks about)
 *   - Sentiment signals (positive/negative reactions to owl behavior)
 *   - Tool usage frequency (which skills/tools the user reaches for)
 *   - Temporal patterns (when the user is active)
 *   - Interaction style (message length, question rate, command rate)
 *
 * These micro-signals feed into the UserProfileModel for cross-system
 * intelligence without waiting for batch evolution cycles.
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { log } from '../logger.js';

// ─── Types ─────────────────────────────────────────────────────

export interface MicroSignal {
  timestamp: string;
  type: 'topic' | 'sentiment' | 'tool_use' | 'style' | 'temporal';
  key: string;
  value: number; // 0–1 intensity or count
}

export interface UserProfile {
  /** Topics the user discusses, with frequency counts */
  topics: Record<string, number>;
  /** Tools/skills the user has used, with usage counts */
  toolUsage: Record<string, number>;
  /** Average message length (rolling) */
  avgMessageLength: number;
  /** Fraction of messages that are questions */
  questionRate: number;
  /** Fraction of messages that are commands/imperatives */
  commandRate: number;
  /** Activity by hour of day (0-23), counts */
  hourlyActivity: number[];
  /** Activity by day of week (0=Sun, 6=Sat), counts */
  dailyActivity: number[];
  /** Positive sentiment signals count */
  positiveSignals: number;
  /** Negative sentiment signals count */
  negativeSignals: number;
  /** Total messages processed */
  totalMessages: number;
  /** Related capability clusters inferred from usage */
  capabilityClusters: Record<string, string[]>;
  /** Last updated timestamp */
  lastUpdated: string;
}

// ─── Capability Clusters ─────────────────────────────────────

/** Maps a capability to related ones the user might also need */
const CAPABILITY_GRAPH: Record<string, string[]> = {
  email: ['contacts', 'calendar', 'notification', 'template'],
  calendar: ['reminder', 'notification', 'email', 'schedule'],
  screenshot: ['screen_recording', 'clipboard', 'annotation'],
  phone_call: ['contacts', 'voicemail', 'sms'],
  weather: ['calendar', 'notification', 'travel'],
  news: ['summary', 'bookmark', 'notification'],
  reminder: ['calendar', 'notification', 'timer'],
  search: ['bookmark', 'summary', 'web_scrape'],
  file_management: ['backup', 'archive', 'sync'],
  music: ['playlist', 'timer', 'notification'],
  translation: ['language_detection', 'dictionary'],
  timer: ['reminder', 'notification', 'calendar'],
  contacts: ['email', 'phone_call', 'sms'],
  notification: ['quiet_hours', 'priority', 'schedule'],
  clipboard: ['screenshot', 'paste', 'history'],
  sms: ['contacts', 'phone_call', 'notification'],
  notes: ['bookmark', 'summary', 'search'],
};

// ─── Heuristic Detectors ─────────────────────────────────────

const TOPIC_PATTERNS: [RegExp, string][] = [
  [/\b(?:email|mail|inbox|send|compose)\b/i, 'email'],
  [/\b(?:calendar|event|meeting|schedule|appointment)\b/i, 'calendar'],
  [/\b(?:remind|reminder|alarm|timer|notify)\b/i, 'reminder'],
  [/\b(?:weather|forecast|temperature|rain|sunny)\b/i, 'weather'],
  [/\b(?:news|headlines|article|breaking)\b/i, 'news'],
  [/\b(?:screenshot|screen\s*cap|capture\s*screen)\b/i, 'screenshot'],
  [/\b(?:call|phone|facetime|dial)\b/i, 'phone_call'],
  [/\b(?:search|find|look\s*up|google)\b/i, 'search'],
  [/\b(?:file|folder|directory|document|download)\b/i, 'file_management'],
  [/\b(?:music|song|playlist|spotify|play)\b/i, 'music'],
  [/\b(?:translat|language|convert|interpret)\b/i, 'translation'],
  [/\b(?:note|memo|jot\s*down|write\s*down)\b/i, 'notes'],
  [/\b(?:code|program|debug|compile|deploy)\b/i, 'coding'],
  [/\b(?:photo|image|picture|camera)\b/i, 'media'],
  [/\b(?:travel|flight|hotel|trip|book)\b/i, 'travel'],
  [/\b(?:finance|money|budget|expense|payment)\b/i, 'finance'],
  [/\b(?:health|exercise|workout|diet|sleep)\b/i, 'health'],
  [/\b(?:task|todo|checklist|done|complete)\b/i, 'task_management'],
];

const POSITIVE_SIGNALS = [
  'thanks', 'thank you', 'perfect', 'great', 'awesome', 'nice',
  'exactly', 'yes', 'correct', 'good job', 'love it', 'well done',
  'helpful', '👍', '❤️', '🎉',
];

const NEGATIVE_SIGNALS = [
  'wrong', 'no', 'stop', 'not what i', "that's not", 'incorrect',
  'don\'t do that', 'undo', 'revert', 'too long', 'too verbose',
  'annoying', 'useless', '👎',
];

// ─── MicroLearner ────────────────────────────────────────────

export class MicroLearner {
  private profile: UserProfile;
  private filePath: string;
  private dirty = false;

  constructor(private workspacePath: string) {
    this.filePath = join(workspacePath, 'user-profile.json');
    this.profile = this.defaultProfile();
  }

  private defaultProfile(): UserProfile {
    return {
      topics: {},
      toolUsage: {},
      avgMessageLength: 0,
      questionRate: 0,
      commandRate: 0,
      hourlyActivity: new Array(24).fill(0),
      dailyActivity: new Array(7).fill(0),
      positiveSignals: 0,
      negativeSignals: 0,
      totalMessages: 0,
      capabilityClusters: {},
      lastUpdated: new Date().toISOString(),
    };
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.filePath)) return;
      const raw = readFileSync(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      this.profile = { ...this.defaultProfile(), ...data };
      log.engine.debug(`[MicroLearner] Loaded profile: ${this.profile.totalMessages} messages tracked`);
    } catch (err) {
      log.engine.warn(`[MicroLearner] Failed to load profile: ${err}`);
    }
  }

  /**
   * Process a single user message — zero LLM calls, pure heuristics.
   * Call this on every incoming message.
   */
  processMessage(message: string, usedTools?: string[]): MicroSignal[] {
    const signals: MicroSignal[] = [];
    const now = new Date();
    const timestamp = now.toISOString();

    this.profile.totalMessages++;
    this.dirty = true;

    // ─── Temporal pattern ─────────────────────────────
    this.profile.hourlyActivity[now.getHours()]++;
    this.profile.dailyActivity[now.getDay()]++;

    // ─── Message style ────────────────────────────────
    const len = message.trim().length;
    const n = this.profile.totalMessages;
    this.profile.avgMessageLength =
      ((this.profile.avgMessageLength * (n - 1)) + len) / n;

    const isQuestion = /\?/.test(message);
    this.profile.questionRate =
      ((this.profile.questionRate * (n - 1)) + (isQuestion ? 1 : 0)) / n;

    const isCommand = /^(do|run|send|open|get|check|show|tell|find|create|delete|set|make|take)\b/i.test(message.trim());
    this.profile.commandRate =
      ((this.profile.commandRate * (n - 1)) + (isCommand ? 1 : 0)) / n;

    // ─── Topic detection ──────────────────────────────
    const lower = message.toLowerCase();
    for (const [pattern, topic] of TOPIC_PATTERNS) {
      if (pattern.test(message)) {
        this.profile.topics[topic] = (this.profile.topics[topic] || 0) + 1;
        signals.push({ timestamp, type: 'topic', key: topic, value: 1 });
      }
    }

    // ─── Sentiment detection ──────────────────────────
    for (const sig of POSITIVE_SIGNALS) {
      if (lower.includes(sig)) {
        this.profile.positiveSignals++;
        signals.push({ timestamp, type: 'sentiment', key: 'positive', value: 1 });
        break;
      }
    }
    for (const sig of NEGATIVE_SIGNALS) {
      if (lower.includes(sig)) {
        this.profile.negativeSignals++;
        signals.push({ timestamp, type: 'sentiment', key: 'negative', value: 1 });
        break;
      }
    }

    // ─── Tool usage tracking ──────────────────────────
    if (usedTools) {
      for (const tool of usedTools) {
        this.profile.toolUsage[tool] = (this.profile.toolUsage[tool] || 0) + 1;
        signals.push({ timestamp, type: 'tool_use', key: tool, value: 1 });
      }
    }

    // ─── Update capability clusters ───────────────────
    this.updateCapabilityClusters();

    this.profile.lastUpdated = timestamp;
    return signals;
  }

  /**
   * Record that a tool/skill was used (called after tool execution).
   */
  recordToolUse(toolName: string): void {
    this.profile.toolUsage[toolName] = (this.profile.toolUsage[toolName] || 0) + 1;
    this.updateCapabilityClusters();
    this.dirty = true;
  }

  /**
   * Based on tools/skills the user has actually used, infer what
   * related capabilities they might need next.
   */
  private updateCapabilityClusters(): void {
    const clusters: Record<string, string[]> = {};

    for (const [tool, count] of Object.entries(this.profile.toolUsage)) {
      if (count < 2) continue; // Only cluster after repeated use
      const related = CAPABILITY_GRAPH[tool];
      if (related) {
        clusters[tool] = related.filter(r => !this.profile.toolUsage[r]);
      }
    }

    // Also cluster from topic mentions
    for (const [topic, count] of Object.entries(this.profile.topics)) {
      if (count < 3) continue;
      const related = CAPABILITY_GRAPH[topic];
      if (related) {
        const missing = related.filter(r => !this.profile.toolUsage[r]);
        if (missing.length > 0) {
          clusters[topic] = [...new Set([...(clusters[topic] || []), ...missing])];
        }
      }
    }

    this.profile.capabilityClusters = clusters;
  }

  /**
   * Get capabilities the user likely needs but doesn't have yet.
   * Sorted by confidence (based on usage frequency of related capabilities).
   */
  getAnticipatedNeeds(): { capability: string; reason: string; confidence: number }[] {
    const needs: Map<string, { reason: string; confidence: number }> = new Map();

    for (const [source, related] of Object.entries(this.profile.capabilityClusters)) {
      const sourceUsage = (this.profile.toolUsage[source] || 0) + (this.profile.topics[source] || 0);
      const confidence = Math.min(0.9, 0.3 + sourceUsage * 0.05);

      for (const cap of related) {
        const existing = needs.get(cap);
        if (!existing || existing.confidence < confidence) {
          needs.set(cap, {
            reason: `User frequently uses "${source}" (${sourceUsage}x) — "${cap}" is a common companion capability`,
            confidence,
          });
        }
      }
    }

    return Array.from(needs.entries())
      .map(([capability, { reason, confidence }]) => ({ capability, reason, confidence }))
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, 10);
  }

  /**
   * Get the user's most active hours (for proactive messaging timing).
   */
  getPeakHours(): number[] {
    const total = this.profile.hourlyActivity.reduce((s, v) => s + v, 0);
    if (total === 0) return [];

    const avg = total / 24;
    return this.profile.hourlyActivity
      .map((count, hour) => ({ hour, count }))
      .filter(h => h.count > avg * 1.5)
      .sort((a, b) => b.count - a.count)
      .map(h => h.hour);
  }

  /**
   * Get the user's top topics by frequency.
   */
  getTopTopics(limit = 5): { topic: string; count: number }[] {
    return Object.entries(this.profile.topics)
      .sort(([, a], [, b]) => b - a)
      .slice(0, limit)
      .map(([topic, count]) => ({ topic, count }));
  }

  /**
   * Get a summary string suitable for injection into LLM context.
   */
  toContextString(): string {
    if (this.profile.totalMessages < 5) return '';

    const parts: string[] = [];

    const topTopics = this.getTopTopics(5);
    if (topTopics.length > 0) {
      parts.push(`Frequently discussed: ${topTopics.map(t => t.topic).join(', ')}`);
    }

    const topTools = Object.entries(this.profile.toolUsage)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5);
    if (topTools.length > 0) {
      parts.push(`Most used tools: ${topTools.map(([t]) => t).join(', ')}`);
    }

    const peakHours = this.getPeakHours();
    if (peakHours.length > 0) {
      parts.push(`Most active hours: ${peakHours.map(h => `${h}:00`).join(', ')}`);
    }

    if (this.profile.commandRate > 0.6) {
      parts.push('User prefers direct commands over conversational style');
    } else if (this.profile.questionRate > 0.5) {
      parts.push('User often asks questions — provide explanatory answers');
    }

    const anticipated = this.getAnticipatedNeeds().slice(0, 3);
    if (anticipated.length > 0) {
      parts.push(`Might also need: ${anticipated.map(a => a.capability).join(', ')}`);
    }

    return parts.length > 0
      ? `<user_profile>\n${parts.join('\n')}\n</user_profile>`
      : '';
  }

  getProfile(): UserProfile {
    return { ...this.profile };
  }

  async save(): Promise<void> {
    if (!this.dirty) return;
    try {
      if (!existsSync(this.workspacePath)) {
        mkdirSync(this.workspacePath, { recursive: true });
      }
      writeFileSync(this.filePath, JSON.stringify(this.profile, null, 2), 'utf-8');
      this.dirty = false;
      log.engine.debug(`[MicroLearner] Saved profile (${this.profile.totalMessages} messages)`);
    } catch (err) {
      log.engine.warn(`[MicroLearner] Failed to save: ${err}`);
    }
  }
}
