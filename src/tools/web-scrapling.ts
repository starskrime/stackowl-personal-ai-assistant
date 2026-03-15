/**
 * StackOwl — Scrapling Web Crawl Tool
 *
 * Anti-bot web scraping powered by Scrapling (Python).
 * Bypasses Cloudflare, TLS fingerprinting, and CAPTCHA challenges.
 * Falls back to basic web_crawl if Scrapling is not installed.
 *
 * Install: pip install scrapling && scrapling install
 */

import { spawn } from "node:child_process";
import type { ToolImplementation, ToolContext } from "./registry.js";

const TIMEOUT = 60_000; // 60s — stealth fetchers can be slow

type FetcherMode = "basic" | "stealth" | "dynamic";

/**
 * Run a Python script via subprocess and return stdout.
 */
function runPython(script: string, timeout = TIMEOUT): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn("python3", ["-c", script], {
      stdio: ["pipe", "pipe", "pipe"],
      timeout,
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    proc.on("close", (code) => {
      if (code !== 0) {
        // Check if scrapling or its dependencies are not installed
        if (stderr.includes("No module named 'scrapling'") ||
            (stderr.includes("ModuleNotFoundError") && stderr.includes("scrapling"))) {
          reject(new Error(
            "Scrapling is not installed. Install it with:\n" +
            "  pip install scrapling[all] && scrapling install\n" +
            "Then try again."
          ));
        } else if (stderr.includes("No module named") || stderr.includes("ModuleNotFoundError")) {
          // Missing dependency — extract the module name
          const modMatch = stderr.match(/No module named '([^']+)'/);
          const modName = modMatch ? modMatch[1] : "unknown";
          reject(new Error(
            `Missing Python dependency: ${modName}\n` +
            `Install it with: pip install ${modName}\n` +
            `Or install all Scrapling deps: pip install scrapling[all]`
          ));
        } else {
          reject(new Error(stderr.trim() || `Python exited with code ${code}`));
        }
      } else {
        resolve(stdout);
      }
    });

    proc.on("error", (err) => {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") {
        reject(new Error(
          "Python 3 is not installed or not in PATH. " +
          "Scrapling requires Python 3.8+."
        ));
      } else {
        reject(err);
      }
    });
  });
}

/**
 * Build the Python script for fetching a URL with Scrapling.
 */
function buildFetchScript(
  url: string,
  mode: FetcherMode,
  options: {
    selector?: string;
    waitFor?: string;
    headless?: boolean;
    proxy?: string;
  } = {},
): string {
  const escapedUrl = url.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  const headless = options.headless !== false ? "True" : "False";

  // Common output logic — extract text, truncate, output as JSON
  const outputLogic = `
import json, sys

# Get page content
title = ""
try:
    title_el = page.find("title")
    if title_el:
        title = title_el.text.strip()
except:
    pass

# Extract specific selector or full body text
if selector:
    elements = page.find_all(selector)
    texts = [el.get_all_text().strip() if hasattr(el, 'get_all_text') else str(el.text).strip() for el in elements]
    content = "\\n\\n".join([t for t in texts if t])
else:
    content = page.get_all_text().strip()

# Truncate
MAX = 25000
if len(content) > MAX:
    content = content[:MAX] + "\\n\\n... [truncated]"

result = {
    "title": title,
    "url": "${escapedUrl}",
    "length": len(content),
    "content": content
}
print(json.dumps(result))
`;

  const selectorLine = options.selector
    ? `selector = '${options.selector.replace(/'/g, "\\'")}'`
    : `selector = None`;

  switch (mode) {
    case "basic":
      return `
from scrapling import Fetcher
${selectorLine}
fetcher = Fetcher()
page = fetcher.get('${escapedUrl}')
${outputLogic}`;

    case "stealth":
      return `
from scrapling import StealthyFetcher
${selectorLine}
fetcher = StealthyFetcher()
page = fetcher.fetch('${escapedUrl}', headless=${headless})
${outputLogic}`;

    case "dynamic":
      return `
from scrapling import DynamicFetcher
${selectorLine}
fetcher = DynamicFetcher()
page = fetcher.fetch('${escapedUrl}', headless=${headless}${options.waitFor ? `, wait_selector='${options.waitFor.replace(/'/g, "\\'")}'` : ""})
${outputLogic}`;

    default:
      return `
from scrapling import Fetcher
${selectorLine}
fetcher = Fetcher()
page = fetcher.get('${escapedUrl}')
${outputLogic}`;
  }
}

export const ScraplingTool: ToolImplementation = {
  definition: {
    name: "scrapling_fetch",
    description:
      "Advanced anti-bot web scraping powered by Scrapling. " +
      "Bypasses Cloudflare, bot detection, TLS fingerprinting, and CAPTCHAs. " +
      "USE THIS when web_crawl fails with 403/blocked/CAPTCHA errors. " +
      "Three modes: 'basic' (fast HTTP, spoofed TLS), 'stealth' (real browser fingerprint), " +
      "'dynamic' (full browser for JS-heavy SPAs). " +
      "Requires: pip install scrapling && scrapling install",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "Full URL to fetch (must start with http:// or https://)",
        },
        mode: {
          type: "string",
          description:
            "Fetcher mode: 'basic' (fast, spoofed TLS — try first), " +
            "'stealth' (real browser fingerprint, bypasses Cloudflare), " +
            "'dynamic' (full Playwright browser for JS-rendered pages). " +
            "Default: 'basic'. Escalate to stealth/dynamic only if basic fails.",
        },
        selector: {
          type: "string",
          description:
            "CSS selector to extract specific elements (e.g., 'article', '.content', '#main'). " +
            "Omit to get full page text.",
        },
        wait_for: {
          type: "string",
          description:
            "CSS selector to wait for before extracting (dynamic mode only). " +
            "Use when page content loads via JavaScript.",
        },
        headless: {
          type: "boolean",
          description:
            "Run browser in headless mode (default: true). Set false to see the browser window (debugging).",
        },
      },
      required: ["url"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const url = args.url as string;
    if (!url) return "Error: URL is required.";

    // Validate URL
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return "Error: Only http:// and https:// URLs are supported.";
      }
    } catch {
      return `Error: Invalid URL: ${url}`;
    }

    const mode = (args.mode as FetcherMode) || "basic";
    if (!["basic", "stealth", "dynamic"].includes(mode)) {
      return `Error: Invalid mode "${mode}". Use basic, stealth, or dynamic.`;
    }

    const selector = args.selector as string | undefined;
    const waitFor = args.wait_for as string | undefined;
    const headless = args.headless as boolean | undefined;

    if (waitFor && mode !== "dynamic") {
      return "Error: wait_for parameter only works with 'dynamic' mode.";
    }

    try {
      const script = buildFetchScript(url, mode, {
        selector,
        waitFor,
        headless,
      });

      const output = await runPython(script);

      // Parse the JSON output
      const result = JSON.parse(output.trim()) as {
        title: string;
        url: string;
        length: number;
        content: string;
      };

      // Detect Cloudflare/bot challenge pages
      const contentLower = result.content.toLowerCase();
      const titleLower = result.title.toLowerCase();
      const isBlocked =
        titleLower.includes("just a moment") ||
        titleLower.includes("attention required") ||
        titleLower.includes("security checkpoint") ||
        contentLower.includes("verify you are human") ||
        contentLower.includes("checking your browser") ||
        contentLower.includes("security verification") ||
        contentLower.includes("enable javascript and cookies to continue");

      if (isBlocked) {
        if (mode === "basic") {
          return (
            `BLOCKED: ${url} has Cloudflare/bot protection. Scrapling basic mode couldn't bypass it.\n` +
            `Try: scrapling_fetch(url='${url}', mode='stealth') — uses real browser fingerprint to bypass Cloudflare.`
          );
        }
        return (
          `BLOCKED: ${url} has aggressive bot protection that Scrapling ${mode} mode couldn't bypass.\n` +
          `Use computer_use as final fallback:\n` +
          `1. computer_use(action='open_url', text='${url}')\n` +
          `2. computer_use(action='wait', amount=3000)\n` +
          `3. computer_use(action='analyze_screen') — read the actual page content`
        );
      }

      if (!result.content || result.content.length < 50) {
        return (
          `Page returned very little content (${result.length} chars). ` +
          (mode === "basic"
            ? "Try mode='stealth' to bypass bot detection, or mode='dynamic' for JS-rendered pages."
            : mode === "stealth"
              ? "Try mode='dynamic' for JS-rendered pages, or use computer_use tool as fallback."
              : "The page may require login or have anti-bot protection that even Scrapling can't bypass. " +
                "Use computer_use tool (open_url + analyze_screen) as final fallback.")
        );
      }

      return `### ${result.title}\n\n${result.url}\n\n${result.content}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      // Provide helpful escalation guidance
      if (msg.includes("not installed")) {
        return msg; // Installation instructions already included
      }

      if (mode === "basic") {
        return (
          `Scrapling basic fetch failed: ${msg}\n\n` +
          `Try escalating:\n` +
          `1. scrapling_fetch(url, mode='stealth') — bypasses Cloudflare/TLS detection\n` +
          `2. scrapling_fetch(url, mode='dynamic') — full browser rendering\n` +
          `3. computer_use(action='open_url', text=url) → computer_use(action='analyze_screen') — real desktop browser`
        );
      }

      return `Scrapling ${mode} fetch failed: ${msg}`;
    }
  },
};
