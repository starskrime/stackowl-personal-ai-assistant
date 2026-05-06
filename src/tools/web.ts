/**
 * StackOwl — Web Fetch Tool
 *
 * Fetches and cleans text from any URL. Uses the smart fetch layer
 * which escalates scrapling → camofox (Obscura reserved) when blocked.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { webFetchEnvelope } from "../browser/smart-fetch.js";

export const WebFetchTool: ToolImplementation = {
  definition: {
    name: "web_fetch",
    description:
      "Fetch and extract content from a URL. Tries scrapling, then camofox (Obscura is reserved). Returns a structured envelope with the page text or a typed error code. Use hint:'anti-bot' if you already have evidence the site uses Cloudflare/PerimeterX/Akamai — this skips the lightweight tier. On ALL_TIERS_UNAVAILABLE or BLOCKED_BY_ANTI_BOT, escalate to live_browser.",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description:
            "Full URL to fetch (e.g. https://example.com/article). Must start with http:// or https://",
        },
        hint: {
          type: "string",
          enum: ["anti-bot"],
          description: "Skip Tier 1 (scrapling) and start with camofox.",
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
      const envelope = await webFetchEnvelope(url);
      return serializeWebToolResult(envelope);
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

