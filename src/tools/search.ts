/**
 * StackOwl — Web Search Tool (DDG HTML backend).
 *
 * Returns a JSON-serialized WebToolResult envelope on every path
 * (success / no-results / HTTP error / timeout / blocked).
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { serializeWebToolResult } from "../browser/envelope.js";
import { searchEnvelope } from "../browser/smart-search.js";
import { log } from "../logger.js";

export const WebSearchTool: ToolImplementation = {
  definition: {
    name: "web_search",
    description:
      "Search the web. Returns titles, URLs, and snippets. " +
      "Use this as your FIRST step when you need current/real-time information " +
      "(news, prices, flight status, weather, etc.) or to find URLs to read with web_fetch. " +
      "Do NOT search for the same query twice — rephrase or call web_fetch on a specific URL instead. " +
      "If results return a BLOCKED_BY_ANTI_BOT envelope, escalate to live_browser.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "A specific, targeted search query.",
        },
        num: {
          type: "number",
          description: "Number of results to return (default 8, max 15)",
        },
      },
      required: ["query"],
    },
    capabilities: ["web_search", "internet_query"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const query = (args["query"] as string)?.trim();
    if (!query) throw new Error("Search query is required");

    const num = Math.min(Number(args["num"] ?? 8), 15);

    // 1. ENTRY
    log.tool.debug("search.execute: entry", { query, maxResults: num });

    try {
      // 3. STEP — HTTP request sent
      log.tool.debug("search.execute: request sent", { query, num, backend: "smart-search" });

      const result = await searchEnvelope(query, num, {
        tavilyApiKey: context.tavilyApiKey,
        camofox: context.camofox,
        puppeteer: context.puppeteer,
        classifier: context.classifier,
      });

      // 3. STEP — results parsed
      const resultCount = result.success && (result as any).results ? (result as any).results.length : 0;
      log.tool.debug("search.execute: results parsed", { resultCount, success: result.success });

      const serialized = serializeWebToolResult(result);
      // 4. EXIT
      log.tool.debug("search.execute: exit", { success: result.success, resultCount, resultLen: serialized.length });
      return serialized;
    } catch (error) {
      // ERROR
      log.tool.error("search.execute: request failed", error instanceof Error ? error : new Error(String(error)), { query });
      throw error;
    }
  },
};

