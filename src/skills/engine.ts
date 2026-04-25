/**
 * StackOwl — Skills Engine
 *
 * Evaluates whether the current user message triggers any reactive (behavioral) skill.
 * All skills are evaluated in ONE LLM call (batch classification).
 */

import type { Skill } from "./types.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { log } from "../logger.js";

export class SkillsEngine {
  async evaluate(
    userMessage: string,
    availableSkills: Skill[],
    context: {
      provider: ModelProvider;
      owl: OwlInstance;
      config: StackOwlConfig;
    },
  ): Promise<Skill | null> {
    if (availableSkills.length === 0) return null;

    const { provider } = context;

    const skillList = availableSkills
      .map(
        (skill, idx) =>
          `${idx + 1}. ID: "${skill.name}"\n   Conditions:\n${(skill.conditions ?? []).map((c) => `   - ${c}`).join("\n")}`,
      )
      .join("\n\n");

    const systemPrompt =
      `You are a classifier that decides whether a user message triggers a behavioral skill.\n` +
      `You will be given a list of skills (each with conditions) and a user message.\n` +
      `Return a JSON object: { "triggered": true|false, "skillId": "<name>" | null }\n` +
      `Only trigger a skill if its conditions are CLEARLY met by the message.\n` +
      `If multiple skills match, return only the first (highest priority) one.\n` +
      `Output ONLY valid JSON — no prose, no code fences.`;

    const userPrompt =
      `SKILLS:\n${skillList}\n\n` +
      `USER MESSAGE:\n"${userMessage}"\n\n` +
      `Which skill (if any) is triggered? Return JSON.`;

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
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```(?:json)?/, "")
          .replace(/```$/, "")
          .trim();
      }

      const parsed = JSON.parse(jsonStr) as {
        triggered: boolean;
        skillId: string | null;
      };

      if (parsed.triggered && parsed.skillId) {
        const triggered = availableSkills.find(
          (s) => s.name === parsed.skillId,
        );
        if (triggered) {
          log.engine.info(`[Skills] ⚡ Triggered: "${triggered.name}"`);
          return triggered;
        }
      }

      return null;
    } catch (err) {
      log.engine.warn(
        `[Skills] Batch evaluation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}
