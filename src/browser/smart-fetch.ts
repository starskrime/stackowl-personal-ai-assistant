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

import { log } from '../logger.js';
import type { BrowserPool } from './pool.js';

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
  source: 'fetch' | 'browser' | 'browser-retry';
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
  log.engine.info('[SmartFetch] Initialized with browser pool');
}

// ─── Realistic headers ──────────────────────────────────────────

const CHROME_HEADERS: Record<string, string> = {
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  Accept:
    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
  'Accept-Encoding': 'gzip, deflate, br',
  'Cache-Control': 'no-cache',
  'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
  'Sec-Ch-Ua-Mobile': '?0',
  'Sec-Ch-Ua-Platform': '"macOS"',
  'Sec-Fetch-Dest': 'document',
  'Sec-Fetch-Mode': 'navigate',
  'Sec-Fetch-Site': 'none',
  'Sec-Fetch-User': '?1',
  'Upgrade-Insecure-Requests': '1',
};

// ─── Bot detection ──────────────────────────────────────────────

interface BlockingStatus {
  blocked: boolean;
  type?: 'cloudflare' | 'captcha' | 'waf' | 'generic';
}

function detectBlocking(title: string, text: string, status?: number): BlockingStatus {
  const lTitle = title.toLowerCase();
  const lText = text.toLowerCase();

  // HTTP-level blocks
  if (status === 403 || status === 429 || status === 503) {
    // 503 with Cloudflare challenge content
    if (lText.includes('cloudflare') || lText.includes('just a moment')) {
      return { blocked: true, type: 'cloudflare' };
    }
    if (status === 403) return { blocked: true, type: 'waf' };
    if (status === 429) return { blocked: true, type: 'waf' };
  }

  // Content-level blocks
  if (
    lTitle.includes('security checkpoint') ||
    lTitle.includes('just a moment') ||
    lTitle.includes('attention required') ||
    lTitle.includes('access denied')
  ) {
    return { blocked: true, type: 'cloudflare' };
  }

  if (
    lText.includes('verify you are human') ||
    lText.includes('verifying your browser') ||
    lText.includes('enable javascript and cookies to continue') ||
    lText.includes('captcha') ||
    lText.includes('please complete the security check')
  ) {
    return { blocked: true, type: 'captcha' };
  }

  if (
    (text.length < 200 && lText.includes('checking your browser')) ||
    (text.length < 300 && lText.includes('ray id'))
  ) {
    return { blocked: true, type: 'cloudflare' };
  }

  return { blocked: false };
}

// ─── HTML → text ────────────────────────────────────────────────

function htmlToText(html: string): { title: string; text: string } {
  // Extract title
  const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : 'Untitled';

  // Strip non-content elements
  let text = html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<nav[^>]*>[\s\S]*?<\/nav>/gi, '')
    .replace(/<footer[^>]*>[\s\S]*?<\/footer>/gi, '')
    .replace(/<header[^>]*>[\s\S]*?<\/header>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    // Block elements → newlines
    .replace(/<\/(p|div|h[1-6]|li|tr|br|section|article)>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    // Remove all remaining tags
    .replace(/<[^>]+>/g, '')
    // Decode common entities
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    // Clean whitespace
    .replace(/[\r\n]+/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
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
      redirect: 'follow',
    });

    clearTimeout(timeoutId);

    const contentType = response.headers.get('content-type') || '';

    // Non-HTML content — return basic info
    if (!contentType.includes('text/html') && !contentType.includes('application/xhtml')) {
      return {
        html: `<title>${contentType}</title><p>Non-HTML content (${contentType}) at ${url}</p>`,
        status: response.status,
      };
    }

    const html = await response.text();
    return { html, status: response.status };
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      log.engine.info(`[SmartFetch] Fast path timeout for ${url}`);
    }
    return null;
  }
}

// ─── Tier 2: Browser fetch ──────────────────────────────────────

async function fetchWithBrowser(url: string): Promise<FetchResult | null> {
  if (!browserPool?.isReady) return null;

  let pooled;
  try {
    pooled = await browserPool.acquire();
    if (!pooled?.browser?.connected) {
      if (pooled) browserPool.release(pooled);
      return null;
    }
  } catch {
    return null;
  }

  try {
    const page = await browserPool.getPage(pooled);
    try {
      // Human-like random delay before navigation
      await new Promise(r => setTimeout(r, 200 + Math.random() * 600));

      await page.goto(url, {
        waitUntil: 'domcontentloaded',
        timeout: 20000,
      });

      // Wait a moment for any JS rendering
      await new Promise(r => setTimeout(r, 1000 + Math.random() * 500));

      const title = await page.title().catch(() => 'Untitled');
      const text = await page.evaluate(() => {
        // Remove non-content elements
        document.querySelectorAll('script, style, nav, footer, header, iframe, noscript')
          .forEach(el => el.remove());
        return document.body?.innerText || '';
      });

      const blocking = detectBlocking(title, text);
      if (blocking.blocked) {
        return {
          title,
          url,
          text: text.slice(0, 500),
          length: text.length,
          source: 'browser',
          blocked: true,
          blockType: blocking.type,
        };
      }

      return {
        title,
        url,
        text,
        length: text.length,
        source: 'browser',
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
  } finally {
    browserPool.release(pooled);
  }
}

// ─── Tier 3: Browser retry with stealth delay ───────────────────

async function fetchWithBrowserRetry(url: string): Promise<FetchResult | null> {
  if (!browserPool?.isReady) return null;

  let pooled;
  try {
    pooled = await browserPool.acquire();
    if (!pooled?.browser?.connected) {
      if (pooled) browserPool.release(pooled);
      return null;
    }
  } catch {
    return null;
  }

  try {
    const page = await browserPool.getPage(pooled);
    try {
      // Longer human-like delay
      await new Promise(r => setTimeout(r, 1000 + Math.random() * 2000));

      // Navigate to a neutral page first, then to target (looks more natural)
      await page.goto('about:blank', { timeout: 5000 }).catch(() => {});
      await new Promise(r => setTimeout(r, 500));

      await page.goto(url, {
        waitUntil: 'networkidle2',
        timeout: 30000,
      });

      // Longer wait for JS-heavy pages and challenge solvers
      await new Promise(r => setTimeout(r, 3000 + Math.random() * 2000));

      const title = await page.title().catch(() => 'Untitled');
      const text = await page.evaluate(() => {
        document.querySelectorAll('script, style, nav, footer, header, iframe, noscript')
          .forEach(el => el.remove());
        return document.body?.innerText || '';
      });

      const blocking = detectBlocking(title, text);

      return {
        title,
        url,
        text,
        length: text.length,
        source: 'browser-retry',
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
  } finally {
    browserPool.release(pooled);
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
export async function webFetch(url: string, options?: SmartFetchOptions): Promise<FetchResult> {
  const maxLength = options?.maxLength ?? 25000;
  const timeout = options?.timeout ?? 15000;
  const forceBrowser = options?.forceBrowser ?? false;

  // ─── Tier 1: Fast path (unless forced to browser) ─────────
  if (!forceBrowser) {
    const fast = await fetchFast(url, timeout, options?.headers);
    if (fast) {
      const { title, text } = htmlToText(fast.html);
      const blocking = detectBlocking(title, text, fast.status);

      if (!blocking.blocked) {
        const trimmed = text.length > maxLength
          ? text.slice(0, maxLength) + '\n\n... [truncated]'
          : text;
        return {
          title,
          url,
          text: trimmed,
          length: text.length,
          source: 'fetch',
          blocked: false,
        };
      }

      log.engine.info(
        `[SmartFetch] Fast path blocked (${blocking.type}) for ${url} — escalating to browser`,
      );
    }
  }

  // ─── Tier 2: Browser with stealth ─────────────────────────
  const browserResult = await fetchWithBrowser(url);
  if (browserResult && !browserResult.blocked) {
    browserResult.text = browserResult.text.length > maxLength
      ? browserResult.text.slice(0, maxLength) + '\n\n... [truncated]'
      : browserResult.text;
    return browserResult;
  }

  if (browserResult?.blocked) {
    log.engine.info(
      `[SmartFetch] Browser also blocked (${browserResult.blockType}) for ${url} — retrying with stealth delay`,
    );
  }

  // ─── Tier 3: Browser retry with longer delays ─────────────
  const retryResult = await fetchWithBrowserRetry(url);
  if (retryResult) {
    retryResult.text = retryResult.text.length > maxLength
      ? retryResult.text.slice(0, maxLength) + '\n\n... [truncated]'
      : retryResult.text;
    return retryResult;
  }

  // All tiers failed
  return {
    title: '',
    url,
    text: '',
    length: 0,
    source: 'fetch',
    blocked: true,
    blockType: 'all_tiers_failed',
  };
}

/**
 * Check if the smart fetch layer has browser capability.
 */
export function hasBrowserPool(): boolean {
  return browserPool?.isReady ?? false;
}
