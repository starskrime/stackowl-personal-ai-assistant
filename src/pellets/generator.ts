/**
 * StackOwl — Pellet Generator
 *
 * Uses the Owl Engine to "digest" raw transcript/conversation data
 * into a highly structured knowledge artifact (Pellet).
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import { OwlEngine } from "../engine/runtime.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { Pellet } from "./store.js";

export class PelletGenerator {
  private engine: OwlEngine;

  constructor() {
    this.engine = new OwlEngine();
  }

  /**
   * Generate a pellet from unstructured data (e.g., a Parliament session transcript
   * or a long conversation).
   */
  async generate(
    sourceMaterial: string,
    sourceName: string,
    context: {
      provider: ModelProvider;
      owl: OwlInstance;
      config: StackOwlConfig;
    },
  ): Promise<Pellet> {
    const { provider, owl, config } = context;

    console.log(
      `[PelletGenerator] 📦 ${owl.persona.name} is digesting knowledge from: ${sourceName}...`,
    );

    const prompt =
      `You are digesting a conversation or research output to create a "Pellet" — a compressed, highly structured knowledge artifact.\n\n` +
      `Source Material:\n${sourceMaterial}\n\n` +
      `Task: Generate the contents of the Pellet. Your response MUST be valid JSON matching this schema:\n` +
      `{\n` +
      `  "slug": "a-kebab-case-short-id",\n` +
      `  "title": "A clear, descriptive title",\n` +
      `  "tags": ["architectural", "decision-record", "database"],\n` +
      `  "owlsInvolved": ["Noctua", "Archimedes"],\n` +
      `  "content": "A beautifully formatted Markdown string containing:\n` +
      `     - ## Key Insight (1-2 sentences)\n` +
      `     - ## Evidence/Arguments (bullet points)\n` +
      `     - ## Final Verdict/Decision (if applicable)\n` +
      `     - ## Context (briefly, what led to this)"\n` +
      `}\n\n` +
      `Output ONLY the JSON object. Do not wrap it in \`\`\`json blocks.`;

    const response = await this.engine.run(prompt, {
      provider,
      owl,
      sessionHistory: [],
      config,
    });

    // Parse JSON (attempt to clean if wrapped in code blocks)
    let jsonStr = response.content.trim();
    if (jsonStr.startsWith("```json")) {
      jsonStr = jsonStr
        .replace(/^```json/, "")
        .replace(/```$/, "")
        .trim();
    } else if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();
    }

    let parsed: any;
    try {
      parsed = JSON.parse(jsonStr);
    } catch (error) {
      console.error(
        "[PelletGenerator] Failed to parse LLM output as JSON. Falling back to dumb string logic.",
      );
      // Fallback strategy if the LLM didn't return valid JSON
      parsed = {
        slug: `pellet-${uuidv4().substring(0, 8)}`,
        title: "Auto-generated Pellet",
        tags: ["auto-generated"],
        owlsInvolved: [owl.persona.name],
        content: response.content,
      };
    }

    const id = parsed.slug || `pellet-${uuidv4().substring(0, 8)}`;

    return {
      id,
      title: parsed.title || id,
      generatedAt: new Date().toISOString(),
      source: sourceName,
      owls: Array.isArray(parsed.owlsInvolved)
        ? parsed.owlsInvolved
        : [owl.persona.name],
      tags: Array.isArray(parsed.tags) ? parsed.tags : [],
      version: 1,
      content: parsed.content || "Content generation failed.",
    };
  }
}
