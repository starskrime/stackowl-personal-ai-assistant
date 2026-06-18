/**
 * StackOwl — SentimentProbe
 *
 * Lightweight keyword classifier that detects whether the user's next message
 * is a correction ("no, that's wrong"), positive ("perfect!"), or neutral.
 *
 * When a correction is detected the owning PostProcessor increments
 * `challenge_instances` in `outcome_journal` so the owl's DNA evolution
 * can increase `challengeLevel` over time (making the owl less sycophantic).
 *
 * Uses an "arm/fire" pattern:
 *   1. PostProcessor calls `arm(userId)` after each assistant response.
 *   2. When the NEXT user message arrives, PostProcessor calls `onNextMessage(text)`.
 *   3. The probe classifies the text and invokes the callback exactly once.
 */

const CORRECTION_SIGNALS = [
  "no,",
  "no ",
  "wrong",
  "actually",
  "that's not",
  "thats not",
  "incorrect",
  "not right",
  "try again",
  "that's wrong",
  "not what i",
];

const POSITIVE_SIGNALS = [
  "thanks",
  "thank you",
  "perfect",
  "exactly",
  "great job",
  "that worked",
  "worked great",
  "well done",
  "exactly what",
  "👍",
  "✅",
];

export function classifySentiment(
  text: string,
): "positive" | "correction" | "neutral" {
  const lower = text.toLowerCase();
  if (CORRECTION_SIGNALS.some((s) => lower.includes(s))) return "correction";
  if (POSITIVE_SIGNALS.some((s) => lower.includes(s))) return "positive";
  return "neutral";
}

type SentimentCallback = (
  sentiment: "positive" | "correction" | "neutral",
  incrementChallenge: boolean,
) => void;

export class SentimentProbe {
  private pendingUserId: string | null = null;

  constructor(private readonly onResult: SentimentCallback) {}

  /**
   * Arm the probe for the next incoming user message.
   * The PostProcessor calls this after delivering an assistant response.
   */
  arm(userId: string): void {
    this.pendingUserId = userId;
  }

  /**
   * Process the next user message.
   * Always invokes the callback — `arm()` is only needed so the PostProcessor
   * can associate a userId with the correction for DB writes.
   * Clears the armed state after firing.
   */
  onNextMessage(text: string): void {
    this.pendingUserId = null; // clear arm regardless
    const sentiment = classifySentiment(text);
    const incrementChallenge = sentiment === "correction";
    this.onResult(sentiment, incrementChallenge);
  }

  /** Returns the currently armed userId, or null if not armed. */
  getArmedUserId(): string | null {
    return this.pendingUserId;
  }
}
