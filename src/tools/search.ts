/**
 * StackOwl — Web Search Tool (DDG HTML backend).
 *
 * Returns a JSON-serialized WebToolResult envelope on every path
 * (success / no-results / HTTP error / timeout / blocked).
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import {
  serializeWebToolResult,
  type WebToolResult,
  type WebToolErrorCode,
} from "../browser/envelope.js";

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

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

    const limit = Math.min(Number(args["num"] ?? 8), 15);

    // Use DuckDuckGo HTML search (no API key needed)
    const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}&b=${limit}`;

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);

      const response = await fetch(searchUrl, {
        signal: controller.signal,
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          Accept: "text/html",
        },
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const r: WebToolResult = {
          success: false,
          error: {
            code: (response.status === 429 ? "RATE_LIMITED" : "INTERNAL_ERROR") as WebToolErrorCode,
            message: `DDG HTTP ${response.status}`,
            attemptedTiers: [{ tier: 1, name: "scrapling", outcome: "error", durationMs: 0, httpStatus: response.status }],
            suggestedEscalation: "live_browser",
          },
        };
        return serializeWebToolResult(r);
      }

      const html = await response.text();
      const results: SearchResult[] = [];

      // Parse results from HTML
      const resultRegex =
        /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)<\/a>[\s\S]*?<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/gi;

      let match;
      while (
        (match = resultRegex.exec(html)) !== null &&
        results.length < limit
      ) {
        let url = match[1];

        // Decode DuckDuckGo redirect URL
        if (url.includes("uddg=")) {
          const uddgMatch = url.match(/uddg=([^&]+)/);
          if (uddgMatch) {
            url = decodeURIComponent(uddgMatch[1]);
          }
        } else if (url.startsWith("//")) {
          url = "https:" + url;
        }

        const title = match[2].replace(/<[^>]+>/g, "").trim();
        const snippet = match[3].replace(/<[^>]+>/g, "").trim();

        if (title && url && url.startsWith("http")) {
          results.push({
            title,
            url,
            snippet: snippet || "",
          });
        }
      }

      // Fallback: simpler parsing
      if (results.length === 0) {
        const simpleRegex =
          /<result[^>]*>[\s\S]*?href="(https:\/\/[^"]+)"[^>]*>[\s\S]*?<a[^>]*>([^<]+)<\/a>/gi;
        while (
          (match = simpleRegex.exec(html)) !== null &&
          results.length < limit
        ) {
          const url = match[1];
          const title = match[2].replace(/<[^>]+>/g, "").trim();
          if (title && url && !url.includes("duckduckgo")) {
            results.push({ title, url, snippet: "" });
          }
        }
      }

      if (results.length === 0) {
        // Classify via cheap-tier model — no hardcoded keywords.
        const classifier = context.classifier;
        if (classifier) {
          const verdict = await classifier.classify({
            url: searchUrl,
            httpStatus: response.status,
            bodyPreview: html.slice(0, 2048),
          });
          if (verdict.blocked) {
            const r: WebToolResult = {
              success: false,
              error: {
                code: "BLOCKED_BY_ANTI_BOT",
                message: `DDG returned a CAPTCHA / anti-bot page for "${query}".`,
                attemptedTiers: [
                  { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 0, blockedReason: verdict.reason ?? "captcha" },
                ],
                suggestedEscalation: "live_browser",
              },
            };
            return serializeWebToolResult(r);
          }
        }
        const r: WebToolResult = {
          success: true,
          data: { kind: "search", query, results: [] },
        };
        return serializeWebToolResult(r);
      }

      const r: WebToolResult = {
        success: true,
        data: {
          kind: "search",
          query,
          results: results.slice(0, limit),
        },
      };
      return serializeWebToolResult(r);
    } catch (error) {
      const code: WebToolErrorCode = error instanceof Error && error.name === "AbortError" ? "TIMEOUT" : "INTERNAL_ERROR";
      const message = error instanceof Error ? error.message : "unknown error";
      const r: WebToolResult = {
        success: false,
        error: {
          code,
          message,
          attemptedTiers: [{ tier: 1, name: "scrapling", outcome: code === "TIMEOUT" ? "timeout" : "error", durationMs: 0 }],
          suggestedEscalation: "live_browser",
        },
      };
      return serializeWebToolResult(r);
    }
  },
};

