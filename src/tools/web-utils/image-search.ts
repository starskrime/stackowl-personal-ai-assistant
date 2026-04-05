/**
 * StackOwl — Web Image Search Tool
 *
 * Finds real photos/images from the internet using DuckDuckGo image search.
 * Returns direct image URLs that can be passed to send_file to download and share.
 *
 * Workflow for "find me pictures of X":
 *   1. web_image_search(query) → list of image URLs + titles + sources
 *   2. send_file(url, caption) → downloads image and sends to user
 */

import type { ToolImplementation, ToolContext } from "../registry.js";

interface ImageResult {
  title: string;
  imageUrl: string;
  sourceUrl: string;
  width?: number;
  height?: number;
}

/**
 * Get the DuckDuckGo vqd token required for image search.
 * DDG requires this token to be extracted from the initial search page.
 */
async function getVqdToken(query: string): Promise<string> {
  const response = await fetch(
    `https://duckduckgo.com/?q=${encodeURIComponent(query)}&iax=images&ia=images`,
    {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        Accept: "text/html",
      },
      signal: AbortSignal.timeout(15_000),
    },
  );

  if (!response.ok) {
    throw new Error(`DuckDuckGo returned HTTP ${response.status}`);
  }

  const html = await response.text();
  const match = html.match(/vqd=['"]([^'"]+)['"]/);
  if (!match?.[1]) {
    throw new Error("Could not extract vqd token from DuckDuckGo");
  }
  return match[1];
}

export const WebImageSearchTool: ToolImplementation = {
  definition: {
    name: "web_image_search",
    description:
      "Search the internet for real photos and images. " +
      "Use this when the user asks to find, search for, or get pictures/photos/images of something. " +
      "Returns direct image URLs — pass them to send_file to download and share with the user. " +
      "Do NOT use image_generation for finding existing photos — that tool CREATES new AI art. " +
      "Example flow: web_image_search('moon mission NASA') → send_file(imageUrl, caption)",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            "What to search for (e.g. 'moon mission NASA', 'Eiffel Tower night', 'golden retriever puppy')",
        },
        count: {
          type: "number",
          description: "Number of images to return (default 5, max 10)",
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
    if (!query) throw new Error("query is required");

    const count = Math.min(Number(args["count"] ?? 5), 10);

    try {
      // Step 1: get vqd token
      const vqd = await getVqdToken(query);

      // Step 2: fetch image results
      const searchUrl =
        `https://duckduckgo.com/i.js?` +
        `l=us-en&o=json&q=${encodeURIComponent(query)}&vqd=${encodeURIComponent(vqd)}&f=,,,,,&p=1`;

      const response = await fetch(searchUrl, {
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          Referer: "https://duckduckgo.com/",
          Accept: "application/json",
        },
        signal: AbortSignal.timeout(15_000),
      });

      if (!response.ok) {
        return `Image search failed: HTTP ${response.status}. Try a different query.`;
      }

      const data = (await response.json()) as {
        results?: Array<{
          title: string;
          image: string;
          url: string;
          width: number;
          height: number;
          thumbnail: string;
          source: string;
        }>;
      };

      if (!data.results || data.results.length === 0) {
        return `No images found for "${query}". Try a different search term.`;
      }

      const results: ImageResult[] = data.results
        .slice(0, count)
        .map((r) => ({
          title: r.title || query,
          imageUrl: r.image,
          sourceUrl: r.url || r.source || "",
          width: r.width,
          height: r.height,
        }))
        .filter((r) => r.imageUrl?.startsWith("http"));

      if (results.length === 0) {
        return `No valid image URLs found for "${query}". Try a different query.`;
      }

      const lines = [
        `Found ${results.length} image(s) for "${query}":`,
        "",
        ...results.map(
          (r, i) =>
            `${i + 1}. **${r.title}**\n   Image URL: ${r.imageUrl}\n   Source: ${r.sourceUrl}${r.width && r.height ? `\n   Size: ${r.width}×${r.height}` : ""}`,
        ),
        "",
        `To share an image, use: send_file(path: "<imageUrl>", caption: "<description>")`,
      ];

      return lines.join("\n");
    } catch (error) {
      if (error instanceof Error) {
        if (error.name === "AbortError") {
          return `Image search timed out. Try a simpler query.`;
        }
        return `Image search error: ${error.message}`;
      }
      return `Image search failed: Unknown error`;
    }
  },
};
