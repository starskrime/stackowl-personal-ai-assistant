/**
 * StackOwl — Google Search Tool (Simple HTTP)
 *
 * Performs web searches using a simple API approach.
 * Returns structured results - title, URL, snippet.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

export const GoogleSearchTool: ToolImplementation = {
  definition: {
    name: "google_search",
    description:
      "Search the web and return the top results. Use this to find current news, research topics, or look up anything on the web.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            'The search query (e.g. "latest AI news", "TypeScript best practices 2025")',
        },
        num: {
          type: "number",
          description: "Number of results to return (default 8, max 15)",
        },
      },
      required: ["query"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
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
        return `Search failed: HTTP ${response.status}`;
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
        return `No results found for "${query}". Try a different search term.`;
      }

      const lines = results.map(
        (r, i) =>
          `${i + 1}. **${r.title}**\n   ${r.url}\n   ${r.snippet || "(no snippet)"}`,
      );

      return `Search results for: "${query}"\n\n${lines.join("\n\n")}`;
    } catch (error) {
      if (error instanceof Error) {
        if (error.name === "AbortError") {
          return `Search timeout. Try a simpler query.`;
        }
        return `Search error: ${error.message}`;
      }
      return `Search failed: Unknown error`;
    }
  },
};
