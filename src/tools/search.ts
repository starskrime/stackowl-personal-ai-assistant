/**
 * StackOwl — Google Search Tool (Simple HTTP)
 *
 * Performs web searches using a simple API approach.
 * Returns structured results - title, URL, snippet.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { camoFoxSearch } from "./camofox.js";

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

/**
 * Parse a CamoFox accessibility snapshot as search results.
 * The snapshot format is: "[role] text [link eN] anchor-text ..."
 * We extract links with surrounding text as title+snippet pairs.
 */
function parseSnapshotAsSearchResults(query: string, snapshot: string): string {
  // Extract lines containing links
  const lines = snapshot.split("\n").map((l) => l.trim()).filter(Boolean);
  const results: SearchResult[] = [];

  for (let i = 0; i < lines.length && results.length < 10; i++) {
    const line = lines[i];
    // Look for patterns like: [link eN] Title  followed by a URL on the next line
    const linkMatch = line.match(/\[link\s+e\d+\]\s*(.+)/);
    if (!linkMatch) continue;

    const title = linkMatch[1].replace(/\[.*?\]/g, "").trim();
    if (!title || title.length < 4) continue;

    // Find URL in nearby lines
    let url = "";
    for (let j = i + 1; j <= i + 3 && j < lines.length; j++) {
      const urlMatch = lines[j].match(/https?:\/\/\S+/);
      if (urlMatch) {
        url = urlMatch[0].replace(/[,)]$/, "");
        break;
      }
    }
    if (!url) continue;

    // Use next non-URL line as snippet
    const snippet =
      lines
        .slice(i + 1, i + 4)
        .find((l) => !l.startsWith("http") && l.length > 20)
        ?.replace(/\[.*?\]/g, "")
        .trim() ?? "";

    results.push({ title, url, snippet });
  }

  if (results.length === 0) {
    return `Search results for: "${query}" [via camofox]\n\n${snapshot.slice(0, 2000)}`;
  }

  const lines2 = results.map(
    (r, i) =>
      `${i + 1}. **${r.title}**\n   ${r.url}\n   ${r.snippet || "(no snippet)"}`,
  );
  return `Search results for: "${query}" [via camofox]\n\n${lines2.join("\n\n")}`;
}

export const DuckDuckGoSearchTool: ToolImplementation = {
  definition: {
    name: "duckduckgo_search",
    description:
      "Search the web for information. Returns titles, URLs, and snippets. " +
      "Use this as your FIRST step when you need current/real-time information " +
      "(news, prices, flight status, weather, etc.) or to find URLs to read with web_crawl. " +
      "Do NOT search for the same query twice — rephrase or try web_crawl on a specific URL instead. " +
      "After 2 searches on the same topic, STOP and use the results you already have.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            'A specific, targeted search query. Be precise — "THY83J flight status DFW arrival" is better than "Turkish Airlines flight"',
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
        // Detect CAPTCHA / bot-block and fall back to CamoFox @google_search
        const lHtml = html.toLowerCase();
        const isCaptcha =
          lHtml.includes("captcha") ||
          lHtml.includes("verify you are human") ||
          lHtml.includes("unusual traffic") ||
          lHtml.includes("robot") ||
          lHtml.includes("blocked") ||
          lHtml.includes("please complete the security check");

        if (isCaptcha) {
          const camoSnapshot = await camoFoxSearch("@google_search", query);
          if (camoSnapshot) {
            return parseSnapshotAsSearchResults(query, camoSnapshot);
          }
        }

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
