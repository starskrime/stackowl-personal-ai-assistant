/**
 * StackOwl — Pellet Recall Tool
 *
 * Gives the LLM ACTIVE access to the knowledge base.
 *
 * Before: pellets were passively injected (top-3 keyword hits at prompt build time)
 * After:  the LLM can proactively search, browse, and traverse its accumulated knowledge
 *
 * The description explicitly instructs the model to check memory BEFORE answering,
 * turning the knowledge base from a passive archive into a first-class reasoning tool.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

export const PelletRecallTool: ToolImplementation = {
  definition: {
    name: "pellet_recall",
    deprecated: true,
    description: [
      "Search your accumulated knowledge base (pellets) for relevant stored knowledge.",
      "",
      "WHEN TO USE:",
      "  - Before answering questions about past decisions, learnings, or user preferences",
      "  - When you sense a topic might have been covered before",
      "  - When starting a task that could benefit from prior context",
      "  - When web_fetch or research returns something you think you've seen before",
      "",
      "Actions:",
      "  search       — semantic search across all pellets (uses embeddings, not keywords)",
      "  get          — fetch a specific pellet by ID",
      "  get_related  — find pellets connected in the knowledge graph (graph-aware)",
      "  list_recent  — most recently created pellets",
      "  list_tags    — all unique tags in the knowledge base",
    ].join("\n"),
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["search", "get", "get_related", "list_recent", "list_tags"],
          description: "Action to perform",
        },
        query: {
          type: "string",
          description:
            "Search query (for action=search). Natural language — semantic search understands concepts, not just keywords.",
        },
        id: {
          type: "string",
          description: "Pellet ID (for action=get and action=get_related)",
        },
        limit: {
          type: "number",
          description: "Max results to return (default: 5, max: 20)",
        },
      },
      required: ["action"],
    },
    capabilities: ["memory_search", "knowledge_retrieve"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },

  async execute(
    _args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    return "The pellet knowledge base has been migrated to the new memory system. Use the `remember` tool to store facts and rely on context injection for retrieved memories.";
  },
};
