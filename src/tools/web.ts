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
    description:
      "Fetch and extract text content from a specific URL. Use this AFTER google_search " +
      "to read a page you found, or when you already know the exact URL. " +
      "Returns cleaned text (no HTML). Good for: articles, documentation, API docs, data pages. " +
      "Automatically escalates to a stealth browser when sites block simple HTTP requests. " +
      "NOT for interactive sites (forms, SPAs, login-gated pages) — use the browser tool for those. " +
      "Limit: 25KB text.",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "Full URL to fetch (e.g. https://example.com/article). Must start with http:// or https://",
        },
      },
      required: ["url"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    let url = args["url"] as string;
    if (!url) throw new Error("URL is required");

    // Validate URL
    try {
      const parsedUrl = new URL(url);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        throw new Error("Only http:// and https:// URLs are supported");
      }
      url = parsedUrl.toString();
    } catch {
      throw new Error(`Invalid URL: ${url}`);
    }

    try {
      const result = await webFetch(url, { maxLength: 25000, timeout: 30000 });

      if (result.blocked) {
        return (
          `BLOCKED: ${url} — bot/CAPTCHA protection detected (${result.blockType || "unknown"}).\n` +
          `The smart fetch layer tried HTTP and stealth browser but was blocked.\n` +
          `Escalation path:\n` +
          `1. scrapling_fetch(url, mode='stealth') — external anti-bot scraping\n` +
          `2. scrapling_fetch(url, mode='dynamic') — full browser with anti-detection\n` +
          `3. computer_use(action='open_url') — real desktop browser, undetectable`
        );
      }

      const via = result.source !== "fetch" ? ` [via ${result.source}]` : "";
      return `### ${result.title}${via}\n\n${result.url}\n\n${result.text}`;
    } catch (error) {
      if (error instanceof Error) {
        return `Error fetching ${url}: ${error.message}`;
      }
      return `Error fetching ${url}: Unknown error`;
    }
  },
};
