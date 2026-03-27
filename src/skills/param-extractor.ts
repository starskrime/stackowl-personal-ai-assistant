/**
 * StackOwl — Skill Parameter Extractor
 *
 * Extracts typed parameters from a user's natural language message
 * for structured skill execution. Single LLM call per extraction.
 */

import { log } from "../logger.js";
import type { ModelProvider } from "../providers/base.js";
import type { SkillParameter } from "./types.js";

export class SkillParamExtractor {
  constructor(private provider: ModelProvider) {}

  /**
   * Extract parameter values from a user message given parameter definitions.
   * Returns a map of parameter name → extracted value with defaults applied.
   */
  async extract(
    userMessage: string,
    paramDefs: Record<string, SkillParameter>,
  ): Promise<Record<string, unknown>> {
    const paramEntries = Object.entries(paramDefs);
    if (paramEntries.length === 0) return {};

    // Build parameter description for the LLM
    const paramDesc = paramEntries
      .map(([name, def]) => {
        const req = def.required !== false ? "required" : "optional";
        const defVal =
          def.default !== undefined
            ? `, default: ${JSON.stringify(def.default)}`
            : "";
        return `- "${name}" (${def.type}, ${req}${defVal}): ${def.description}`;
      })
      .join("\n");

    const prompt =
      `Extract parameter values from this user message.\n\n` +
      `User message: "${userMessage}"\n\n` +
      `Parameters to extract:\n${paramDesc}\n\n` +
      `Return a JSON object with the parameter names as keys and extracted values.\n` +
      `Use the exact types specified (string, number, boolean).\n` +
      `If a parameter is not mentioned in the message, omit it.\n` +
      `Return ONLY the JSON object, no explanation.`;

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content:
              "You are a parameter extraction assistant. Output only valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { maxTokens: 256 },
      );

      const extracted = this.parseJson(response.content);

      // Apply defaults and type coercion
      const result: Record<string, unknown> = {};
      for (const [name, def] of paramEntries) {
        if (name in extracted) {
          result[name] = this.coerce(extracted[name], def.type);
        } else if (def.default !== undefined) {
          result[name] = def.default;
        } else if (def.required !== false) {
          throw new Error(
            `Required parameter "${name}" not found in message: "${userMessage}"`,
          );
        }
      }

      return result;
    } catch (err) {
      // On LLM failure, apply defaults only
      log.engine.warn(
        `[ParamExtractor] LLM extraction failed, using defaults: ` +
          `${err instanceof Error ? err.message : err}`,
      );
      const result: Record<string, unknown> = {};
      for (const [name, def] of paramEntries) {
        if (def.default !== undefined) {
          result[name] = def.default;
        } else if (def.required !== false) {
          throw new Error(
            `Required parameter "${name}" could not be extracted and has no default`,
          );
        }
      }
      return result;
    }
  }

  private parseJson(text: string): Record<string, unknown> {
    // Try direct parse
    try {
      return JSON.parse(text);
    } catch {
      /* continue */
    }

    // Extract JSON from markdown code block
    const match = text.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (match) {
      try {
        return JSON.parse(match[1].trim());
      } catch {
        /* continue */
      }
    }

    // Try finding JSON object in text
    const braceMatch = text.match(/\{[\s\S]*\}/);
    if (braceMatch) {
      try {
        return JSON.parse(braceMatch[0]);
      } catch {
        /* continue */
      }
    }

    return {};
  }

  private coerce(value: unknown, type: string): unknown {
    if (value === null || value === undefined) return value;

    switch (type) {
      case "number": {
        const n = Number(value);
        return isNaN(n) ? value : n;
      }
      case "boolean": {
        if (typeof value === "string") {
          return value.toLowerCase() === "true" || value === "1";
        }
        return Boolean(value);
      }
      case "string":
        return String(value);
      default:
        return value;
    }
  }
}
