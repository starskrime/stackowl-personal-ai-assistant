import type { ToolImplementation, ToolContext } from "./registry.js";

/**
 * Recall Memory Tool — searches across pellets, sessions, and memory
 * to reconstruct conversational threads.
 */
export class RecallMemoryTool implements ToolImplementation {
  definition = {
    name: "recall_memory",
    description:
      "Search your memory for past conversations, knowledge, and insights. " +
      'Use when the user says "remember", "we discussed", "what did we talk about", ' +
      "or wants to recall a previous topic. Returns a narrative thread reconstruction.",
    parameters: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description:
            "What to recall — a topic, keyword, or natural language description",
        },
        scope: {
          type: "string",
          description:
            'Where to search: "all" (default), "pellets" (knowledge only), "sessions" (conversations only)',
        },
      },
      required: ["query"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const query = args.query as string;
    if (!query) return "Error: query is required.";

    const scope = (args.scope as string) || "all";
    const memorySearcher = context.engineContext?.memorySearcher;

    if (!memorySearcher) {
      return "Memory search is not available. The MemorySearcher module is not initialized.";
    }

    try {
      const thread = await memorySearcher.recall(
        query,
        scope as "all" | "pellets" | "sessions",
      );

      if (thread.timeline.length === 0) {
        return `No memories found matching "${query}". Try different keywords.`;
      }

      let result = `**Memory Thread: "${query}"**\n\n`;
      result += `${thread.narrative}\n\n`;
      result += `---\n`;
      result += `Sources: ${thread.relatedPellets.length} pellets, ${thread.relatedSessions.length} sessions\n`;

      // Include top entries for reference
      const topEntries = thread.timeline.slice(0, 5);
      if (topEntries.length > 0) {
        result += `\n**Key References:**\n`;
        for (const entry of topEntries) {
          const date = new Date(entry.timestamp).toLocaleDateString();
          result += `- [${date}] (${entry.source}): ${entry.excerpt.slice(0, 150)}\n`;
        }
      }

      return result;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Memory recall failed: ${msg}`;
    }
  }
}
