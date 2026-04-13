/**
 * StackOwl — CamoFox Browser Tool
 *
 * Anti-detection browser automation using CamoFox (Firefox/Camoufox).
 * Passes Cloudflare, Google, and bot-detection that breaks Chromium solutions.
 *
 * Session model:
 *   - Sessions are stateful (userId → tabId map persists across tool calls)
 *   - `start`   → create/recreate a session
 *   - `navigate` → auto-starts if no session exists, otherwise navigates
 *   - `stop`    → close the tab and clear session state
 *
 * Search macros: "@google_search query", "@youtube_search query", etc.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { getCamoFoxClient } from "../browser/camofox-client.js";
import type { SnapshotResponse } from "../browser/camofox-client.js";

// ─── Module-level session state ───────────────────────────────────
// Maps userId → tabId. Survives across multiple tool invocations
// within the same process lifetime.

const sessions = new Map<string, string>();

// ─── Helpers ─────────────────────────────────────────────────────

function formatSnapshot(label: string, snap: SnapshotResponse): string {
  const lines = [
    `[${label}] ${snap.url}`,
    ``,
    snap.snapshot || "(empty snapshot)",
  ];
  return lines.join("\n");
}

/** Require an active session or throw a helpful error. */
function requireTab(userId: string): string {
  const tabId = sessions.get(userId);
  if (!tabId) {
    throw new Error(
      `No active CamoFox session for userId="${userId}". Call action="start" or action="navigate" first.`,
    );
  }
  return tabId;
}

// ─── Tool definition ─────────────────────────────────────────────

export const CamoFoxTool: ToolImplementation = {
  definition: {
    name: "camofox",
    description: [
      "Anti-detection browser automation using CamoFox (Firefox/Camoufox engine).",
      "Use when web_crawl or other browsers are blocked by Cloudflare, Google, or bot-detection.",
      "",
      "Sessions are STATEFUL — start once, then navigate/click/type freely across calls.",
      "Each userId gets isolated cookies and storage (default userId: 'stackowl').",
      "",
      "Actions: start, navigate, snapshot, click, type, screenshot, scroll, wait, stop, youtube_transcript",
      "",
      "Search macros (use in url field):",
      "  @google_search <query>    @youtube_search <query>    @amazon_search <query>",
      "  @reddit_search <query>    @wikipedia_search <query>  @twitter_search <query>",
      "  @yelp_search <query>      @spotify_search <query>    @linkedin_search <query>",
      "  @tiktok_search <query>    @twitch_search <query>     @netflix_search <query>",
      "",
      "YouTube transcripts: action='youtube_transcript', url='https://youtube.com/watch?v=...'",
    ].join("\n"),
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: [
            "start",
            "navigate",
            "snapshot",
            "click",
            "type",
            "screenshot",
            "scroll",
            "wait",
            "stop",
            "youtube_transcript",
          ],
          description: "Action to perform",
        },
        url: {
          type: "string",
          description:
            'URL or search macro. E.g. "https://example.com" or "@google_search best coffee shops"',
        },
        ref: {
          type: "string",
          description:
            "Element reference from snapshot, e.g. e5. Use for click and type actions.",
        },
        text: {
          type: "string",
          description: "Text to type (for type action)",
        },
        pressEnter: {
          type: "boolean",
          description: "Press Enter after typing (default: false)",
        },
        direction: {
          type: "string",
          enum: ["up", "down", "left", "right"],
          description: "Scroll direction (for scroll action, default: down)",
        },
        amount: {
          type: "number",
          description: "Scroll amount in pixels (for scroll action, default: 500)",
        },
        selector: {
          type: "string",
          description: "CSS selector to wait for (for wait action)",
        },
        timeout: {
          type: "number",
          description: "Timeout in ms (for wait action)",
        },
        userId: {
          type: "string",
          description:
            'Session profile ID. Isolates cookies/storage per user. Default: "stackowl"',
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const client = getCamoFoxClient();
    if (!client) {
      return (
        "CamoFox is not configured. Add a `camofox` block to stackowl.config.json:\n" +
        '  "camofox": { "enabled": true, "baseUrl": "http://localhost:9377" }\n' +
        "Then start the CamoFox server: npx camofox-browser"
      );
    }

    const action = args["action"] as string;
    const userId = ((args["userId"] as string) || "stackowl").trim();
    const url = args["url"] as string | undefined;
    const ref = args["ref"] as string | undefined;
    const text = args["text"] as string | undefined;
    const pressEnter = args["pressEnter"] as boolean | undefined;
    const direction = (args["direction"] as "up" | "down" | "left" | "right") ?? "down";
    const amount = args["amount"] as number | undefined;
    const selector = args["selector"] as string | undefined;
    const timeout = args["timeout"] as number | undefined;

    try {
      switch (action) {
        // ── start ─────────────────────────────────────────────────
        case "start": {
          // Close existing session first (gracefully)
          const existingTabId = sessions.get(userId);
          if (existingTabId) {
            await client.closeTab(existingTabId, userId).catch(() => {});
            sessions.delete(userId);
          }

          const tab = await client.createTab(userId, url);
          sessions.set(userId, tab.tabId);

          return formatSnapshot("Session started", tab);
        }

        // ── navigate ──────────────────────────────────────────────
        case "navigate": {
          if (!url) return "Error: `url` is required for navigate action.";

          const existingTabId = sessions.get(userId);

          // Auto-start: no session yet — create tab and navigate in one request
          if (!existingTabId) {
            const tab = await client.createTab(userId, url);
            sessions.set(userId, tab.tabId);
            return formatSnapshot("Auto-started + navigated", tab);
          }

          const result = await client.navigate(existingTabId, userId, url);
          return formatSnapshot("Navigated", result);
        }

        // ── snapshot ──────────────────────────────────────────────
        case "snapshot": {
          const tabId = requireTab(userId);
          const result = await client.snapshot(tabId, userId);
          return formatSnapshot("Snapshot", result);
        }

        // ── click ─────────────────────────────────────────────────
        case "click": {
          if (!ref) return "Error: `ref` is required for click action (e.g. ref='e3').";
          const tabId = requireTab(userId);
          const result = await client.click(tabId, userId, ref);
          return formatSnapshot(`Clicked ${ref}`, result);
        }

        // ── type ──────────────────────────────────────────────────
        case "type": {
          if (!ref) return "Error: `ref` is required for type action.";
          if (text === undefined) return "Error: `text` is required for type action.";
          const tabId = requireTab(userId);
          const result = await client.type(tabId, userId, ref, text, pressEnter);
          return formatSnapshot(`Typed into ${ref}`, result);
        }

        // ── screenshot ────────────────────────────────────────────
        case "screenshot": {
          const tabId = requireTab(userId);
          const base64 = await client.screenshot(tabId, userId);
          return `Screenshot captured (base64 PNG, ${Math.round(base64.length * 0.75 / 1024)}KB):\ndata:image/png;base64,${base64}`;
        }

        // ── scroll ────────────────────────────────────────────────
        case "scroll": {
          const tabId = requireTab(userId);
          const result = await client.scroll(tabId, userId, direction, amount ?? 500);
          return formatSnapshot(`Scrolled ${direction} ${amount ?? 500}px`, result);
        }

        // ── wait ──────────────────────────────────────────────────
        case "wait": {
          const tabId = requireTab(userId);
          const result = await client.wait(tabId, userId, selector, timeout);
          const label = selector
            ? `Waited for "${selector}"`
            : `Waited ${timeout ?? "default"}ms`;
          return formatSnapshot(label, result);
        }

        // ── stop ──────────────────────────────────────────────────
        case "stop": {
          const tabId = sessions.get(userId);
          if (!tabId) {
            return `No active session for userId="${userId}".`;
          }
          await client.closeTab(tabId, userId).catch(() => {});
          sessions.delete(userId);
          return `Session for userId="${userId}" closed.`;
        }

        // ── youtube_transcript ────────────────────────────────────
        case "youtube_transcript": {
          if (!url) return "Error: `url` is required for youtube_transcript action.";
          const result = await client.youtubeTranscript(url);
          const header = result.title
            ? `### ${result.title}${result.duration ? ` (${Math.round(result.duration / 60)}min)` : ""}`
            : "### YouTube Transcript";
          return `${header}\n\n${result.transcript || "(no transcript available)"}`;
        }

        default:
          return `Unknown action: "${action}". Valid actions: start, navigate, snapshot, click, type, screenshot, scroll, wait, stop, youtube_transcript`;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);

      // Stale tabId — session was GC'd by server
      if (msg.includes("404") || msg.includes("not found")) {
        sessions.delete(userId);
        return (
          `CamoFox session expired or not found (tabId removed). ` +
          `Call action="start" or action="navigate" to create a new session.\n` +
          `Original error: ${msg}`
        );
      }

      // Server not running
      if (
        msg.includes("ECONNREFUSED") ||
        msg.includes("fetch failed") ||
        msg.includes("timed out")
      ) {
        return (
          `CamoFox server is not reachable. Start it with: npx camofox-browser\n` +
          `Default URL: http://localhost:9377\n` +
          `Error: ${msg}`
        );
      }

      return `CamoFox error: ${msg}`;
    }
  },
};

// ─── Exported helper for smart-fetch / search CAPTCHA fallback ────

/**
 * One-shot CamoFox fetch: creates a temp tab, gets snapshot, closes tab.
 * Used by smart-fetch Tier 4 and DDG CAPTCHA fallback.
 *
 * Returns null if CamoFox is unavailable.
 */
export async function camoFoxFetch(
  url: string,
  userId = "stackowl-smartfetch",
): Promise<{ snapshot: string; pageUrl: string } | null> {
  const client = getCamoFoxClient();
  if (!client) return null;

  let tabId: string | null = null;
  try {
    const tab = await client.createTab(userId, url);
    tabId = tab.tabId;
    const snap = await client.snapshot(tabId, userId);
    return { snapshot: snap.snapshot, pageUrl: snap.url };
  } catch {
    return null;
  } finally {
    if (tabId) {
      await client.closeTab(tabId, userId).catch(() => {});
    }
  }
}

/**
 * One-shot CamoFox search: runs a search macro, returns snapshot.
 * Used by DDG CAPTCHA fallback.
 */
export async function camoFoxSearch(
  macro: string,
  query: string,
  userId = "stackowl-search",
): Promise<string | null> {
  const client = getCamoFoxClient();
  if (!client) return null;

  let tabId: string | null = null;
  try {
    const tab = await client.createTab(userId);
    tabId = tab.tabId;
    const result = await client.navigate(tabId, userId, `${macro} ${query}`);
    return result.snapshot;
  } catch {
    return null;
  } finally {
    if (tabId) {
      await client.closeTab(tabId, userId).catch(() => {});
    }
  }
}
