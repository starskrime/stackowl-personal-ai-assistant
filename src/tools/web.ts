/**
 * StackOwl — Web Crawl Tool
 *
 * Fetches and cleans text from any URL. Uses the smart fetch layer
 * which automatically escalates from HTTP → stealth browser when blocked.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { webFetch } from "../browser/smart-fetch.js";

export const WebCrawlTool: ToolImplementation = {
  definition: {
    name: "web_crawl",
    deprecated: true,
    description:
      "Fetch and extract text content from a specific URL. Use this AFTER duckduckgo_search " +
      "to read a page you found, or when you already know the exact URL. " +
      "Returns cleaned text (no HTML). Good for: articles, documentation, API docs, data pages. " +
      "Automatically escalates: HTTP → stealth Chromium → CamoFox (Firefox, anti-detection). " +
      "For interactive sites (forms, SPAs, login flows, clicking buttons) use the camofox tool instead. " +
      "Limit: 25KB text.",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description:
            "Full URL to fetch (e.g. https://example.com/article). Must start with http:// or https://",
        },
      },
      required: ["url"],
    },
  },

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const { serializeWebToolResult } = await import("../browser/envelope.js");
    let url = args["url"] as string;
    if (!url) {
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: "URL is required", attemptedTiers: [] },
      });
    }
    try {
      const parsedUrl = new URL(url);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        return serializeWebToolResult({
          success: false,
          error: { code: "INVALID_URL", message: "Only http(s) URLs are supported", attemptedTiers: [] },
        });
      }
      url = parsedUrl.toString();
    } catch {
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: `Invalid URL: ${url}`, attemptedTiers: [] },
      });
    }

    try {
      const result = await webFetch(url, { maxLength: 25000, timeout: 30000 });
      if (result.blocked) {
        return serializeWebToolResult({
          success: false,
          error: {
            code: "ALL_TIERS_UNAVAILABLE",
            message: `${url} — bot/CAPTCHA protection (${result.blockType ?? "unknown"})`,
            attemptedTiers: [{ tier: 1, name: "http", durationMs: 0, outcome: "blocked", blockedReason: "other" }],
          },
        });
      }
      return serializeWebToolResult({
        success: true,
        data: { kind: "page", url: result.url, title: result.title, content: result.text },
      });
    } catch (error) {
      return serializeWebToolResult({
        success: false,
        error: {
          code: "INTERNAL_ERROR",
          message: error instanceof Error ? error.message : String(error),
          attemptedTiers: [],
        },
      });
    }
  },
};
