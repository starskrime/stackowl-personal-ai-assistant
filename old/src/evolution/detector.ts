/**
 * StackOwl — Capability Gap Detector
 *
 * Two-stage detection:
 *   1. Structured marker [CAPABILITY_GAP: ...] — zero cost, injected via system prompt
 *   2. LLM binary classifier — runs only when response looks like a refusal,
 *      works regardless of apostrophe encoding, phrasing, or language
 */

import type { ModelProvider } from "../providers/base.js";

export type GapType = "TOOL_MISSING" | "CAPABILITY_GAP";

export interface CapabilityGap {
  type: GapType;
  attemptedToolName?: string;
  userRequest: string;
  description: string;
}

// Structured marker set by the system prompt — deterministic when the model includes it
const STRUCTURED_MARKER = /\[CAPABILITY_GAP:\s*([^\]]+)\]/i;

// Cheap Unicode-aware pre-filter — avoids running the classifier on every message.
// Intentionally narrow: only phrases that signal a TECHNICAL capability limit, not
// emotional language ("I'm sorry") or knowledge gaps ("I don't have data on X").
// False positives here trigger an expensive LLM classifier call and potentially
// unnecessary skill synthesis — keep this list tight.
const REFUSAL_SIGNALS = [
  "i cannot perform",
  "i am unable to perform",
  "unable to execute",
  "unable to access",
  "unable to control",
  "unable to connect",
  "i lack the ability",
  "not equipped to",
  "no tool available",
  "no capability to",
  "beyond my capabilities",
  "outside my current capabilities",
  "don\u2019t have access to", // don't have access to (curly quote)
  "don't have access to", // don't have access to (straight quote)
  "do not have access to",
  "i don\u2019t have a tool",
  "i don't have a tool",
  "no way to directly",
  "cannot directly",
  "don\u2019t currently have",
  "don't currently have",
  "i can\u2019t directly",
  "i can't directly",
  "there isn\u2019t a tool",
  "there isn't a tool",
  "doesn\u2019t have the capability",
  "doesn't have the capability",
  "not currently possible for me",
  "i would need a tool",
  "requires a capability i don",
];

export class GapDetector {
  /**
   * Detect whether the LLM's response signals a capability gap.
   *
   * Async because stage 2 calls the LLM for binary classification.
   * Returns null if no gap detected (normal response).
   */
  async detectFromResponse(
    responseText: string,
    userRequest: string,
    provider: ModelProvider,
    model: string,
  ): Promise<CapabilityGap | null> {
    // Stage 1: structured marker — instant, no API call
    const markerMatch = responseText.match(STRUCTURED_MARKER);
    if (markerMatch) {
      console.log(`[GapDetector] structured marker found`);
      return {
        type: "CAPABILITY_GAP",
        userRequest,
        description: markerMatch[1].trim(),
      };
    }

    // Stage 2: pre-filter — skip classifier on clearly normal responses
    const lower = responseText.toLowerCase();
    const looksLikeRefusal = REFUSAL_SIGNALS.some((s) => lower.includes(s));
    if (!looksLikeRefusal) {
      return null;
    }

    console.log(
      `[GapDetector] refusal signal found, running LLM classifier...`,
    );
    return this.classifyWithLLM(responseText, userRequest, provider, model);
  }

  /**
   * Build a gap from a tool call that failed because the tool doesn't exist.
   * Synchronous — no classifier needed, the failure is unambiguous.
   */
  fromMissingTool(toolName: string, userRequest: string): CapabilityGap {
    return {
      type: "TOOL_MISSING",
      attemptedToolName: toolName,
      userRequest,
      description: `The owl tried to call a tool named "${toolName}" which does not exist in the registry.`,
    };
  }

  private async classifyWithLLM(
    responseText: string,
    userRequest: string,
    provider: ModelProvider,
    model: string,
  ): Promise<CapabilityGap | null> {
    const prompt = `You are a binary classifier. Read the user request and AI response below, then answer with YES or NO only.

User request: "${userRequest}"
AI response: "${responseText.slice(0, 500)}"

Question: Is the AI declining because it LACKS A TECHNICAL TOOL or SYSTEM CAPABILITY to do this task?

Answer YES if the AI is saying it cannot physically do something due to missing tools, access, or technical capability — for example: can't take a screenshot, can't send an email, can't access a database, can't control the screen, can't make a call.

Answer NO if the AI is: refusing for ethical/policy reasons, asking for clarification, saying it doesn't know a fact, or successfully completing the task.

Reply with a single word: YES or NO.`;

    try {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        model,
      );

      const answer = response.content.trim().toUpperCase();
      console.log(`[GapDetector] classifier answered: ${answer}`);

      if (answer.startsWith("YES")) {
        return {
          type: "CAPABILITY_GAP",
          userRequest,
          description: responseText.slice(0, 300),
        };
      }
    } catch (err) {
      console.warn(
        `[GapDetector] classifier call failed: ${err instanceof Error ? err.message : err}`,
      );
    }

    return null;
  }
}
