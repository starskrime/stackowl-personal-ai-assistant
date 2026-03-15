/**
 * StackOwl — Web Crawl Tool (Simple HTTP Fetch)
 *
 * Fetches and cleans text from any URL using native Node.js fetch.
 * Simple and fast - no browser needed.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";

export const WebCrawlTool: ToolImplementation = {
  definition: {
    name: "web_crawl",
    description:
      "Fetch and extract text content from a specific URL. Use this AFTER google_search " +
      "to read a page you found, or when you already know the exact URL. " +
      "Returns cleaned text (no HTML). Good for: articles, documentation, API docs, data pages. " +
      "NOT for interactive sites (forms, SPAs, login-gated pages) — use the browser tool for those. " +
      "Limit: 25KB text. Some sites block crawlers — if you get empty/blocked results, try a different URL.",
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
    } catch (e) {
      throw new Error(`Invalid URL: ${url}`);
    }

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);

      const response = await fetch(url, {
        signal: controller.signal,
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          Accept:
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.5",
        },
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        if (response.status === 403) {
          return (
            `BLOCKED: ${url} returned HTTP 403 (Forbidden). ` +
            `The site is blocking automated access. Escalation path:\n` +
            `1. scrapling_fetch(url, mode='stealth') — anti-bot scraping with TLS fingerprint spoofing\n` +
            `2. scrapling_fetch(url, mode='dynamic') — full browser rendering\n` +
            `3. computer_use(action='open_url') + computer_use(action='analyze_screen') — real desktop browser, undetectable`
          );
        }
        return `Failed to fetch ${url}: HTTP ${response.status} ${response.statusText}`;
      }

      const contentType = response.headers.get("content-type") || "";

      // Check if it's HTML
      if (!contentType.includes("text/html")) {
        return `Content type is ${contentType}, but only HTML pages are supported. URL: ${url}`;
      }

      const html = await response.text();

      // Extract title
      const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
      const title = titleMatch ? titleMatch[1].trim() : "Untitled";

      // Simple HTML to text conversion
      let text = html
        // Remove script and style tags with their content
        .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
        .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
        // Remove HTML comments
        .replace(/<!--[\s\S]*?-->/g, "")
        // Replace block elements with newlines
        .replace(/<\/(p|div|h[1-6]|li|tr|br)>/gi, "\n")
        .replace(/<br\s*\/?>/gi, "\n")
        // Remove all remaining HTML tags
        .replace(/<[^>]+>/g, "")
        // Decode HTML entities
        .replace(/&nbsp;/g, " ")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        // Clean up whitespace
        .replace(/[\r\n]+/g, "\n")
        .replace(/[ \t]+\n/g, "\n")
        .replace(/\n[ \t]+/g, "\n")
        .trim();

      // Detect bot protection / challenge pages
      const botBlocked =
        title.includes("Security Checkpoint") ||
        title.includes("Just a moment") ||
        title.includes("Attention Required") ||
        text.includes("Verify you are human") ||
        text.includes("verifying your browser") ||
        text.includes("Enable JavaScript and cookies to continue") ||
        (text.length < 200 && text.includes("Checking your browser"));
      if (botBlocked) {
        return (
          `BLOCKED: ${url} has bot/CAPTCHA protection ("${title}"). ` +
          `This page cannot be read by web_crawl. Escalation path:\n` +
          `1. scrapling_fetch(url, mode='stealth') — bypasses Cloudflare/TLS fingerprinting\n` +
          `2. scrapling_fetch(url, mode='dynamic') — full browser with anti-detection\n` +
          `3. computer_use(action='open_url') + computer_use(action='analyze_screen') — real desktop browser, undetectable`
        );
      }

      // Limit text length
      const MAX_TEXT = 25000;
      if (text.length > MAX_TEXT) {
        text = text.slice(0, MAX_TEXT) + "\n\n... [truncated]";
      }

      return `### ${title}\n\n${url}\n\n${text}`;
    } catch (error) {
      if (error instanceof Error) {
        if (error.name === "AbortError") {
          return `Timeout: The request to ${url} took too long (30s)`;
        }
        return `Error fetching ${url}: ${error.message}`;
      }
      return `Error fetching ${url}: Unknown error`;
    }
  },
};
