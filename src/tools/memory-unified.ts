/**
 * StackOwl — Unified Memory Tool
 *
 * Dispatches to pluggable search/store/get implementations.
 * Reduces the LLM-visible tool count by exposing a single "memory" tool
 * with an `action` discriminator instead of multiple separate tools
 * (recall_memory, memory_search, remember, pellet_recall, etc.).
 *
 * Supported actions:
 *   search  — semantic search across all memory stores
 *   store   — persist a new fact, preference, or learning
 *   get     — retrieve a specific memory entry by ID
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

export interface MemoryUnifiedDeps {
  search?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  store?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  get?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createMemoryUnifiedTool(deps: MemoryUnifiedDeps): ToolImplementation {
  return {
    definition: {
      name: "memory",
      description:
        "Unified memory tool. Use action:search to find memories, action:store to save a memory, " +
        "action:get to retrieve by ID. " +
        "Example: {action:'search', query:'last project discussion'} or {action:'store', content:'User prefers MP4 format'} " +
        "or {action:'get', id:'mem_abc123'}.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: search, store, get",
            enum: ["search", "store", "get"],
          },
          query: {
            type: "string",
            description: "Search query (for action:search)",
          },
          content: {
            type: "string",
            description: "Content to store (for action:store)",
          },
          id: {
            type: "string",
            description: "Memory ID to retrieve (for action:get)",
          },
          tags: {
            type: "string",
            description: "Comma-separated tags (for action:store)",
          },
        },
        required: ["action"],
      },
      capabilities: ["memory_search", "memory_store", "memory_get"],
    },
    category: "memory" as any,
    execute: async (args, context) => {
      const action = args["action"] as string;
      const impl = deps[action as keyof MemoryUnifiedDeps];

      if (!impl) {
        return JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "ACTION_NOT_SUPPORTED",
            message: `Memory action '${action}' is not configured.`,
            suggestion: `Available actions: search, store, get`,
          },
        });
      }

      return impl(args, context);
    },
  };
}
