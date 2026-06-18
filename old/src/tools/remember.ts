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
 * Writes via UnifiedMemory.remember() with confidence:0.9, source:"inferred".
 * Bypasses the post-session extraction pipeline so the fact is available
 * immediately in the next turn.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export class RememberTool implements ToolImplementation {
  definition = {
    name: "remember",
    deprecated: true,
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

    const category = ((args.category as string) ?? "skill");

    const kindMap: Record<string, string> = {
      skill: "procedural",
      habit: "procedural",
      preference: "semantic",
      personal: "semantic",
      project_detail: "semantic",
      context: "semantic",
      goal: "semantic",
    };
    const kind = kindMap[category] ?? "semantic";

    log.memory.debug("remember.execute: entry", { category, kind, contentLen: content.length });

    const unifiedMemory = (context.engineContext as any)?.unifiedMemory;

    if (unifiedMemory) {
      try {
        const userId = context.engineContext?.userId ?? "default";
        const owlName = context.engineContext?.owl?.persona?.name ?? "default";

        await unifiedMemory.remember({
          content,
          kind,
          domain: category,
          scope: "user",
          source: "inferred",
          confidence: 0.9,
          userId,
          owlName,
        });

        log.memory.info("remember.execute: stored", { category, kind, contentLen: content.length });
        return `Remembered (${category}): "${content.slice(0, 80)}${content.length > 80 ? "..." : ""}"`;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log.memory.error("remember.execute: UnifiedMemory.remember failed", err, { category, kind });
        return `Failed to store memory: ${msg}`;
      }
    }

    // Graceful degradation — no memory store available
    log.memory.warn("remember.execute: no unifiedMemory in context — memory not persisted");
    return `Noted: "${content.slice(0, 80)}" — (memory store not available, won't persist across sessions)`;
  }
}
