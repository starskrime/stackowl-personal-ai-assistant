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
    description: [
      "Search your accumulated knowledge base (pellets) for relevant stored knowledge.",
      "",
      "WHEN TO USE:",
      "  - Before answering questions about past decisions, learnings, or user preferences",
      "  - When you sense a topic might have been covered before",
      "  - When starting a task that could benefit from prior context",
      "  - When web_crawl or research returns something you think you've seen before",
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
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const store = context.engineContext?.pelletStore;
    if (!store) {
      return "Knowledge base (pellet store) is not available in this context.";
    }

    const action = args["action"] as string;
    const query = args["query"] as string | undefined;
    const id = args["id"] as string | undefined;
    const limit = Math.min(Number(args["limit"] ?? 5), 20);

    try {
      switch (action) {
        // ── search ───────────────────────────────────────────────
        case "search": {
          if (!query?.trim()) {
            return "Error: `query` is required for action=search.";
          }
          const results = await store.searchWithGraph(query.trim(), limit);
          if (results.length === 0) {
            return `No relevant pellets found for: "${query}"\n\nTry a broader search term or use action=list_recent.`;
          }
          return formatPellets(results, `Knowledge base results for: "${query}"`);
        }

        // ── get ──────────────────────────────────────────────────
        case "get": {
          if (!id?.trim()) return "Error: `id` is required for action=get.";
          const pellet = await store.get(id.trim());
          if (!pellet) return `Pellet "${id}" not found.`;
          return formatPellet(pellet, true);
        }

        // ── get_related ──────────────────────────────────────────
        case "get_related": {
          if (!id?.trim()) return "Error: `id` is required for action=get_related.";
          // Use searchWithGraph with the pellet's title as the query
          const seed = await store.get(id.trim());
          if (!seed) return `Pellet "${id}" not found.`;

          const related = await store.searchWithGraph(
            `${seed.title} ${seed.tags.join(" ")}`,
            limit + 1,
          );
          const filtered = related.filter((p) => p.id !== id).slice(0, limit);

          if (filtered.length === 0) {
            return `No related pellets found for "${seed.title}".`;
          }
          return formatPellets(
            filtered,
            `Related to: "${seed.title}" [${seed.tags.join(", ")}]`,
          );
        }

        // ── list_recent ──────────────────────────────────────────
        case "list_recent": {
          const all = await store.listAll();
          const recent = all.slice(0, limit);
          if (recent.length === 0) return "No pellets in knowledge base yet.";
          return formatPellets(recent, `${recent.length} most recent pellets`);
        }

        // ── list_tags ────────────────────────────────────────────
        case "list_tags": {
          const all = await store.listAll();
          const tagCount = new Map<string, number>();
          for (const p of all) {
            for (const tag of p.tags) {
              tagCount.set(tag, (tagCount.get(tag) ?? 0) + 1);
            }
          }
          if (tagCount.size === 0) return "No tags found in knowledge base.";

          const sorted = [...tagCount.entries()].sort((a, b) => b[1] - a[1]);
          const lines = sorted.map(([tag, count]) => `  ${tag} (${count})`);
          return `Tags in knowledge base (${sorted.length} total):\n${lines.join("\n")}`;
        }

        default:
          return `Unknown action "${action}". Use: search, get, get_related, list_recent, list_tags`;
      }
    } catch (err) {
      return `pellet_recall error: ${err instanceof Error ? err.message : String(err)}`;
    }
  },
};

// ─── Formatting helpers ──────────────────────────────────────────

function formatPellet(
  p: import("../pellets/store.js").Pellet,
  full = false,
): string {
  const header = `### ${p.title}`;
  const meta = [
    `ID: ${p.id}`,
    p.tags.length > 0 ? `Tags: ${p.tags.join(", ")}` : null,
    `Source: ${p.source}`,
    `Created: ${p.generatedAt.slice(0, 10)}`,
    p.version > 1 ? `Version: ${p.version}` : null,
  ]
    .filter(Boolean)
    .join(" | ");

  const body = full
    ? p.content
    : p.content.slice(0, 500) + (p.content.length > 500 ? "\n...[truncated — use action=get for full content]" : "");

  return `${header}\n${meta}\n\n${body}`;
}

function formatPellets(
  pellets: import("../pellets/store.js").Pellet[],
  heading: string,
): string {
  const items = pellets.map((p) => formatPellet(p, false));
  return `## ${heading}\n\n${items.join("\n\n---\n\n")}`;
}
