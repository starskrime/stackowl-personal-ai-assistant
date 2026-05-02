/**
 * StackOwl — Unified Memory Tool
 *
 * Dispatches to pluggable search/store/get/write/invalidate implementations.
 * Reduces the LLM-visible tool count by exposing a single "memory" tool
 * with an `action` discriminator instead of multiple separate tools
 * (recall_memory, memory_search, remember, pellet_recall, etc.).
 *
 * Supported actions:
 *   search     — semantic search across all memory stores
 *   store      — persist a new fact, preference, or learning
 *   get        — retrieve a specific memory entry by ID
 *   write      — direct fact write with optional category/confidence
 *   invalidate — mark matching facts as invalidated by keyword
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

export interface MemoryUnifiedDeps {
  search?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  store?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  get?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  write?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  invalidate?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createMemoryUnifiedTool(deps: MemoryUnifiedDeps): ToolImplementation {
  return {
    definition: {
      name: "memory",
      description:
        "Unified memory tool. Use action:search to find memories, action:store to save a memory, " +
        "action:get to retrieve by ID, action:write to directly write a fact with category/confidence, " +
        "action:invalidate to mark matching facts as invalidated by keyword. " +
        "Example: {action:'search', query:'last project discussion'} or {action:'store', content:'User prefers MP4 format'} " +
        "or {action:'get', id:'mem_abc123'} or {action:'write', content:'User prefers dark mode', category:'prefs', confidence:0.9} " +
        "or {action:'invalidate', query:'dark mode'}.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: search, store, get, write, invalidate",
            enum: ["search", "store", "get", "write", "invalidate"],
          },
          query: {
            type: "string",
            description: "Search query (for action:search or action:invalidate)",
          },
          content: {
            type: "string",
            description: "Content to store (for action:store or action:write)",
          },
          id: {
            type: "string",
            description: "Memory ID to retrieve (for action:get)",
          },
          tags: {
            type: "string",
            description: "Comma-separated tags (for action:store)",
          },
          category: {
            type: "string",
            description: "Fact category (for action:write)",
          },
          confidence: {
            type: "number",
            description: "Confidence score 0-1 (for action:write, default 0.8)",
          },
        },
        required: ["action"],
      },
      capabilities: ["memory_search", "memory_store", "memory_get", "memory_write", "memory_invalidate"],
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
            suggestion: `Available actions: search, store, get, write, invalidate`,
          },
        });
      }

      return impl(args, context);
    },
  };
}
