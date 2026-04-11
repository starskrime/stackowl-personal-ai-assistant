/**
 * StackOwl — Remember Tool
 *
 * Gives the model a first-class, explicit write path into long-term memory.
 * Inspired by mem0's add() API: the caller doesn't think about which store
 * to write to — just "remember this".
 *
 * The model is instructed to call this after every successful action
 * ("I just solved X using Y — I'll remember this approach") and when the
 * user states something important ("User prefers MP4 format").
 *
 * Writes directly to FactStore with confidence:0.9, source:"inferred",
 * TTL 365 days. Bypasses the post-session extraction pipeline so the fact
 * is available immediately in the next turn.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import type { FactCategory } from "../memory/fact-store.js";
import { log } from "../logger.js";

export class RememberTool implements ToolImplementation {
  definition = {
    name: "remember",
    description:
      "Permanently store a fact, learned approach, or user preference in long-term memory. " +
      "Call this after completing a task successfully, when the user shares a preference, " +
      "or when you discover an approach that works. " +
      "Memory stored here is available in ALL future conversations.",
    parameters: {
      type: "object" as const,
      properties: {
        content: {
          type: "string",
          description:
            "What to remember. Be specific and actionable. Good examples: " +
            '"yt-dlp --output %(title)s.mp4 works for Instagram reels", ' +
            '"User prefers concise bullet-point answers over long paragraphs", ' +
            '"For this project, use TypeScript strict mode"',
        },
        category: {
          type: "string",
          enum: [
            "skill",
            "preference",
            "project_detail",
            "personal",
            "context",
            "goal",
            "habit",
          ],
          description:
            "Category: skill (how to do something), preference (user likes/dislikes), " +
            "project_detail (about the current project), personal (about the user), " +
            "context (situational facts), goal (user objectives), habit (recurring patterns).",
        },
      },
      required: ["content"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const content = args.content as string;
    if (!content?.trim()) return "Error: content is required.";

    const category = ((args.category as string) ?? "skill") as FactCategory;
    const factStore = context.engineContext?.factStore;

    if (!factStore) {
      // Graceful degradation — log but don't fail the model's tool call
      log.memory.warn("[RememberTool] FactStore not available — memory not persisted");
      return `Noted: "${content.slice(0, 80)}" — (memory store not available, won't persist across sessions)`;
    }

    try {
      const userId = (context.engineContext as any)?.userId ?? "default";

      // Use addWithEmbedding so the fact is immediately searchable via semantic search.
      // The provider is available on EngineContext and used for embed() calls.
      const provider = context.engineContext?.provider;
      await factStore.addWithEmbedding(
        {
          userId,
          fact: content,
          category,
          confidence: 0.9,
          source: "inferred",
          expiresAt: new Date(
            Date.now() + 365 * 24 * 60 * 60 * 1000, // 1 year
          ).toISOString(),
        },
        provider,
      );

      // Also write to owl_learnings for cross-owl knowledge sharing (Phase 4)
      const db = (context.engineContext as any)?.db;
      const owlName = (context.engineContext as any)?.owl?.persona?.name ?? "default";
      if (db && (category === "skill" || category === "habit")) {
        db.owlLearnings.add(owlName, content, "skill", undefined, 0.85);
      }

      log.memory.info(`[RememberTool] Stored (${category}): "${content.slice(0, 80)}"`);
      return `Remembered (${category}): "${content.slice(0, 80)}${content.length > 80 ? "..." : ""}"`;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.memory.warn(`[RememberTool] Failed to store: ${msg}`);
      return `Failed to store memory: ${msg}`;
    }
  }
}
