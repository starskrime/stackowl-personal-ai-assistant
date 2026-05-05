/**
 * StackOwl — Smart Fetch Layer
 *
 * Unified web access that ALL browsing goes through. Replaces raw fetch()
 * everywhere: tools, learning pipeline, research.
 *
 * Tiered strategy:
 *   1. Fast path — native fetch() with realistic headers (80% of requests)
 *   2. Browser escalation — warm pooled Chromium with stealth patches
 *   3. Stealth retry — longer wait, human-like delays
 *
 * The module holds a reference to the BrowserPool (set via initSmartFetch).
 * This avoids threading the pool through dozens of constructors.
 */

import { log } from "../logger.js";
import type { BrowserPool } from "./pool.js";
import {
  initCamoFoxClient,
  getCamoFoxClient,
} from "./camofox-client.js";
import type { CamoFoxClientConfig } from "./camofox-client.js";

// ─── Types ───────────────────────────────────────────────────────

export interface SmartFetchOptions {
  /** Max output text length. Default: 25000 */
  maxLength?: number;
  /** Timeout for the fast-path fetch in ms. Default: 15000 */
  timeout?: number;
  /** Skip fast path, go straight to browser. Default: false */
  forceBrowser?: boolean;
  /** Extra headers for fast path. */
  headers?: Record<string, string>;
}

export interface FetchResult {
  title: string;
  url: string;
  text: string;
  length: number;
  /** Which tier resolved the request */
  source: "fetch" | "browser" | "browser-retry" | "camofox";
  /** True if all tiers failed to bypass blocking */
  blocked: boolean;
  /** Blocking type if detected */
  blockType?: string;
}

// ─── Module state ────────────────────────────────────────────────

let browserPool: BrowserPool | null = null;

/**
 * Wire the smart fetch layer to the browser pool.
 * Call once during bootstrap, after BrowserPool.init().
 */
export function initSmartFetch(pool: BrowserPool): void {
  browserPool = pool;
  log.engine.info("[SmartFetch] Initialized with browser pool");
}

/**
 * Wire the smart fetch layer to a CamoFox server.
 * Call once during bootstrap if camofox.enabled is true.
 */
export function initCamoFox(config: CamoFoxClientConfig): void {
  initCamoFoxClient(config);
  log.engine.info(`[SmartFetch] CamoFox Tier 4 enabled at ${config.baseUrl}`);
}

// ─── Realistic headers ──────────────────────────────────────────

const CHROME_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
  "Accept-Encoding": "gzip, deflate, br",
  "Cache-Control": "no-cache",
  "Sec-Ch-Ua":
    '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
  "Sec-Ch-Ua-Mobile": "?0",
  "Sec-Ch-Ua-Platform": '"macOS"',
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
  "Sec-Fetch-User": "?1",
  "Upgrade-Insecure-Requests": "1",
};

// ─── Bot detection (Element 16: moved to BlockingClassifier) ────
// The legacy keyword-based detector was deleted. Tier runners now consult
// BlockingClassifier (IntelligenceRouter cheap-tier) for verdicts. The
// legacy webFetch / fetchWithBrowser paths below stub `blocking` until
// Phase D removes them entirely.

// ─── HTML → text ────────────────────────────────────────────────

function htmlToText(html: string): { title: string; text: string } {
  // Extract title
  const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : "Untitled";

  // Strip non-content elements
  let text = html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<nav[^>]*>[\s\S]*?<\/nav>/gi, "")
    .replace(/<footer[^>]*>[\s\S]*?<\/footer>/gi, "")
    .replace(/<header[^>]*>[\s\S]*?<\/header>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    // Block elements → newlines
    .replace(/<\/(p|div|h[1-6]|li|tr|br|section|article)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    // Remove all remaining tags
    .replace(/<[^>]+>/g, "")
    // Decode common entities
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    // Clean whitespace
    .replace(/[\r\n]+/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { title, text };
}

// ─── Tier 1: Fast path (native fetch) ───────────────────────────

async function fetchFast(
  url: string,
  timeout: number,
  extraHeaders?: Record<string, string>,
): Promise<{ html: string; status: number } | null> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const response = await fetch(url, {
      signal: controller.signal,
      headers: { ...CHROME_HEADERS, ...extraHeaders },
      redirect: "follow",
    });

    clearTimeout(timeoutId);

    const contentType = response.headers.get("content-type") || "";

    // Non-HTML content — return basic info
    if (
      !contentType.includes("text/html") &&
      !contentType.includes("application/xhtml")
    ) {
      return {
        html: `<title>${contentType}</title><p>Non-HTML content (${contentType}) at ${url}</p>`,
        status: response.status,
      };
    }

    const html = await response.text();
    return { html, status: response.status };
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      log.engine.info(`[SmartFetch] Fast path timeout for ${url}`);
    }
    return null;
  }
}

// ─── Browser helpers (shared pooled browser) ────────────────────

type PooledBrowser = Awaited<ReturnType<BrowserPool["acquire"]>>;

/**
 * Acquire a connected browser from the pool.
 * Lazily initializes the pool on first call — no browsers are launched at startup.
 * Returns null if pool is unavailable or browser is disconnected.
 */
async function acquireBrowser(): Promise<PooledBrowser | null> {
  if (!browserPool) return null;
  // Lazy init: launch browsers on first actual request
  if (!browserPool.isReady) {
    try {
      await browserPool.init();
    } catch (err) {
      log.engine.warn(`[SmartFetch] Browser pool init failed: ${err instanceof Error ? err.message : err}`);
      return null;
    }
    if (!browserPool.isReady) return null;
  }
  try {
    const pooled = await browserPool.acquire();
    if (!pooled?.browser?.connected) {
      if (pooled) browserPool.release(pooled);
      return null;
    }
    return pooled;
  } catch {
    return null;
  }
}

// ─── Tier 2: Browser fetch ──────────────────────────────────────

async function fetchWithBrowser(
  url: string,
  pooled: PooledBrowser,
): Promise<FetchResult | null> {
  try {
    const page = await browserPool!.getPage(pooled);
    try {
      // Human-like random delay before navigation
      await new Promise((r) => setTimeout(r, 200 + Math.random() * 600));

      await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 20000,
      });

      // Wait a moment for any JS rendering
      await new Promise((r) => setTimeout(r, 1000 + Math.random() * 500));

      const title = await page.title().catch(() => "Untitled");
      const text = await page.evaluate(() => {
        // Remove non-content elements
        document
          .querySelectorAll(
            "script, style, nav, footer, header, iframe, noscript",
          )
          .forEach((el) => el.remove());
        return document.body?.innerText || "";
      });

      const blocking = { blocked: false } as { blocked: boolean; type?: string };
      if (blocking.blocked) {
        return {
          title,
          url,
          text: text.slice(0, 500),
          length: text.length,
          source: "browser",
          blocked: true,
          blockType: blocking.type,
        };
      }

      return {
        title,
        url,
        text,
        length: text.length,
        source: "browser",
        blocked: false,
      };
    } finally {
      await page.close().catch(() => {});
    }
  } catch (err) {
    log.engine.warn(
      `[SmartFetch] Browser fetch failed for ${url}: ` +
        `${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
}

// ─── Tier 3: Browser retry with stealth delay ───────────────────

async function fetchWithBrowserRetry(
  url: string,
  pooled: PooledBrowser,
): Promise<FetchResult | null> {
  try {
    const page = await browserPool!.getPage(pooled);
    try {
      // Longer human-like delay
      await new Promise((r) => setTimeout(r, 1000 + Math.random() * 2000));

      // Navigate to a neutral page first, then to target (looks more natural)
      await page.goto("about:blank", { timeout: 5000 }).catch(() => {});
      await new Promise((r) => setTimeout(r, 500));

      await page.goto(url, {
        waitUntil: "networkidle2",
        timeout: 30000,
      });

      // Longer wait for JS-heavy pages and challenge solvers
      await new Promise((r) => setTimeout(r, 3000 + Math.random() * 2000));

      const title = await page.title().catch(() => "Untitled");
      const text = await page.evaluate(() => {
        document
          .querySelectorAll(
            "script, style, nav, footer, header, iframe, noscript",
          )
          .forEach((el) => el.remove());
        return document.body?.innerText || "";
      });

      const blocking = { blocked: false } as { blocked: boolean; type?: string };

      return {
        title,
        url,
        text,
        length: text.length,
        source: "browser-retry",
        blocked: blocking.blocked,
        blockType: blocking.type,
      };
    } finally {
      await page.close().catch(() => {});
    }
  } catch (err) {
    log.engine.warn(
      `[SmartFetch] Browser retry failed for ${url}: ` +
        `${err instanceof Error ? err.message : err}`,
    );
    return null;
  }
}

// ─── Public API ─────────────────────────────────────────────────

/**
 * Unified web fetch with automatic bot-detection escalation.
 *
 * Usage:
 *   import { webFetch } from '../browser/smart-fetch.js';
 *   const result = await webFetch('https://example.com');
 *   if (!result.blocked) console.log(result.text);
 */
export async function webFetch(
  url: string,
  options?: SmartFetchOptions,
): Promise<FetchResult> {
  const maxLength = options?.maxLength ?? 25000;
  const timeout = options?.timeout ?? 15000;
  const forceBrowser = options?.forceBrowser ?? false;

  // ─── Tier 1: Fast path (unless forced to browser) ─────────
  if (!forceBrowser) {
    const fast = await fetchFast(url, timeout, options?.headers);
    if (fast) {
      const { title, text } = htmlToText(fast.html);
      const blocking = { blocked: false } as { blocked: boolean; type?: string };

      if (!blocking.blocked) {
        const trimmed =
          text.length > maxLength
            ? text.slice(0, maxLength) + "\n\n... [truncated]"
            : text;
        return {
          title,
          url,
          text: trimmed,
          length: text.length,
          source: "fetch",
          blocked: false,
        };
      }

      log.engine.info(
        `[SmartFetch] Fast path blocked (${blocking.type}) for ${url} — escalating to browser`,
      );
    }
  }

  // Acquire a single browser for tiers 2 and 3 (avoids double-acquisition deadlock)
  const pooled = await acquireBrowser();
  if (pooled) {
    try {
      // ─── Tier 2: Browser with stealth ─────────────────────────
      const browserResult = await fetchWithBrowser(url, pooled);
      if (browserResult && !browserResult.blocked) {
        browserResult.text =
          browserResult.text.length > maxLength
            ? browserResult.text.slice(0, maxLength) + "\n\n... [truncated]"
            : browserResult.text;
        return browserResult;
      }

      if (browserResult?.blocked) {
        log.engine.info(
          `[SmartFetch] Browser also blocked (${browserResult.blockType}) for ${url} — retrying with stealth delay`,
        );
      }

      // ─── Tier 3: Browser retry with longer delays ─────────────
      const retryResult = await fetchWithBrowserRetry(url, pooled);
      if (retryResult) {
        retryResult.text =
          retryResult.text.length > maxLength
            ? retryResult.text.slice(0, maxLength) + "\n\n... [truncated]"
            : retryResult.text;
        return retryResult;
      }
    } finally {
      browserPool!.release(pooled);
    }
  }

  // ─── Tier 4: CamoFox anti-detection browser ───────────────
  const camoFox = getCamoFoxClient();
  if (camoFox) {
    log.engine.info(`[SmartFetch] Escalating to CamoFox Tier 4 for ${url}`);
    let tabId: string | null = null;
    const userId = "stackowl-smartfetch";
    try {
      const tab = await camoFox.createTab(userId, url);
      tabId = tab.tabId;
      const snap = await camoFox.snapshot(tabId, userId);
      // Convert accessibility snapshot to plain text (good enough for content extraction)
      const text = snap.snapshot
        .replace(/\[[\w\s]+\]\s*/g, "") // strip [role] labels
        .replace(/\be\d+\b/g, "")        // strip eN refs
        .replace(/\s{2,}/g, " ")
        .trim();

      const trimmed =
        text.length > maxLength
          ? text.slice(0, maxLength) + "\n\n... [truncated]"
          : text;

      return {
        title: snap.url,
        url: snap.url,
        text: trimmed,
        length: text.length,
        source: "camofox",
        blocked: false,
      };
    } catch (err) {
      log.engine.warn(
        `[SmartFetch] CamoFox Tier 4 failed for ${url}: ${err instanceof Error ? err.message : err}`,
      );
    } finally {
      if (tabId) {
        await camoFox.closeTab(tabId, userId).catch(() => {});
      }
    }
  }

  // All tiers failed
  return {
    title: "",
    url,
    text: "",
    length: 0,
    source: "fetch",
    blocked: true,
    blockType: "all_tiers_failed",
  };
}

/**
 * Check if the smart fetch layer has browser capability.
 */
export function hasBrowserPool(): boolean {
  return browserPool?.isReady ?? false;
}

// ─── 3-tier dispatcher (Element 16 Phase A) ─────────────────────

import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { TierAttempt, WebToolData, WebToolResult } from "./envelope.js";

export interface TierRunOk { attempt: TierAttempt; data: WebToolData }
export interface TierRunFail { attempt: TierAttempt; data?: undefined }
export type TierRunResult = TierRunOk | TierRunFail;

export interface TierRunner {
  tier: number;
  name: "http" | "camofox" | "scrapling";
  isAvailable(): boolean | Promise<boolean>;
  run(url: string, ctx: { bus: GatewayEventBus }): Promise<TierRunResult>;
}

export interface DispatcherCtx {
  bus: GatewayEventBus;
  hint?: "anti-bot";
}

export async function runEscalationChain(
  runners: TierRunner[],
  url: string,
  ctx: DispatcherCtx,
): Promise<WebToolResult> {
  const attempts: TierAttempt[] = [];
  for (const r of runners) {
    if (ctx.hint === "anti-bot" && r.tier === 1) {
      attempts.push({ tier: 1, name: r.name, durationMs: 0, outcome: "skipped-by-hint" });
      continue;
    }
    const available = await r.isAvailable();
    if (!available) {
      attempts.push({ tier: r.tier, name: r.name, durationMs: 0, outcome: "unavailable" });
      continue;
    }
    const t0 = Date.now();
    ctx.bus.emit({ type: "web:tier_attempted", tier: r.tier, name: r.name, url, startedAt: t0 } as any);
    let res: TierRunResult;
    try { res = await r.run(url, { bus: ctx.bus }); }
    catch {
      const a: TierAttempt = { tier: r.tier, name: r.name, durationMs: Date.now() - t0, outcome: "error" };
      attempts.push(a);
      continue;
    }
    attempts.push(res.attempt);
    if (res.data) return { success: true, data: res.data };
    if (res.attempt.outcome === "blocked") {
      ctx.bus.emit({ type: "web:tier_blocked", tier: r.tier, name: r.name, blockedReason: res.attempt.blockedReason ?? "other", durationMs: res.attempt.durationMs } as any);
    }
  }

  const allUnavailable = attempts.every(a => a.outcome === "unavailable" || a.outcome === "skipped-by-hint");
  return {
    success: false,
    error: {
      code: allUnavailable ? "ALL_TIERS_UNAVAILABLE" : "BLOCKED_BY_ANTI_BOT",
      message: allUnavailable
        ? "No web fetch tier was available. Run `stackowl backends install` to install camofox/scrapling."
        : "All web fetch tiers exhausted; the page remained blocked.",
      attemptedTiers: attempts,
    },
  };
}

import type { BlockingClassifier } from "./blocking-classifier.js";

export interface HttpTierDeps {
  classifier: Pick<BlockingClassifier, "classify">;
  fetcher?: (url: string, timeoutMs: number) => Promise<{ status: number; body: string; contentType: string }>;
}

const TIER1_TIMEOUT_MS = 4000;

const TRIGGER_STATUSES = new Set([401, 403, 429, 503]);

export function createHttpTier(deps: HttpTierDeps): TierRunner {
  const fetcher = deps.fetcher ?? defaultHttpFetch;
  return {
    tier: 1,
    name: "http",
    isAvailable: () => true,
    async run(url, _ctx) {
      const t0 = Date.now();
      let resp: { status: number; body: string; contentType: string };
      try { resp = await fetcher(url, TIER1_TIMEOUT_MS); }
      catch {
        return { attempt: { tier: 1, name: "http", durationMs: Date.now() - t0, outcome: "timeout" } };
      }

      const trigger = TRIGGER_STATUSES.has(resp.status) || resp.status >= 500
        || (resp.status !== 200 && resp.body.length < 1024);
      if (trigger) {
        const v = await deps.classifier.classify({ url, httpStatus: resp.status, bodyPreview: resp.body.slice(0, 2048) });
        if (v.blocked) {
          return { attempt: {
            tier: 1, name: "http", durationMs: Date.now() - t0,
            outcome: "blocked", blockedReason: (v.reason ?? "other") as any, httpStatus: resp.status,
          } };
        }
      }

      const { title, text } = htmlToText(resp.body);
      return {
        attempt: { tier: 1, name: "http", durationMs: Date.now() - t0, outcome: "success", httpStatus: resp.status },
        data: { kind: "page", url, title, content: text, contentType: resp.contentType },
      };
    },
  };
}

async function defaultHttpFetch(url: string, timeoutMs: number) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctrl.signal, headers: CHROME_HEADERS, redirect: "follow" });
    const body = await r.text();
    return { status: r.status, body, contentType: r.headers.get("content-type") ?? "" };
  } finally { clearTimeout(t); }
}

import type { RuntimeAvailability } from "../runtime/availability.js";
import type { CamoFoxClient } from "./camofox-client.js";

export interface CamoFoxTierDeps {
  availability: Pick<RuntimeAvailability, "isReady">;
  client: CamoFoxClient | null;
  classifier?: Pick<BlockingClassifier, "classify">;
}

const TIER2_BUDGET_MS = 20000;

export function createCamoFoxTier(deps: CamoFoxTierDeps): TierRunner {
  return {
    tier: 2,
    name: "camofox",
    isAvailable: async () => {
      if (!deps.client) return false;
      return await deps.availability.isReady("camofox");
    },
    async run(url, _ctx) {
      const t0 = Date.now();
      const userId = "stackowl-smartfetch";
      let tabId: string | null = null;
      try {
        const tab = await deps.client!.createTab(userId, url);
        tabId = tab.tabId;
        const snap = await Promise.race([
          deps.client!.snapshot(tabId, userId),
          new Promise<null>((_r, rej) => setTimeout(() => rej(new Error("camofox-timeout")), TIER2_BUDGET_MS)),
        ]);
        if (!snap) throw new Error("camofox-empty");
        const text = (snap.snapshot ?? "").replace(/\[[\w\s]+\]\s*/g, "").replace(/\be\d+\b/g, "").replace(/\s{2,}/g, " ").trim();

        if (deps.classifier) {
          const v = await deps.classifier.classify({ url, httpStatus: 200, bodyPreview: text.slice(0, 2048) });
          if (v.blocked) {
            return { attempt: { tier: 2, name: "camofox", durationMs: Date.now() - t0, outcome: "blocked", blockedReason: (v.reason ?? "other") as any } };
          }
        }
        return {
          attempt: { tier: 2, name: "camofox", durationMs: Date.now() - t0, outcome: "success" },
          data: { kind: "page", url: snap.url ?? url, content: text },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && err.message === "camofox-timeout";
        return { attempt: { tier: 2, name: "camofox", durationMs: Date.now() - t0, outcome: isTimeout ? "timeout" : "error" } };
      } finally {
        if (tabId) await deps.client!.closeTab(tabId, userId).catch(() => {});
      }
    },
  };
}

export interface ScraplingTierDeps {
  probe: () => Promise<{ ok: boolean; version?: string; error?: string }>;
  runScrapling: (url: string) => Promise<{ title: string; url: string; content: string }>;
}

const TIER3_BUDGET_MS = 25000;
const SCRAPLING_INSTALL_HINT = "pip install 'scrapling[all]' && patchright install chromium";

export function createScraplingTier(deps: ScraplingTierDeps): TierRunner & { installHint(): string } {
  let probed: { ok: boolean } | null = null;
  let installHintMessage = SCRAPLING_INSTALL_HINT;

  return {
    tier: 3,
    name: "scrapling",
    isAvailable: async () => {
      if (probed) return probed.ok;
      const r = await deps.probe();
      probed = { ok: r.ok };
      if (!r.ok && r.error) installHintMessage = `${SCRAPLING_INSTALL_HINT}  # probe error: ${r.error}`;
      return r.ok;
    },
    installHint: () => installHintMessage,
    async run(url, _ctx) {
      const t0 = Date.now();
      try {
        const r = await Promise.race([
          deps.runScrapling(url),
          new Promise<null>((_r, rej) => setTimeout(() => rej(new Error("scrapling-timeout")), TIER3_BUDGET_MS)),
        ]);
        if (!r) throw new Error("scrapling-empty");
        return {
          attempt: { tier: 3, name: "scrapling", durationMs: Date.now() - t0, outcome: "success" },
          data: { kind: "page", url: r.url, title: r.title, content: r.content },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && err.message === "scrapling-timeout";
        return { attempt: { tier: 3, name: "scrapling", durationMs: Date.now() - t0, outcome: isTimeout ? "timeout" : "error" } };
      }
    },
  };
}
