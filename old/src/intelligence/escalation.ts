import { log } from "../logger.js";
import type { Tier } from "./router.js";

export const TIER_ORDER: Tier[] = ["low", "mid", "high"];

const RESET_MS = 15 * 60 * 1000;

// Heuristic patterns that signal the user is unhappy with the previous response.
// No LLM call — pure regex so it's instant and free.
const CORRECTION_PATTERNS: RegExp[] = [
  /\b(wrong|incorrect|nope|no[,.]?\s+(that'?s?|you|it)|not right|not what i (said|meant|asked|wanted))\b/i,
  /\b(try again|redo|redo this|do it again|one more time|again|still (wrong|not|didn'?t))\b/i,
  /\b(you (didn'?t|don'?t|missed|ignored|forgot)|didn'?t answer|didn'?t (address|include|cover))\b/i,
  /\b(that'?s (not|wrong|off|incorrect|bad|terrible|useless)|that doesn'?t (work|help|make sense))\b/i,
  /\b(rephrase|rewrite|restate|be more (specific|detailed|clear|precise|careful))\b/i,
  /\b(not helpful|unhelpful|makes no sense|what\?|huh\?|what do you mean)\b/i,
  /\bi (said|meant|asked|told you|already said)\b/i,
];

export class TierEscalationManager {
  private floor: Tier = "low";
  private lastEscalatedAt = 0;

  get currentFloor(): Tier {
    return this.floor;
  }

  /**
   * Call at the start of every message.
   * Resets the floor back to "low" if 15 minutes have elapsed since the last escalation.
   */
  checkAutoReset(): void {
    if (this.floor === "low") return;
    if (Date.now() - this.lastEscalatedAt >= RESET_MS) {
      log.engine.info(
        `[TierEscalation] 15 min idle — resetting tier floor from ${this.floor} → low`,
      );
      this.floor = "low";
      this.lastEscalatedAt = 0;
    }
  }

  /**
   * Move the floor up one step (low→mid, mid→high, high stays).
   * Returns the new floor.
   */
  escalate(): Tier {
    const idx = TIER_ORDER.indexOf(this.floor);
    if (idx < TIER_ORDER.length - 1) {
      const next = TIER_ORDER[idx + 1]!;
      log.engine.warn(
        `[TierEscalation] Escalating tier floor: ${this.floor} → ${next}`,
      );
      this.floor = next;
      this.lastEscalatedAt = Date.now();
    }
    return this.floor;
  }

  /**
   * Explicit reset — e.g. user starts a completely new topic.
   */
  reset(): void {
    log.engine.info("[TierEscalation] Manual reset → low");
    this.floor = "low";
    this.lastEscalatedAt = 0;
  }

  /**
   * Returns true if the message looks like a correction or expression of dissatisfaction.
   * Uses fast regex heuristics — no LLM call.
   */
  detectCorrectionSignal(userMessage: string): boolean {
    return CORRECTION_PATTERNS.some((p) => p.test(userMessage));
  }
}
