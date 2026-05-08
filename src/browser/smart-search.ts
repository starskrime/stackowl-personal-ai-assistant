/**
 * StackOwl — Smart Search Layer (Element 16e)
 *
 * 4-tier search escalation chain that mirrors smart-fetch.ts:
 *   Tier 1: DDG HTML scraping (free, no key)
 *   Tier 2: Tavily API (key required, reliable JSON)
 *   Tier 3: Google via CamoFox (requires running CamoFox server)
 *   Tier 4: Google via Puppeteer (local Chromium)
 *
 * Entry point: searchEnvelope(query, num, deps)
 */

import {
  runEscalationChain,
  type TierRunner,
  type TierRunResult,
  type DispatcherCtx,
} from "./smart-fetch.js";
import type { WebToolResult } from "./envelope.js";
import { parseGoogleHtml, type SearchResult } from "./google-parser.js";
import type { CamoFoxClient } from "./camofox-client.js";
import type { PuppeteerFetcher } from "./puppeteer-fetcher.js";
import { GatewayEventBus } from "../gateway/event-bus.js";
import type { BlockingClassifier } from "./blocking-classifier.js";

// ─── Realistic headers for DDG ───────────────────────────────────

const DDG_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
  "Cache-Control": "no-cache",
};

// ─── DDG HTML result parser ──────────────────────────────────────

function parseDdgHtml(html: string): SearchResult[] {
  const results: SearchResult[] = [];

  // Primary: .result__a href + .result__snippet
  const resultRegex =
    /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/gi;

  let match: RegExpExecArray | null;
  while ((match = resultRegex.exec(html)) !== null) {
    let url = match[1];
    if (url.includes("uddg=")) {
      const m = url.match(/uddg=([^&]+)/);
      if (m) url = decodeURIComponent(m[1]);
    } else if (url.startsWith("//")) {
      url = "https:" + url;
    }
    const title = match[2].replace(/<[^>]+>/g, "").trim();
    const snippet = match[3].replace(/<[^>]+>/g, "").trim();
    if (title && url && url.startsWith("http")) {
      results.push({ title, url, snippet });
    }
  }

  return results;
}

// ─── Tier 1: DDG HTML ─────────────────────────────────────────────

export function createDdgHtmlTier(): TierRunner {
  return {
    tier: 1,
    name: "scrapling",
    isAvailable: () => true,
    async run(query, _ctx): Promise<TierRunResult> {
      const t0 = Date.now();
      // 300–900ms jitter to avoid rate-limiting
      await new Promise((r) => setTimeout(r, 300 + Math.random() * 600));

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 20_000);

        const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
        const response = await fetch(searchUrl, {
          signal: controller.signal,
          headers: DDG_HEADERS,
        });
        clearTimeout(timeoutId);

        if (!response.ok) {
          return {
            attempt: {
              tier: 1,
              name: "scrapling",
              durationMs: Date.now() - t0,
              outcome: response.status === 429 ? "blocked" : "error",
              httpStatus: response.status,
              blockedReason: response.status === 429 ? "rate-limit" : undefined,
            },
          };
        }

        const html = await response.text();
        const results = parseDdgHtml(html);

        // No results — may be a CAPTCHA/anti-bot page → blocked, escalate
        if (results.length === 0) {
          return {
            attempt: {
              tier: 1,
              name: "scrapling",
              durationMs: Date.now() - t0,
              outcome: "blocked",
              blockedReason: "captcha",
            },
          };
        }

        return {
          attempt: {
            tier: 1,
            name: "scrapling",
            durationMs: Date.now() - t0,
            outcome: "success",
          },
          data: {
            kind: "search",
            query,
            results,
          },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && err.name === "AbortError";
        return {
          attempt: {
            tier: 1,
            name: "scrapling",
            durationMs: Date.now() - t0,
            outcome: isTimeout ? "timeout" : "error",
          },
        };
      }
    },
  };
}

// ─── Tier 2: Tavily API ────────────────────────────────────────────

export function createTavilyApiTier(apiKey: string): TierRunner {
  return {
    tier: 2,
    name: "tavily-api",
    isAvailable: () => true,
    async run(query, _ctx): Promise<TierRunResult> {
      const t0 = Date.now();
      try {
        const response = await fetch("https://api.tavily.com/search", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            api_key: apiKey,
            query,
            search_depth: "basic",
            include_answer: false,
            max_results: 10,
          }),
          signal: AbortSignal.timeout(15_000),
        });

        if (!response.ok) {
          return {
            attempt: {
              tier: 2,
              name: "tavily-api",
              durationMs: Date.now() - t0,
              outcome: response.status === 429 ? "blocked" : "error",
              httpStatus: response.status,
              blockedReason: response.status === 429 ? "rate-limit" : undefined,
            },
          };
        }

        const json = (await response.json()) as {
          results?: Array<{ title: string; url: string; content?: string }>;
        };

        const results: SearchResult[] = (json.results ?? []).map((r) => ({
          title: r.title,
          url: r.url,
          snippet: r.content,
        }));

        return {
          attempt: {
            tier: 2,
            name: "tavily-api",
            durationMs: Date.now() - t0,
            outcome: "success",
          },
          data: {
            kind: "search",
            query,
            results,
          },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
        return {
          attempt: {
            tier: 2,
            name: "tavily-api",
            durationMs: Date.now() - t0,
            outcome: isTimeout ? "timeout" : "error",
          },
        };
      }
    },
  };
}

// ─── Tier 3: Google via CamoFox ───────────────────────────────────

export function createGoogleCamoFoxTier(client: CamoFoxClient): TierRunner {
  return {
    tier: 3,
    name: "google-camofox",
    isAvailable: () => client.isHealthy(),
    async run(query, _ctx): Promise<TierRunResult> {
      const t0 = Date.now();
      const userId = "stackowl-search";
      let tabId: string | null = null;
      try {
        // Navigate to Google search via CamoFox macro
        const tab = await client.createTab(userId);
        tabId = tab.tabId;
        const snap = await client.navigate(
          tabId,
          userId,
          `@google_search ${query}`,
        );

        const html = snap.snapshot ?? "";
        const results = parseGoogleHtml(html, query);

        if (results.length === 0) {
          return {
            attempt: {
              tier: 3,
              name: "google-camofox",
              durationMs: Date.now() - t0,
              outcome: "blocked",
              blockedReason: "captcha",
            },
          };
        }

        return {
          attempt: {
            tier: 3,
            name: "google-camofox",
            durationMs: Date.now() - t0,
            outcome: "success",
          },
          data: {
            kind: "search",
            query,
            results,
          },
        };
      } catch {
        return {
          attempt: {
            tier: 3,
            name: "google-camofox",
            durationMs: Date.now() - t0,
            outcome: "error",
          },
        };
      } finally {
        if (tabId) await client.closeTab(tabId, userId).catch(() => {});
      }
    },
  };
}

// ─── Tier 4: Google via Puppeteer ────────────────────────────────

export function createGooglePuppeteerTier(fetcher: PuppeteerFetcher): TierRunner {
  return {
    tier: 4,
    name: "google-puppeteer",
    isAvailable: () => fetcher.probe(),
    async run(query, _ctx): Promise<TierRunResult> {
      const t0 = Date.now();
      try {
        const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=en`;
        const r = await fetcher.fetch(searchUrl, {
          waitForSelector: "div.g",
          waitForSelectorTimeout: 5000,
        });
        const results = parseGoogleHtml(r.html, query);

        if (results.length === 0) {
          return {
            attempt: {
              tier: 4,
              name: "google-puppeteer",
              durationMs: Date.now() - t0,
              outcome: "blocked",
              blockedReason: "captcha",
              httpStatus: r.status,
            },
          };
        }

        return {
          attempt: {
            tier: 4,
            name: "google-puppeteer",
            durationMs: Date.now() - t0,
            outcome: "success",
            httpStatus: r.status,
          },
          data: {
            kind: "search",
            query,
            results,
          },
        };
      } catch {
        return {
          attempt: {
            tier: 4,
            name: "google-puppeteer",
            durationMs: Date.now() - t0,
            outcome: "error",
          },
        };
      }
    },
  };
}

// ─── SearchEnvelopeDeps ───────────────────────────────────────────

export interface SearchEnvelopeDeps {
  /** Tavily API key — if absent, Tier 2 is omitted */
  tavilyApiKey?: string;
  /** CamoFox client — if absent, Tier 3 is omitted */
  camofox?: CamoFoxClient;
  /** PuppeteerFetcher — if absent, Tier 4 is omitted */
  puppeteer?: PuppeteerFetcher;
  /** Classifier for blocking detection — if absent, falls back to heuristic detection */
  classifier?: BlockingClassifier;
  /** Event bus for tier telemetry */
  bus?: GatewayEventBus;
}

// ─── Entry point ─────────────────────────────────────────────────

/**
 * Run the 4-tier search escalation chain.
 *
 * @param query   Search query string
 * @param num     Max results to return (trimmed after first successful tier)
 * @param deps    Optional tier dependencies; tiers are omitted when deps absent
 */
export async function searchEnvelope(
  query: string,
  num: number,
  deps: SearchEnvelopeDeps,
): Promise<WebToolResult> {
  const tiers: TierRunner[] = [];

  // Tier 1: DDG HTML — always included
  tiers.push(createDdgHtmlTier());

  // Tier 2: Tavily API — only if key provided
  if (deps.tavilyApiKey) {
    tiers.push(createTavilyApiTier(deps.tavilyApiKey));
  }

  // Tier 3: Google via CamoFox — only if client provided
  if (deps.camofox) {
    tiers.push(createGoogleCamoFoxTier(deps.camofox));
  }

  // Tier 4: Google via Puppeteer — only if fetcher provided
  if (deps.puppeteer) {
    tiers.push(createGooglePuppeteerTier(deps.puppeteer));
  }

  // Create context with required bus — provide stub if absent
  const bus = deps.bus ?? new GatewayEventBus();
  const ctx: DispatcherCtx = { bus };

  // runEscalationChain passes the first arg to each tier's run() as-is.
  // We pass the query as the "url" parameter — each tier interprets it as a query.
  const result = await runEscalationChain(tiers, query, ctx);

  if (!result.success) {
    // Always attach suggestedEscalation for search failures
    return {
      success: false,
      error: {
        ...result.error,
        suggestedEscalation: "live_browser",
      },
    };
  }

  // Trim results to the requested number
  if (result.data.kind === "search" && result.data.results.length > num) {
    return {
      success: true,
      data: {
        ...result.data,
        results: result.data.results.slice(0, num),
      },
    };
  }

  return result;
}
