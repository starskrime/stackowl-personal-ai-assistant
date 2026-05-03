/**
 * StackOwl — Pellet Generator
 *
 * Uses IntelligenceRouter to "digest" raw transcript/conversation data
 * into a highly structured knowledge artifact (Pellet).
 *
 * The constructor accepts a duck-typed GenerationRouter so it can be
 * trivially mocked in tests. Production callers wire up a real router
 * via makeProviderRouter(provider) or by passing an IntelligenceRouter
 * adapter directly.
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { Pellet } from "./store.js";
import { log } from "../logger.js";

// ─── Duck-typed interface ─────────────────────────────────────────
// Accepts both the real IntelligenceRouter and simple test mocks
// that return the generated text directly.

export interface GenerationRouter {
  resolve(tier: string, prompt: string): Promise<string>;
}

/**
 * Convenience adapter: wraps a plain ModelProvider into a GenerationRouter.
 * Used by parliament/orchestrator callers that don't yet have a full
 * IntelligenceRouter in scope.
 */
export function makeProviderRouter(provider: ModelProvider): GenerationRouter {
  return {
    async resolve(_tier: string, prompt: string): Promise<string> {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0.3 },
      );
      return response.content;
    },
  };
}

// ─── PelletGenerator ─────────────────────────────────────────────

export class PelletGenerator {
  constructor(private router: GenerationRouter) {}

  /**
   * Generate a pellet from unstructured source material using IntelligenceRouter.
   * Returns null if sourceMaterial is empty or if the LLM returns unparseable JSON.
   */
  async generate(
    sourceMaterial: string,
    sourceName: string,
    opts?: { provenance?: string[] },
  ): Promise<Pellet | null> {
    if (!sourceMaterial.trim()) return null;

    const prompt =
      `You are digesting a conversation or research output to create a "Pellet" — a compressed, highly structured knowledge artifact.\n\n` +
      `Source: ${sourceName}\n` +
      `Material:\n${sourceMaterial}\n\n` +
      `Task: Generate the contents of the Pellet. Your response MUST be valid JSON matching this schema:\n` +
      `{\n` +
      `  "slug": "a-kebab-case-short-id",\n` +
      `  "title": "A clear, descriptive title",\n` +
      `  "tags": ["architectural", "decision-record", "database"],\n` +
      `  "owlsInvolved": ["Noctua", "Archimedes"],\n` +
      `  "content": "Formatted Markdown with ## Key Insight, ## Evidence/Arguments, ## Final Verdict"\n` +
      `}\n\n` +
      `Output ONLY the JSON object. Do not wrap it in \`\`\`json blocks.`;

    let jsonStr: string;
    try {
      jsonStr = await this.router.resolve("synthesis", prompt);
    } catch (err) {
      log.engine.warn(
        `[PelletGenerator] router.resolve failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }

    // Strip optional code-block wrapping
    jsonStr = jsonStr.trim();
    if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr.replace(/^```(?:json)?/, "").replace(/```$/, "").trim();
    }

    let parsed: any;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      log.engine.warn(
        "[PelletGenerator] Failed to parse LLM output as JSON — skipping pellet",
      );
      return null;
    }

    const id =
      (parsed.slug as string | undefined) || `pellet-${uuidv4().substring(0, 8)}`;

    return {
      id,
      title: (parsed.title as string | undefined) || id,
      generatedAt: new Date().toISOString(),
      source: sourceName,
      owls: Array.isArray(parsed.owlsInvolved)
        ? (parsed.owlsInvolved as string[])
        : [],
      tags: Array.isArray(parsed.tags) ? (parsed.tags as string[]) : [],
      version: 1,
      content: (parsed.content as string | undefined) || "",
      successCount: 0,
      failureCount: 0,
      provenance: opts?.provenance ?? [],
    };
  }
}
