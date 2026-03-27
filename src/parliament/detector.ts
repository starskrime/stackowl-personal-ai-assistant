/**
 * StackOwl — Parliament Auto-Detector
 *
 * Uses the AI model to decide whether a user message would benefit
 * from a multi-perspective Parliament debate.
 *
 * No hard-coded keyword lists — the LLM understands nuance, context,
 * and intent far better than regex patterns ever could.
 */

import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

/**
 * Ask the AI model whether this message warrants a Parliament debate.
 * Returns true if the message is a complex decision, dilemma, or tradeoff
 * that genuinely benefits from hearing multiple opposing viewpoints.
 *
 * Quick-exits for obviously short or trivial messages to avoid wasting
 * an LLM call on "hi" or "thanks".
 */
export async function shouldConveneParliament(
  userMessage: string,
  provider: ModelProvider,
): Promise<boolean> {
  // Minimum length sanity check — no LLM call needed for tiny messages
  if (userMessage.trim().length < 25) return false;

  try {
    const response = await provider.chat(
      [
        {
          role: "user",
          content:
            `Classify this message. Reply with ONLY the single word "DEBATE" or "SINGLE".\n\n` +
            `DEBATE = the message asks about a decision, dilemma, tradeoff, or choice where reasonable people could disagree. ` +
            `Examples: "Should I switch careers?", "Should we use React or Vue?", "Is it worth buying a house now?", ` +
            `"Should I accept this job offer?", "Microservices vs monolith?"\n\n` +
            `SINGLE = everything else (factual questions, greetings, tasks, commands, coding requests). ` +
            `Examples: "What's the weather?", "Write a function", "Search for news", "Hello", "Summarize this"\n\n` +
            `Message: "${userMessage}"\n\n` +
            `Reply with ONLY one word: DEBATE or SINGLE`,
        },
      ],
      undefined,
      { temperature: 0, maxTokens: 64 },
    );

    const answer = response.content.trim().toUpperCase();
    // Look for DEBATE anywhere in the response (handles models that add explanation)
    // Also handle models that say things like "This is a DEBATE question"
    const shouldConvene =
      answer.includes("DEBATE") && !answer.includes("SINGLE");

    log.engine.info(
      `[ParliamentDetector] "${userMessage.slice(0, 60)}..." → raw="${answer}" → ${shouldConvene ? "PARLIAMENT" : "skip"}`,
    );

    return shouldConvene;
  } catch (err) {
    log.engine.debug(
      `[ParliamentDetector] Detection failed: ${err instanceof Error ? err.message : String(err)}`,
    );
    return false;
  }
}
