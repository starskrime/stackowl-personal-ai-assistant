/**
 * StackOwl — User Mental Model
 *
 * Heuristic user state inference based on behavioral signals.
 * Observes message length, response latency, topic switches,
 * and linguistic cues to infer the user's current state.
 *
 * Calibrates from a baseline of observed behavior (10+ sessions).
 * Only surfaces high-confidence inferences (>= 0.6).
 * Never mentions the inference to the user directly.
 */

import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

export type UserLikelyState =
  | "focused"
  | "browsing"
  | "frustrated"
  | "in_a_hurry"
  | "exploring";

export interface UserState {
  likelyState: UserLikelyState;
  confidence: number;
}

interface SessionSignals {
  messageLengths: number[];
  responseLatencies: number[]; // ms between messages
  topicSwitchCount: number;
  clarificationCount: number;
  questionRepetitions: number;
}

interface Baseline {
  avgMessageLength: number;
  avgResponseLatency: number;
  sessionCount: number;
}

// ─── Constants ──────────────────────────────────────────────────

const CALIBRATION_THRESHOLD = 10; // sessions before we trust our baseline
const CONFIDENCE_THRESHOLD = 0.6;
const MAX_SIGNALS_PER_SESSION = 50; // prevent unbounded arrays

// ─── User Mental Model ─────────────────────────────────────────

export class UserMentalModel {
  private baseline: Baseline = {
    avgMessageLength: 0,
    avgResponseLatency: 0,
    sessionCount: 0,
  };
  private signals: SessionSignals = {
    messageLengths: [],
    responseLatencies: [],
    topicSwitchCount: 0,
    clarificationCount: 0,
    questionRepetitions: 0,
  };
  private lastMessageAt: number | null = null;
  private lastUserMessages: string[] = [];
  private cachedState: UserState | null = null;
  private dirty = false;

  constructor(baseline?: Baseline) {
    if (baseline) this.baseline = baseline;
  }

  /**
   * Get the current baseline for persistence.
   */
  getBaseline(): Baseline {
    return { ...this.baseline };
  }

  /**
   * Whether the model is calibrated (enough sessions observed).
   */
  isCalibrated(): boolean {
    return this.baseline.sessionCount >= CALIBRATION_THRESHOLD;
  }

  /**
   * Record a new user message. Call on every incoming message.
   * Instant — no LLM.
   */
  update(messageContent: string, timestamp: number = Date.now()): void {
    this.dirty = true;
    this.cachedState = null;

    // Track message length
    if (this.signals.messageLengths.length < MAX_SIGNALS_PER_SESSION) {
      this.signals.messageLengths.push(messageContent.length);
    }

    // Track response latency (time since last message)
    if (this.lastMessageAt !== null) {
      const latency = timestamp - this.lastMessageAt;
      if (latency > 0 && latency < 30 * 60 * 1000) {
        // ignore gaps >30min
        if (this.signals.responseLatencies.length < MAX_SIGNALS_PER_SESSION) {
          this.signals.responseLatencies.push(latency);
        }
      }
    }
    this.lastMessageAt = timestamp;

    // Detect clarification requests
    const CLARIFICATION =
      /\b(?:what do you mean|i don't understand|can you explain|clarify|huh\??|что|не понял|в смысле)\b/i;
    if (CLARIFICATION.test(messageContent)) {
      this.signals.clarificationCount++;
    }

    // Detect question repetition (user asking same thing again)
    const normalized = messageContent.toLowerCase().trim();
    if (
      this.lastUserMessages.length > 0 &&
      this.lastUserMessages.some(
        (prev) =>
          prev === normalized ||
          (normalized.length > 10 && prev.includes(normalized.slice(0, 20))),
      )
    ) {
      this.signals.questionRepetitions++;
    }

    // Keep last 5 messages for repetition detection
    this.lastUserMessages.push(normalized);
    if (this.lastUserMessages.length > 5) {
      this.lastUserMessages.shift();
    }
  }

  /**
   * Record a topic switch (from ContinuityEngine classification).
   */
  recordTopicSwitch(): void {
    this.dirty = true;
    this.cachedState = null;
    this.signals.topicSwitchCount++;
  }

  /**
   * Call at end of session to update baseline with this session's data.
   */
  endSession(): void {
    if (this.signals.messageLengths.length === 0) return;

    const avgLen =
      this.signals.messageLengths.reduce((a, b) => a + b, 0) /
      this.signals.messageLengths.length;
    const avgLat =
      this.signals.responseLatencies.length > 0
        ? this.signals.responseLatencies.reduce((a, b) => a + b, 0) /
          this.signals.responseLatencies.length
        : this.baseline.avgResponseLatency;

    // Rolling average update
    const n = this.baseline.sessionCount;
    this.baseline.avgMessageLength =
      (this.baseline.avgMessageLength * n + avgLen) / (n + 1);
    this.baseline.avgResponseLatency =
      (this.baseline.avgResponseLatency * n + avgLat) / (n + 1);
    this.baseline.sessionCount = n + 1;

    // Reset session signals
    this.resetSessionSignals();

    log.engine.info(
      `[UserMentalModel] Baseline updated: session #${this.baseline.sessionCount}, avgLen=${this.baseline.avgMessageLength.toFixed(0)}, avgLat=${(this.baseline.avgResponseLatency / 1000).toFixed(1)}s`,
    );
  }

  /**
   * Reset session-scoped signals without updating baseline.
   */
  resetSessionSignals(): void {
    this.signals = {
      messageLengths: [],
      responseLatencies: [],
      topicSwitchCount: 0,
      clarificationCount: 0,
      questionRepetitions: 0,
    };
    this.lastMessageAt = null;
    this.lastUserMessages = [];
    this.cachedState = null;
    this.dirty = false;
  }

  /**
   * Returns inferred user state, or null if not calibrated or not confident.
   */
  getState(): UserState | null {
    if (!this.isCalibrated()) return null;
    if (this.signals.messageLengths.length < 3) return null; // need at least 3 messages

    if (this.cachedState && !this.dirty) return this.cachedState;

    const state = this.inferState();
    this.cachedState = state;
    this.dirty = false;

    return state && state.confidence >= CONFIDENCE_THRESHOLD ? state : null;
  }

  /**
   * Format for system prompt injection.
   * Returns empty string if not calibrated or low confidence.
   */
  toContextString(): string {
    const state = this.getState();
    if (!state) return "";

    const directives: Record<UserLikelyState, string> = {
      frustrated:
        "The user may be frustrated — be extra clear, acknowledge difficulty, offer step-by-step help.",
      in_a_hurry:
        "The user appears to be in a hurry — keep responses concise and direct, skip pleasantries.",
      exploring:
        "The user is in exploration mode — offer ideas, alternatives, and thought-provoking angles.",
      browsing:
        "The user is browsing across topics — keep responses brief and self-contained.",
      focused:
        "The user is focused on a task — stay on-topic and provide detailed, actionable responses.",
    };

    return `\n<user_state inference="${state.likelyState}" confidence="${state.confidence.toFixed(2)}">\n${directives[state.likelyState]}\n</user_state>\n`;
  }

  // ─── Private ──────────────────────────────────────────────────

  private inferState(): UserState {
    const avgLen =
      this.signals.messageLengths.reduce((a, b) => a + b, 0) /
      this.signals.messageLengths.length;
    const avgLat =
      this.signals.responseLatencies.length > 0
        ? this.signals.responseLatencies.reduce((a, b) => a + b, 0) /
          this.signals.responseLatencies.length
        : this.baseline.avgResponseLatency;

    const lenRatio =
      this.baseline.avgMessageLength > 0
        ? avgLen / this.baseline.avgMessageLength
        : 1;
    const latRatio =
      this.baseline.avgResponseLatency > 0
        ? avgLat / this.baseline.avgResponseLatency
        : 1;

    const recentLengths = this.signals.messageLengths.slice(-3);
    const consecutiveShort = recentLengths.every(
      (l) =>
        this.baseline.avgMessageLength > 0 &&
        l < this.baseline.avgMessageLength * 0.3,
    );

    // Score each state
    const scores: Record<UserLikelyState, number> = {
      frustrated: 0,
      in_a_hurry: 0,
      exploring: 0,
      browsing: 0,
      focused: 0.3, // default bias
    };

    // Frustrated: short messages, clarifications, repetitions
    if (consecutiveShort) scores.frustrated += 0.3;
    if (this.signals.clarificationCount >= 2) scores.frustrated += 0.3;
    if (this.signals.questionRepetitions >= 1) scores.frustrated += 0.25;

    // In a hurry: fast responses + short messages
    if (latRatio < 0.5 && lenRatio < 0.5) scores.in_a_hurry += 0.5;
    else if (latRatio < 0.7 && lenRatio < 0.7) scores.in_a_hurry += 0.3;

    // Exploring: long messages, exploration markers detected outside
    if (lenRatio > 1.5 && this.signals.messageLengths.length >= 5) {
      scores.exploring += 0.3;
    }

    // Browsing: many topic switches
    if (this.signals.topicSwitchCount >= 4) scores.browsing += 0.65;
    else if (this.signals.topicSwitchCount >= 3) scores.browsing += 0.5;
    else if (this.signals.topicSwitchCount >= 2) scores.browsing += 0.3;

    // Focused: no topic switches, steady pace
    if (
      this.signals.topicSwitchCount === 0 &&
      this.signals.messageLengths.length >= 5
    ) {
      scores.focused += 0.3;
    }

    // Find highest score
    let best: UserLikelyState = "focused";
    let bestScore = 0;
    for (const [state, score] of Object.entries(scores) as [
      UserLikelyState,
      number,
    ][]) {
      if (score > bestScore) {
        bestScore = score;
        best = state;
      }
    }

    // Confidence is the best score, capped at 0.9
    const confidence = Math.min(bestScore, 0.9);

    return { likelyState: best, confidence };
  }
}
