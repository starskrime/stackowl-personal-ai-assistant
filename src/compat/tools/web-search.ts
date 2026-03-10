/**
 * StackOwl — OpenCLAW-Style Web Search Tool
 *
 * Provides web search using Brave, Perplexity, or other providers.
 * Similar to OpenCLAW's web_search tool.
 */

import type { ToolImplementation, ToolContext } from "../../tools/registry.js";

interface SearchProvider {
  name: string;
  search: (query: string, count: number) => Promise<SearchResult[]>;
}

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

const BRAVE_API_BASE = "https://api.search.brave.com/res/v1/web/search";
const PERPLEXITY_API_BASE = "https://api.perplexity.ai/search";

export class WebSearchTool implements ToolImplementation {
  private provider: SearchProvider;
  private apiKey: string | undefined;
  private cache: Map<string, { results: SearchResult[]; timestamp: number }> =
    new Map();
  private cacheTimeout = 15 * 60 * 1000; // 15 minutes

  constructor(provider: "brave" | "perplexity" = "brave", apiKey?: string) {
    this.apiKey = apiKey || process.env.WEB_SEARCH_API_KEY;

    if (provider === "perplexity" && !this.apiKey) {
      console.warn(
        "[WebSearch] Perplexity API key not set, falling back to Brave",
      );
      provider = "brave";
    }

    this.provider = this.createProvider(provider);
  }

  private createProvider(name: "brave" | "perplexity"): SearchProvider {
    if (name === "perplexity") {
      return {
        name: "perplexity",
        search: this.perplexitySearch.bind(this),
      };
    }
    return {
      name: "brave",
      search: this.braveSearch.bind(this),
    };
  }

  definition = {
    name: "web_search",
    description: `Search the web for information. Returns title, URL, and snippet for each result.

Examples:
- web_search query="latest AI news" - Search the web
- web_search query="how to use Docker" count=5 - Get 5 results

Use this when you need current information, facts, or web resources.`,
    parameters: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Search query",
        },
        count: {
          type: "number",
          description: "Number of results (1-10, default: 5)",
        },
      },
      required: ["query"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const query = args["query"] as string;
    const count = Math.min(Math.max((args["count"] as number) || 5, 1), 10);

    if (!query) {
      return "ERROR: query is required";
    }

    // Check cache
    const cacheKey = `${query}:${count}`;
    const cached = this.cache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < this.cacheTimeout) {
      return this.formatResults(cached.results);
    }

    try {
      const results = await this.provider.search(query, count);

      // Cache results
      this.cache.set(cacheKey, { results, timestamp: Date.now() });

      return this.formatResults(results);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `ERROR: Search failed - ${msg}`;
    }
  }

  private async braveSearch(
    query: string,
    count: number,
  ): Promise<SearchResult[]> {
    const apiKey = this.apiKey || process.env.BRAVE_API_KEY;

    const url = new URL(BRAVE_API_BASE);
    url.searchParams.set("q", query);
    url.searchParams.set("count", String(count));

    const headers: Record<string, string> = {
      Accept: "application/json",
    };

    if (apiKey) {
      headers["X-Subscription-Token"] = apiKey;
    }

    const response = await fetch(url.toString(), { headers });

    if (!response.ok) {
      throw new Error(
        `Brave API error: ${response.status} ${response.statusText}`,
      );
    }

    const data = (await response.json()) as {
      web?: {
        results?: Array<{
          title?: string;
          url?: string;
          description?: string;
        }>;
      };
    };

    const results = data.web?.results || [];

    return results.map((r) => ({
      title: r.title || "Untitled",
      url: r.url || "",
      snippet: r.description || "",
    }));
  }

  private async perplexitySearch(
    query: string,
    count: number,
  ): Promise<SearchResult[]> {
    if (!this.apiKey) {
      throw new Error("Perplexity API key not configured");
    }

    const response = await fetch(PERPLEXITY_API_BASE, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "llama-3.1-sonar-small-128k-online",
        query,
        max_results: count,
      }),
    });

    if (!response.ok) {
      throw new Error(`Perplexity API error: ${response.status}`);
    }

    const data = (await response.json()) as {
      results?: Array<{
        title?: string;
        url?: string;
        content?: string;
      }>;
    };

    const results = data.results || [];

    return results.map((r) => ({
      title: r.title || "Untitled",
      url: r.url || "",
      snippet: r.content || "",
    }));
  }

  private formatResults(results: SearchResult[]): string {
    if (results.length === 0) {
      return "No results found.";
    }

    const lines: string[] = ["## Search Results\n"];

    for (let i = 0; i < results.length; i++) {
      const r = results[i];
      lines.push(`${i + 1}. **${r.title}**`);
      lines.push(`   ${r.url}`);
      lines.push(
        `   ${r.snippet.slice(0, 200)}${r.snippet.length > 200 ? "..." : ""}`,
      );
      lines.push("");
    }

    return lines.join("\n");
  }
}
