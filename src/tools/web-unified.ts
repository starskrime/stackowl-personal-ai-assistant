/**
 * StackOwl — Unified Web Tool
 *
 * Dispatches to pluggable search/fetch/interact implementations.
 * Reduces the LLM-visible tool count by exposing a single "web" tool
 * with an `action` discriminator instead of multiple separate tools.
 *
 * Supported actions:
 *   search   — web search (e.g. DuckDuckGo, Google)
 *   fetch    — retrieve and extract text from a URL
 *   interact — browser automation (e.g. CamoFox, Playwright)
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

export interface WebUnifiedDeps {
  search?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  fetch?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  interact?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}

export function createWebUnifiedTool(deps: WebUnifiedDeps): ToolImplementation {
  return {
    definition: {
      name: "web",
      description:
        "Unified web tool. Use action:search to search the web, action:fetch to retrieve a URL, " +
        "action:interact to control a browser. " +
        "Example: {action:'search', query:'typescript 5.5'} or {action:'fetch', url:'https://example.com'} " +
        "or {action:'interact', url:'https://example.com', selector:'#main'}.",
      parameters: {
        type: "object",
        properties: {
          action: {
            type: "string",
            description: "One of: search, fetch, interact",
            enum: ["search", "fetch", "interact"],
          },
          query: {
            type: "string",
            description: "Search query (for action:search)",
          },
          url: {
            type: "string",
            description:
              "URL to fetch or navigate to (for action:fetch, action:interact)",
          },
          selector: {
            type: "string",
            description:
              "CSS selector for interaction (for action:interact)",
          },
          js: {
            type: "string",
            description:
              "JavaScript to execute in browser (for action:interact)",
          },
        },
        required: ["action"],
      },
      capabilities: ["web_search", "web_fetch", "web_interact"],
      executionPolicy: { timeoutMs: 30_000, maxRetries: 1, retryDelayMs: 1_500 },
    },
    category: "network",
    execute: async (args, context) => {
      const action = args["action"] as string;
      const impl = deps[action as keyof WebUnifiedDeps];

      if (!impl) {
        return JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "ACTION_NOT_SUPPORTED",
            message: `Web action '${action}' is not configured.`,
            suggestion: `Available actions: ${Object.keys(deps).join(", ") || "none"}`,
          },
        });
      }

      return impl(args, context);
    },
  };
}
