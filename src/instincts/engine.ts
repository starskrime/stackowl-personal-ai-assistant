/**
 * StackOwl — Instinct Engine
 *
 * Evaluates whether the current user message triggers any reactive instinct.
 *
 * Key fix: ALL instincts are evaluated in ONE LLM call (batch classification).
 * The previous implementation called the LLM once per instinct — 10 instincts
 * meant 10 extra API calls on every single user message before the real
 * response even started. This version sends all instincts to the model at once
 * and gets a single JSON decision back.
 */

import type { Instinct } from "./registry.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { log } from "../logger.js";

export class InstinctEngine {
  /**
   * Check if a user message triggers any of the provided instincts.
   * All instincts are evaluated in a SINGLE LLM call regardless of count.
   * Returns the first triggered instinct, or null if none apply.
   */
  async evaluate(
    userMessage: string,
    availableInstincts: Instinct[],
    context: {
      provider: ModelProvider;
      owl: OwlInstance;
      config: StackOwlConfig;
    },
  ): Promise<Instinct | null> {
    if (availableInstincts.length === 0) return null;

    const { provider } = context;

    // Format all instincts into a single numbered list
    const instinctList = availableInstincts
      .map(
        (inst, idx) =>
          `${idx + 1}. ID: "${inst.name}"\n   Conditions:\n${inst.conditions.map((c) => `   - ${c}`).join("\n")}`,
      )
      .join("\n\n");

    const systemPrompt =
      `You are a classifier that decides whether a user message triggers a behavioral instinct.\n` +
      `You will be given a list of instincts (each with conditions) and a user message.\n` +
      `Return a JSON object: { "triggered": true|false, "instinctId": "<name>" | null }\n` +
      `Only trigger an instinct if its conditions are CLEARLY met by the message.\n` +
      `If multiple instincts match, return only the first (highest priority) one.\n` +
      `Output ONLY valid JSON — no prose, no code fences.`;

    const userPrompt =
      `INSTINCTS:\n${instinctList}\n\n` +
      `USER MESSAGE:\n"${userMessage}"\n\n` +
      `Which instinct (if any) is triggered? Return JSON.`;

    try {
      const response = await provider.chat(
        [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        undefined,
        { temperature: 0, maxTokens: 128 },
      );

      let jsonStr = response.content.trim();
      // Strip markdown code fences if model wraps anyway
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```(?:json)?/, "")
          .replace(/```$/, "")
          .trim();
      }

      const parsed = JSON.parse(jsonStr) as {
        triggered: boolean;
        instinctId: string | null;
      };

      if (parsed.triggered && parsed.instinctId) {
        const triggered = availableInstincts.find(
          (i) => i.name === parsed.instinctId,
        );
        if (triggered) {
          log.engine.info(`[Instinct] ⚡ Triggered: "${triggered.name}"`);
          return triggered;
        }
      }

      return null;
    } catch (err) {
      log.engine.warn(
        `[Instinct] Batch evaluation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}
