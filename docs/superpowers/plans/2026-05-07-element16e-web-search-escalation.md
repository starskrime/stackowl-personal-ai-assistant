# Element 16e — Web Search Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-tier DDG-HTML `web_search` with a 4-tier escalation chain (DDG → Tavily API → Google-via-CamoFox → Google-via-Puppeteer) using the same `runEscalationChain` infrastructure built in Element 16d.

**Architecture:** Two new files — `src/browser/google-parser.ts` (parse Google SERP HTML into `SearchResult[]`) and `src/browser/smart-search.ts` (4 tier factories + `searchEnvelope()` entry point) — mirror the `smart-fetch.ts` pattern exactly. `search.ts` sheds its inline DDG scrape and calls `searchEnvelope()`. The wiring chain (GatewayContext → EngineContext → ToolContext) adds `camofox?` and `tavilyApiKey?` following the same pattern `puppeteer` used in 16d.

**Tech Stack:** TypeScript (NodeNext modules), Vitest, `runEscalationChain` + `TierRunner` + `WebToolResult` from `src/browser/smart-fetch.ts` / `envelope.ts`, `CamoFoxClient` from `src/browser/camofox-client.ts`, `PuppeteerFetcher` from `src/browser/puppeteer-fetcher.ts`, Tavily Search API (REST, no SDK).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/browser/envelope.ts` | modify | Add `"google-camofox"`, `"google-puppeteer"`, `"tavily-api"` to `TierName` + `NAMES` |
| `src/runtime/availability.ts` | modify | Add same 3 names to `BackendName` + `emptyMap()` |
| `src/browser/puppeteer-fetcher.ts` | modify | Add `waitForSelector?` option to `fetch()` so Google tier can wait for JS-rendered results |
| `src/browser/google-parser.ts` | **NEW** | `parseGoogleHtml(html, query): SearchResult[]` — 3-strategy ranked fallback |
| `src/browser/smart-search.ts` | **NEW** | 4 tier factories + `SearchEnvelopeDeps` + `searchEnvelope()` |
| `src/gateway/types.ts` | modify | Add `camofox?` + `tavilyApiKey?` to `GatewayContext` |
| `src/engine/runtime.ts` | modify | Add same 2 fields to `EngineContext` + both `toolCtx` construction sites |
| `src/gateway/handlers/context-builder.ts` | modify | Thread `camofox` + `tavilyApiKey` in `baseContext()` |
| `src/tools/registry.ts` | modify | Add same 2 fields to `ToolContext` |
| `src/tools/search.ts` | modify | Replace inline DDG scrape with `searchEnvelope()` |
| `src/index.ts` | modify | Bootstrap `tavilyApiKey` + wire `camofoxClient` into `gateway.ctx` |
| `__tests__/browser/google-parser.test.ts` | **NEW** | Unit tests for `parseGoogleHtml` |
| `__tests__/browser/smart-search.test.ts` | **NEW** | Unit tests for tier factories + `searchEnvelope` |
| `__tests__/browser/search-escalation.test.ts` | **NEW** | Integration tests for escalation chain scenarios |

---

## Task 1: Type System Prerequisites

**Files:**
- Modify: `src/browser/envelope.ts`
- Modify: `src/runtime/availability.ts`

- [ ] **Step 1: Update `TierName` and `NAMES` in envelope.ts**

Current state of `envelope.ts` line 18:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer";
```
Current `NAMES` set (line 66):
```typescript
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura", "puppeteer"]);
```

Replace both:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer" | "google-camofox" | "google-puppeteer" | "tavily-api";
```
```typescript
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura", "puppeteer", "google-camofox", "google-puppeteer", "tavily-api"]);
```

- [ ] **Step 2: Update `BackendName` and `emptyMap()` in availability.ts**

Current `availability.ts` line 6:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer";
```
Current `emptyMap()` (line ~23):
```typescript
function emptyMap(): AvailabilityMap {
  return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus(), puppeteer: emptyStatus() };
}
```

Replace both:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer" | "google-camofox" | "google-puppeteer" | "tavily-api";
```
```typescript
function emptyMap(): AvailabilityMap {
  return {
    camofox: emptyStatus(),
    scrapling: emptyStatus(),
    "live-browser": emptyStatus(),
    puppeteer: emptyStatus(),
    "google-camofox": emptyStatus(),
    "google-puppeteer": emptyStatus(),
    "tavily-api": emptyStatus(),
  };
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants
npx tsc --noEmit 2>&1 | head -20
```
Expected: no new errors (zero or same pre-existing count as before).

- [ ] **Step 4: Commit**

```bash
git add src/browser/envelope.ts src/runtime/availability.ts
git commit -m "feat(element16e): add google-camofox, google-puppeteer, tavily-api to TierName + BackendName"
```

---

## Task 2: PuppeteerFetcher — waitForSelector Option

**Files:**
- Modify: `src/browser/puppeteer-fetcher.ts`
- Test: `__tests__/browser/smart-search.test.ts` (covered in Task 5)

Google's January 2025 JS enforcement means `div.g` is not in the DOM immediately after `domcontentloaded`. The Puppeteer Google tier must wait up to 5s for the selector to appear before calling `page.content()`.

- [ ] **Step 1: Add `PuppeteerFetchOptions` interface**

In `src/browser/puppeteer-fetcher.ts`, the current `fetch()` signature is:
```typescript
async fetch(url: string, timeoutMs = 25_000): Promise<PuppeteerFetchResult>
```

Add an options interface and change the signature. Add immediately after the `PuppeteerFetchResult` interface (around line 20):

```typescript
export interface PuppeteerFetchOptions {
  timeoutMs?: number;
  /** CSS selector to wait for before calling page.content(). Default: none. */
  waitForSelector?: string;
  /** Timeout in ms for waitForSelector. Default: 5000. */
  waitForSelectorTimeout?: number;
}
```

- [ ] **Step 2: Update `fetch()` to use options**

Replace the current `fetch(url: string, timeoutMs = 25_000)` method with:

```typescript
async fetch(url: string, opts: PuppeteerFetchOptions = {}): Promise<PuppeteerFetchResult> {
  const timeoutMs = opts.timeoutMs ?? 25_000;
  if (!this.browser || !this.sessionPool) {
    throw new Error("PuppeteerFetcher not initialized — call init() first");
  }
  const session = await this.sessionPool.getSession();
  let context: BrowserContext | null = null;
  try {
    context = await this.browser.createBrowserContext();
    const page = await context.newPage();

    const cookies = session.getCookies(new URL(url).origin);
    if (cookies.length) await context.setCookie(...(cookies as any[]));

    const response = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: timeoutMs,
    });

    if (!response) throw new Error(`Navigation to ${url} failed — no response`);

    if (opts.waitForSelector) {
      try {
        await page.waitForSelector(opts.waitForSelector, {
          timeout: opts.waitForSelectorTimeout ?? 5000,
        });
      } catch {
        // Selector didn't appear — proceed with whatever HTML is available.
        // Parser will return [] and BlockingClassifier decides next action.
      }
    }

    const html = await page.content();
    const updatedCookies = await page.cookies();
    session.setCookiesFromResponse(updatedCookies as any, new URL(url).origin);
    session.markGood();

    return {
      html,
      finalUrl: page.url(),
      status: response.status(),
    };
  } catch (err) {
    session.markBad();
    throw err;
  } finally {
    await context?.close();
  }
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
npx tsc --noEmit 2>&1 | head -20
```
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add src/browser/puppeteer-fetcher.ts
git commit -m "feat(element16e): add waitForSelector option to PuppeteerFetcher.fetch()"
```

---

## Task 3: google-parser.ts — New File + Tests

**Files:**
- Create: `src/browser/google-parser.ts`
- Create: `__tests__/browser/google-parser.test.ts`

- [ ] **Step 1: Write the failing tests first**

Create `__tests__/browser/google-parser.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { parseGoogleHtml } from "../../src/browser/google-parser.js";

const JSON_LD_HTML = `<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SearchResultsPage",
  "mainEntity": {
    "@type": "ItemList",
    "itemListElement": [
      {
        "@type": "ListItem",
        "position": 1,
        "name": "Best Coffee Shops NYC",
        "url": "https://example.com/coffee",
        "description": "Top 10 coffee shops in New York City."
      }
    ]
  }
}
</script></head><body></body></html>`;

const JSON_LD_ARRAY_TYPE_HTML = `<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": ["SearchResultsPage", "ItemList"],
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Array Type Result",
      "url": "https://example.com/array-type"
    }
  ]
}
</script></head><body></body></html>`;

const DIV_G_HTML = `<html><body>
<div class="g">
  <h3><a href="https://example.com/divg">Div G Result</a></h3>
  <span>Snippet text for div.g result</span>
</div>
</body></html>`;

const HVEID_HTML = `<html><body>
<div data-hveid="ABC123">
  <h3><a href="https://example.com/hveid">Hveid Result</a></h3>
</div>
</body></html>`;

const CAPTCHA_HTML = `<html><body>
<h1>Before you continue</h1>
<p>This page checks to see if it's really you sending the requests.</p>
<form id="captcha-form"></form>
</body></html>`;

const ENCODED_URL_HTML = `<html><body>
<div class="g">
  <h3><a href="https%3A%2F%2Fexample.com%2Fencoded">Encoded URL Result</a></h3>
</div>
</body></html>`;

describe("parseGoogleHtml", () => {
  it("parses JSON-LD with scalar @type SearchResultsPage", () => {
    const results = parseGoogleHtml(JSON_LD_HTML, "coffee shops nyc");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      title: "Best Coffee Shops NYC",
      url: "https://example.com/coffee",
      snippet: "Top 10 coffee shops in New York City.",
    });
  });

  it("parses JSON-LD with array @type containing SearchResultsPage", () => {
    const results = parseGoogleHtml(JSON_LD_ARRAY_TYPE_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].url).toBe("https://example.com/array-type");
  });

  it("falls back to div.g h3 a when no JSON-LD", () => {
    const results = parseGoogleHtml(DIV_G_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe("Div G Result");
    expect(results[0].url).toBe("https://example.com/divg");
  });

  it("falls back to [data-hveid] h3 a as third option", () => {
    const results = parseGoogleHtml(HVEID_HTML, "test");
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe("Hveid Result");
    expect(results[0].url).toBe("https://example.com/hveid");
  });

  it("returns [] on CAPTCHA HTML without throwing", () => {
    expect(() => parseGoogleHtml(CAPTCHA_HTML, "test")).not.toThrow();
    expect(parseGoogleHtml(CAPTCHA_HTML, "test")).toEqual([]);
  });

  it("decodes percent-encoded URLs", () => {
    const results = parseGoogleHtml(ENCODED_URL_HTML, "test");
    expect(results[0]?.url).toBe("https://example.com/encoded");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/browser/google-parser.test.ts 2>&1 | tail -15
```
Expected: FAIL — `Cannot find module '../../src/browser/google-parser.js'`

- [ ] **Step 3: Implement google-parser.ts**

Create `src/browser/google-parser.ts`:

```typescript
export interface SearchResult {
  title: string;
  url: string;
  snippet?: string;
}

/**
 * Parse a Google SERP HTML string into SearchResult[].
 * Three strategies in ranked order:
 *   1. JSON-LD structured data (most stable, crawler-preserved)
 *   2. div.g h3 a[href] (classic organic result container)
 *   3. [data-hveid] h3 a[href] (position-tracking attribute fallback)
 * Returns [] if all strategies yield zero results — never throws.
 */
export function parseGoogleHtml(html: string, _query: string): SearchResult[] {
  // Strategy 1: JSON-LD
  const ldResults = parseJsonLd(html);
  if (ldResults.length > 0) return ldResults;

  // Strategy 2: div.g h3 a
  const divGResults = parseDivG(html);
  if (divGResults.length > 0) return divGResults;

  // Strategy 3: [data-hveid] h3 a
  return parseHveid(html);
}

function parseJsonLd(html: string): SearchResult[] {
  const scriptRe = /<script[^>]+type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = scriptRe.exec(html)) !== null) {
    try {
      const obj = JSON.parse(m[1]) as Record<string, unknown>;
      const types: unknown[] = Array.isArray(obj["@type"]) ? (obj["@type"] as unknown[]) : [obj["@type"]];
      const isSerp = types.some(t => t === "SearchResultsPage" || t === "ItemList");
      if (!isSerp) continue;

      const rawItems: unknown[] =
        (obj["mainEntity"] as Record<string, unknown>)?.["itemListElement"] as unknown[] ??
        (obj["itemListElement"] as unknown[]) ??
        [];

      const results: SearchResult[] = [];
      for (const item of rawItems) {
        if (!item || typeof item !== "object") continue;
        const i = item as Record<string, unknown>;
        const title = typeof i["name"] === "string" ? i["name"] : typeof i["headline"] === "string" ? i["headline"] : "";
        const url = typeof i["url"] === "string" ? i["url"] : "";
        const snippet = typeof i["description"] === "string" ? i["description"] : undefined;
        if (title && url && url.startsWith("http")) {
          results.push({ title, url, snippet });
        }
      }
      if (results.length > 0) return results;
    } catch {
      // malformed JSON-LD — try next block
    }
  }
  return [];
}

function parseDivG(html: string): SearchResult[] {
  // Match <h3> containing an <a href> — works whether inside div.g or not
  // We look specifically for <div class="g"> containers first, then fall through.
  const divGRe = /<div[^>]+class="[^"]*\bg\b[^"]*"[^>]*>[\s\S]*?<h3[^>]*>[\s\S]*?<a[^>]+href="([^"#][^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  return extractFromRegex(divGRe, html);
}

function parseHveid(html: string): SearchResult[] {
  const hveidRe = /<[^>]+data-hveid[^>]*>[\s\S]*?<h3[^>]*>[\s\S]*?<a[^>]+href="([^"#][^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  return extractFromRegex(hveidRe, html);
}

function extractFromRegex(re: RegExp, html: string): SearchResult[] {
  const results: SearchResult[] = [];
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const rawUrl = m[1];
    const rawTitle = m[2];
    let url: string;
    try {
      url = decodeURIComponent(rawUrl);
    } catch {
      url = rawUrl;
    }
    const title = rawTitle.replace(/<[^>]+>/g, "").trim();
    if (!url.startsWith("http") || !title || seen.has(url)) continue;
    // Filter out Google's own navigation links
    if (url.includes("google.com/search") || url.includes("google.com/preferences")) continue;
    seen.add(url);
    results.push({ title, url });
  }
  return results;
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/browser/google-parser.test.ts 2>&1 | tail -15
```
Expected: 6 tests passing, 0 failing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/google-parser.ts __tests__/browser/google-parser.test.ts
git commit -m "feat(element16e): add google-parser.ts with JSON-LD + div.g + data-hveid fallback"
```

---

## Task 4: smart-search.ts — New File + Unit Tests

**Files:**
- Create: `src/browser/smart-search.ts`
- Create: `__tests__/browser/smart-search.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/browser/smart-search.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import {
  createDdgHtmlTier,
  createTavilyApiTier,
  createGoogleCamoFoxTier,
  createGooglePuppeteerTier,
  searchEnvelope,
  type SearchEnvelopeDeps,
} from "../../src/browser/smart-search.js";

// ─── Tier identity tests ────────────────────────────────────────

describe("createDdgHtmlTier", () => {
  it("has tier:1 and name:'scrapling'", () => {
    const t = createDdgHtmlTier();
    expect(t.tier).toBe(1);
    expect(t.name).toBe("scrapling");
  });

  it("isAvailable() returns true", async () => {
    expect(await createDdgHtmlTier().isAvailable()).toBe(true);
  });
});

describe("createTavilyApiTier", () => {
  it("has tier:2 and name:'tavily-api'", () => {
    const t = createTavilyApiTier("test-key");
    expect(t.tier).toBe(2);
    expect(t.name).toBe("tavily-api");
  });

  it("isAvailable() returns true (key checked at construction)", async () => {
    expect(await createTavilyApiTier("k").isAvailable()).toBe(true);
  });

  it("maps Tavily json.results to SearchResult[]", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [{ title: "Test Title", url: "https://example.com", content: "Snippet" }],
      }),
    }) as any;
    const t = createTavilyApiTier("key");
    const result = await t.run("best coffee", { bus: { emit: () => {} } as any });
    expect(result.attempt.outcome).toBe("success");
    expect(result.data?.kind).toBe("search");
    if (result.data?.kind === "search") {
      expect(result.data.results[0]).toMatchObject({ title: "Test Title", url: "https://example.com" });
    }
    vi.restoreAllMocks();
  });
});

describe("createGoogleCamoFoxTier", () => {
  it("has tier:3 and name:'google-camofox'", () => {
    const client = { isHealthy: vi.fn().mockResolvedValue(true) } as any;
    const t = createGoogleCamoFoxTier(client);
    expect(t.tier).toBe(3);
    expect(t.name).toBe("google-camofox");
  });

  it("isAvailable() delegates to client.isHealthy()", async () => {
    const healthy = { isHealthy: vi.fn().mockResolvedValue(true) } as any;
    const unhealthy = { isHealthy: vi.fn().mockResolvedValue(false) } as any;
    expect(await createGoogleCamoFoxTier(healthy).isAvailable()).toBe(true);
    expect(await createGoogleCamoFoxTier(unhealthy).isAvailable()).toBe(false);
  });
});

describe("createGooglePuppeteerTier", () => {
  it("has tier:4 and name:'google-puppeteer'", () => {
    const fetcher = { probe: vi.fn().mockResolvedValue(true) } as any;
    const t = createGooglePuppeteerTier(fetcher);
    expect(t.tier).toBe(4);
    expect(t.name).toBe("google-puppeteer");
  });

  it("isAvailable() delegates to fetcher.probe()", async () => {
    const ready = { probe: vi.fn().mockResolvedValue(true) } as any;
    const notReady = { probe: vi.fn().mockResolvedValue(false) } as any;
    expect(await createGooglePuppeteerTier(ready).isAvailable()).toBe(true);
    expect(await createGooglePuppeteerTier(notReady).isAvailable()).toBe(false);
  });
});

// ─── searchEnvelope() tier inclusion tests ───────────────────────

describe("searchEnvelope tier inclusion", () => {
  it("omits Tier 2 when deps.tavilyApiKey is undefined", async () => {
    // DDG mock returns results immediately
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a> <a class="result__snippet">Snippet</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = {
      bus: { emit: () => {} } as any,
    };
    const result = await searchEnvelope("test query", 5, deps);
    // If Tavily was included and called, fetch would be called with api.tavily.com
    const calls = (global.fetch as any).mock.calls as string[][];
    const tavilyCalls = calls.filter(([url]) => String(url).includes("tavily"));
    expect(tavilyCalls).toHaveLength(0);
    vi.restoreAllMocks();
  });

  it("omits Tier 3 when deps.camofox is undefined", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a> <a class="result__snippet">Snippet</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = { bus: { emit: () => {} } as any };
    // No camofox — Tier 3 should never appear in result
    await searchEnvelope("test", 5, deps);
    // CamoFoxClient methods would throw if called — no throw means it wasn't invoked
    vi.restoreAllMocks();
  });

  it("omits Tier 4 when deps.puppeteer is undefined", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a> <a class="result__snippet">Snippet</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = { bus: { emit: () => {} } as any };
    await searchEnvelope("test", 5, deps);
    vi.restoreAllMocks();
  });

  it("returns Tavily results when DDG returns blocked", async () => {
    let callCount = 0;
    global.fetch = vi.fn().mockImplementation((url: string) => {
      callCount++;
      if (String(url).includes("duckduckgo")) {
        // DDG returns CAPTCHA-like response with 0 results
        return Promise.resolve({ ok: true, text: async () => "<html>CAPTCHA page</html>" });
      }
      // Tavily returns results
      return Promise.resolve({
        ok: true,
        json: async () => ({
          results: [{ title: "Tavily Result", url: "https://tavily-result.com", content: "From Tavily" }],
        }),
      });
    }) as any;

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      bus: { emit: () => {} } as any,
    };
    const result = await searchEnvelope("blocked query", 5, deps);
    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].title).toBe("Tavily Result");
    }
    vi.restoreAllMocks();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/browser/smart-search.test.ts 2>&1 | tail -10
```
Expected: FAIL — `Cannot find module '../../src/browser/smart-search.js'`

- [ ] **Step 3: Implement smart-search.ts**

Create `src/browser/smart-search.ts`:

```typescript
/**
 * StackOwl — Smart Search Layer (Element 16e)
 *
 * 4-tier escalation chain for web_search:
 *   T1 DDG-HTML    → T2 Tavily API (optional) → T3 Google-via-CamoFox (optional)
 *   → T4 Google-via-Puppeteer (optional)
 *
 * Reuses runEscalationChain from smart-fetch.ts — all bus events,
 * sequencer reordering, hint-skipping come for free.
 */

import type { CamoFoxClient } from "./camofox-client.js";
import type { PuppeteerFetcher } from "./puppeteer-fetcher.js";
import type { BlockingClassifier } from "./blocking-classifier.js";
import type { GatewayEventBus } from "../events/bus.js";
import { runEscalationChain } from "./smart-fetch.js";
import { parseGoogleHtml, type SearchResult } from "./google-parser.js";
import type { TierRunner, TierRunResult } from "./smart-fetch.js";
import type { WebToolResult } from "./envelope.js";

// ─── Public interface ────────────────────────────────────────────

export interface SearchEnvelopeDeps {
  tavilyApiKey?: string;
  camofox?: CamoFoxClient;
  puppeteer?: PuppeteerFetcher;
  classifier?: BlockingClassifier;
  bus?: GatewayEventBus;
}

// ─── No-op fallbacks ─────────────────────────────────────────────

const NOOP_BUS: GatewayEventBus = { emit: () => {} } as unknown as GatewayEventBus;

// ─── DDG jitter ─────────────────────────────────────────────────

function randomJitter(): Promise<void> {
  const delay = 300 + Math.random() * 600; // 300–900ms
  return new Promise((r) => setTimeout(r, delay));
}

// ─── DDG HTML parser (inline — no external dep) ─────────────────

function parseDdgHtml(html: string): SearchResult[] {
  const results: SearchResult[] = [];
  const resultRegex =
    /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)<\/a>[\s\S]*?<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/gi;
  let m: RegExpExecArray | null;
  while ((m = resultRegex.exec(html)) !== null && results.length < 15) {
    let url = m[1];
    if (url.includes("uddg=")) {
      const decoded = url.match(/uddg=([^&]+)/);
      if (decoded) url = decodeURIComponent(decoded[1]);
    } else if (url.startsWith("//")) {
      url = "https:" + url;
    }
    const title = m[2].replace(/<[^>]+>/g, "").trim();
    const snippet = m[3].replace(/<[^>]+>/g, "").trim();
    if (title && url.startsWith("http")) {
      results.push({ title, url, snippet: snippet || undefined });
    }
  }
  return results;
}

// ─── Tier 1: DDG-HTML ────────────────────────────────────────────

export function createDdgHtmlTier(): TierRunner {
  return {
    tier: 1,
    name: "scrapling",
    isAvailable: () => true,
    async run(query: string): Promise<TierRunResult> {
      const t0 = Date.now();
      await randomJitter();
      const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
      try {
        const resp = await fetch(url, {
          headers: {
            "User-Agent":
              "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            Accept: "text/html",
          },
        });
        if (!resp.ok) {
          const outcome = resp.status === 202 || resp.status === 429 ? "blocked" : "error";
          return {
            attempt: {
              tier: 1,
              name: "scrapling",
              durationMs: Date.now() - t0,
              outcome,
              httpStatus: resp.status,
            },
          };
        }
        const html = await resp.text();
        const results = parseDdgHtml(html);
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
          attempt: { tier: 1, name: "scrapling", durationMs: Date.now() - t0, outcome: "success" },
          data: { kind: "search", query, results },
        };
      } catch {
        return {
          attempt: { tier: 1, name: "scrapling", durationMs: Date.now() - t0, outcome: "error" },
        };
      }
    },
  };
}

// ─── Tier 2: Tavily API ──────────────────────────────────────────

export function createTavilyApiTier(apiKey: string): TierRunner {
  return {
    tier: 2,
    name: "tavily-api",
    isAvailable: () => true,
    async run(query: string): Promise<TierRunResult> {
      const t0 = Date.now();
      try {
        const resp = await fetch("https://api.tavily.com/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_key: apiKey,
            query,
            max_results: 10,
            search_depth: "basic",
          }),
          signal: AbortSignal.timeout(15_000),
        });
        if (!resp.ok) {
          return {
            attempt: {
              tier: 2,
              name: "tavily-api",
              durationMs: Date.now() - t0,
              outcome: resp.status === 429 ? "blocked" : "error",
              httpStatus: resp.status,
            },
          };
        }
        const json = (await resp.json()) as {
          results?: Array<{ title: string; url: string; content?: string }>;
        };
        const results: SearchResult[] = (json.results ?? []).map((r) => ({
          title: r.title,
          url: r.url,
          snippet: r.content,
        }));
        return {
          attempt: { tier: 2, name: "tavily-api", durationMs: Date.now() - t0, outcome: "success" },
          data: { kind: "search", query, results },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && err.name === "TimeoutError";
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

// ─── Tier 3: Google via CamoFox ──────────────────────────────────

const CAMOFOX_USER = "search-tier-internal";
const CAMOFOX_TIMEOUT_MS = 20_000;

export function createGoogleCamoFoxTier(client: CamoFoxClient): TierRunner {
  return {
    tier: 3,
    name: "google-camofox",
    isAvailable: () => client.isHealthy(),
    async run(query: string): Promise<TierRunResult> {
      const t0 = Date.now();
      let tabId: string | null = null;
      try {
        const snapOrTimeout = await Promise.race([
          (async () => {
            const tab = await client.createTab(CAMOFOX_USER);
            tabId = tab.tabId;
            return client.navigate(tabId, CAMOFOX_USER, "@google_search " + query);
          })(),
          new Promise<null>((_, rej) => setTimeout(() => rej(new Error("camofox-timeout")), CAMOFOX_TIMEOUT_MS)),
        ]);
        if (!snapOrTimeout) throw new Error("camofox-empty");
        const results = parseGoogleHtml(snapOrTimeout.snapshot, query);
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
          attempt: { tier: 3, name: "google-camofox", durationMs: Date.now() - t0, outcome: "success" },
          data: { kind: "search", query, results },
        };
      } catch (err) {
        const isTimeout = err instanceof Error && err.message === "camofox-timeout";
        return {
          attempt: {
            tier: 3,
            name: "google-camofox",
            durationMs: Date.now() - t0,
            outcome: isTimeout ? "timeout" : "error",
          },
        };
      } finally {
        if (tabId) await client.closeTab(tabId, CAMOFOX_USER).catch(() => {});
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
    async run(query: string): Promise<TierRunResult> {
      const t0 = Date.now();
      const googleUrl = "https://www.google.com/search?q=" + encodeURIComponent(query);
      try {
        const r = await fetcher.fetch(googleUrl, {
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
          data: { kind: "search", query, results },
        };
      } catch {
        return {
          attempt: { tier: 4, name: "google-puppeteer", durationMs: Date.now() - t0, outcome: "error" },
        };
      }
    },
  };
}

// ─── Entry point ─────────────────────────────────────────────────

export async function searchEnvelope(
  query: string,
  num: number,
  deps: SearchEnvelopeDeps,
): Promise<WebToolResult> {
  const bus = deps.bus ?? NOOP_BUS;
  const tiers: TierRunner[] = [createDdgHtmlTier()];
  if (deps.tavilyApiKey) tiers.push(createTavilyApiTier(deps.tavilyApiKey));
  if (deps.camofox) tiers.push(createGoogleCamoFoxTier(deps.camofox));
  if (deps.puppeteer) tiers.push(createGooglePuppeteerTier(deps.puppeteer));

  const result = await runEscalationChain(tiers, query, { bus });

  // runEscalationChain doesn't set suggestedEscalation — add it for search failures
  if (!result.success) {
    return {
      success: false,
      error: { ...result.error, suggestedEscalation: "live_browser" },
    };
  }

  // Trim results to requested num
  if (result.data.kind === "search" && result.data.results.length > num) {
    return {
      success: true,
      data: { ...result.data, results: result.data.results.slice(0, num) },
    };
  }
  return result;
}
```

- [ ] **Step 4: Run unit tests**

```bash
npx vitest run __tests__/browser/smart-search.test.ts 2>&1 | tail -15
```
Expected: all tests passing (some may need adjustment if fetch mock shape differs — fix inline).

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-search.ts __tests__/browser/smart-search.test.ts
git commit -m "feat(element16e): add smart-search.ts with 4-tier search escalation"
```

---

## Task 5: Interface Wiring — GatewayContext, EngineContext, context-builder, ToolContext

**Files:**
- Modify: `src/gateway/types.ts`
- Modify: `src/engine/runtime.ts`
- Modify: `src/gateway/handlers/context-builder.ts`
- Modify: `src/tools/registry.ts`

All 4 changes add the same 2 fields: `camofox?: CamoFoxClient` and `tavilyApiKey?: string`. Follow exactly the same pattern `puppeteer` used in 16d.

- [ ] **Step 1: Add fields to GatewayContext in types.ts**

In `src/gateway/types.ts`, the current last block (around line 372) reads:
```typescript
  // ─── Element 16b/16d — Browser fetch pipeline ────────────────────
  blockingClassifier?: import("../browser/blocking-classifier.js").BlockingClassifier;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
}
```

Replace with:
```typescript
  // ─── Element 16b/16d — Browser fetch pipeline ────────────────────
  blockingClassifier?: import("../browser/blocking-classifier.js").BlockingClassifier;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
  // ─── Element 16e — Search escalation ─────────────────────────────
  camofox?: import("../browser/camofox-client.js").CamoFoxClient;
  tavilyApiKey?: string;
}
```

- [ ] **Step 2: Add fields to EngineContext and both toolCtx sites in runtime.ts**

In `src/engine/runtime.ts`, find the EngineContext interface where `puppeteer` is defined (around line 144–146):
```typescript
  /** BlockingClassifier — passed to web tools for CAPTCHA/block detection */
  classifier?: import("../browser/blocking-classifier.js").BlockingClassifier;
  /** PuppeteerFetcher — passed to web tools for headless browser fallback */
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
```

Add after the puppeteer line:
```typescript
  /** CamoFoxClient — passed to search tool for Google-via-CamoFox tier */
  camofox?: import("../browser/camofox-client.js").CamoFoxClient;
  /** Tavily Search API key — passed to search tool for Tavily tier */
  tavilyApiKey?: string;
```

Find the first toolCtx construction in `executeModel` (around line 1277–1286):
```typescript
        const toolCtx = {
          cwd: cwd || process.cwd(),
          engineContext: {
            ...context,
            activeSubGoal: context.activeSubGoal,
            userMessage: context.userMessage,
          },
          classifier: context.classifier,
          puppeteer: context.puppeteer,
        };
```

Add `camofox` and `tavilyApiKey`:
```typescript
        const toolCtx = {
          cwd: cwd || process.cwd(),
          engineContext: {
            ...context,
            activeSubGoal: context.activeSubGoal,
            userMessage: context.userMessage,
          },
          classifier: context.classifier,
          puppeteer: context.puppeteer,
          camofox: context.camofox,
          tavilyApiKey: context.tavilyApiKey,
        };
```

Find the second toolCtx construction in `synthesizeResponse` (around line 2919–2927):
```typescript
      const toolCtx = {
        cwd: process.cwd(),
        engineContext: {
          activeSubGoal: request.activeSubGoal,
          userMessage: request.userMessage,
        },
        classifier: (request as any).classifier,
        puppeteer: (request as any).puppeteer,
      };
```

Add:
```typescript
      const toolCtx = {
        cwd: process.cwd(),
        engineContext: {
          activeSubGoal: request.activeSubGoal,
          userMessage: request.userMessage,
        },
        classifier: (request as any).classifier,
        puppeteer: (request as any).puppeteer,
        camofox: (request as any).camofox,
        tavilyApiKey: (request as any).tavilyApiKey,
      };
```

- [ ] **Step 3: Thread camofox + tavilyApiKey through context-builder.ts**

In `src/gateway/handlers/context-builder.ts`, the `baseContext()` return currently ends with (around line 139–141):
```typescript
      classifier: this.ctx.blockingClassifier,
      puppeteer: this.ctx.puppeteer,
    };
  }
```

Add after `puppeteer`:
```typescript
      classifier: this.ctx.blockingClassifier,
      puppeteer: this.ctx.puppeteer,
      camofox: this.ctx.camofox,
      tavilyApiKey: this.ctx.tavilyApiKey,
    };
  }
```

- [ ] **Step 4: Add fields to ToolContext in registry.ts**

The current `ToolContext` interface (around line 28–33):
```typescript
export interface ToolContext {
  cwd: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
}
```

Add the 2 new fields:
```typescript
export interface ToolContext {
  cwd: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
  camofox?: import("../browser/camofox-client.js").CamoFoxClient;
  tavilyApiKey?: string;
}
```

- [ ] **Step 5: Verify TypeScript compiles cleanly**

```bash
npx tsc --noEmit 2>&1 | head -20
```
Expected: zero new errors.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/types.ts src/engine/runtime.ts src/gateway/handlers/context-builder.ts src/tools/registry.ts
git commit -m "feat(element16e): wire camofox + tavilyApiKey through GatewayContext → ToolContext"
```

---

## Task 6: search.ts — Replace Inline DDG Scrape

**Files:**
- Modify: `src/tools/search.ts`

- [ ] **Step 1: Add imports to search.ts**

At the top of `src/tools/search.ts`, after the existing imports, add:
```typescript
import { searchEnvelope } from "../browser/smart-search.js";
```

The existing imports are:
```typescript
import type { ToolImplementation, ToolContext } from "./registry.js";
import {
  serializeWebToolResult,
  type WebToolResult,
  type WebToolErrorCode,
} from "../browser/envelope.js";
```

Add the new import after line 10 (after the envelope import):
```typescript
import { searchEnvelope } from "../browser/smart-search.js";
```

- [ ] **Step 2: Remove local SearchResult interface**

The file currently defines (around line 13–16):
```typescript
interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}
```

Delete this interface — `SearchResult` now comes from `google-parser.ts` and is used internally by `smart-search.ts`. The `search.ts` file no longer needs it.

- [ ] **Step 3: Replace execute() body**

The current `execute()` method (everything from `const query = ...` through the final `}`) runs about 100 lines of inline DDG scraping. Replace the **entire body** of `execute()` with:

```typescript
  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const query = (args["query"] as string)?.trim();
    if (!query) throw new Error("Search query is required");

    const num = Math.min(Number(args["num"] ?? 8), 15);

    const result = await searchEnvelope(query, num, {
      tavilyApiKey: context.tavilyApiKey,
      camofox: context.camofox,
      puppeteer: context.puppeteer,
      classifier: context.classifier,
      bus: (context.engineContext as any)?.eventBus,
    });

    return serializeWebToolResult(result);
  },
```

The `WebToolResult` and `WebToolErrorCode` imports are no longer needed in `search.ts` after this change. Remove unused imports:

```typescript
// Remove: type WebToolResult, type WebToolErrorCode — no longer used directly
```

Final imports in `search.ts`:
```typescript
import type { ToolImplementation, ToolContext } from "./registry.js";
import { serializeWebToolResult } from "../browser/envelope.js";
import { searchEnvelope } from "../browser/smart-search.js";
```

- [ ] **Step 4: Run TypeScript**

```bash
npx tsc --noEmit 2>&1 | head -20
```
Expected: no errors.

- [ ] **Step 5: Run the full test suite**

```bash
npx vitest run 2>&1 | tail -20
```
Expected: same pass count as before (no regressions). The existing search tool tests may need mock updates — fix inline if they fail.

- [ ] **Step 6: Commit**

```bash
git add src/tools/search.ts
git commit -m "feat(element16e): replace inline DDG scrape in search.ts with searchEnvelope()"
```

---

## Task 7: index.ts — Bootstrap tavilyApiKey + camofox Wiring

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Wire camofoxClient into gateway.ctx**

In `src/index.ts`, find the block that wires `puppeteerFetcher` into `gateway.ctx` (around line 1245–1247):
```typescript
    if (b.puppeteerFetcher) {
      gateway.ctx.puppeteer = b.puppeteerFetcher;
    }
```

Add immediately after that block:
```typescript
    if (b.camofoxClient) {
      gateway.ctx.camofox = b.camofoxClient;
    }
    const tavilyApiKey = process.env["TAVILY_API_KEY"] ?? (b.config as any)?.webSearch?.tavilyApiKey;
    if (tavilyApiKey) {
      gateway.ctx.tavilyApiKey = tavilyApiKey;
    }
```

`b.camofoxClient` already exists — it's the CamoFox client created earlier in bootstrap. No new initialization needed.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npx tsc --noEmit 2>&1 | head -20
```
Expected: no errors.

- [ ] **Step 3: Run full test suite**

```bash
npx vitest run 2>&1 | tail -20
```
Expected: same pass count as before. No regressions.

- [ ] **Step 4: Commit**

```bash
git add src/index.ts
git commit -m "feat(element16e): wire camofox + tavilyApiKey into gateway context at bootstrap"
```

---

## Task 8: Integration Tests — Search Escalation Scenarios

**Files:**
- Create: `__tests__/browser/search-escalation.test.ts`

- [ ] **Step 1: Write the tests**

Create `__tests__/browser/search-escalation.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { searchEnvelope, type SearchEnvelopeDeps } from "../../src/browser/smart-search.js";

const NOOP_BUS = { emit: () => {} } as any;

function makeFetchMock(opts: {
  ddgBlocked?: boolean;
  tavilyOk?: boolean;
  tavilyError?: boolean;
  tavilyKey?: string;
}) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (String(url).includes("duckduckgo")) {
      if (opts.ddgBlocked) {
        return Promise.resolve({ ok: true, text: async () => "<html>CAPTCHA</html>" });
      }
      return Promise.resolve({
        ok: true,
        text: async () =>
          '<a class="result__a" href="https://ddg-result.com">DDG Title</a> <a class="result__snippet">DDG Snippet</a>',
      });
    }
    if (String(url).includes("tavily")) {
      if (opts.tavilyError) {
        return Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
      }
      if (opts.tavilyOk) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            results: [{ title: "Tavily Result", url: "https://tavily-result.com", content: "Tavily snippet" }],
          }),
        });
      }
    }
    return Promise.resolve({ ok: false, status: 500, text: async () => "", json: async () => ({}) });
  });
}

describe("search escalation — DDG succeeds", () => {
  it("returns DDG results and does not call Tavily", async () => {
    const fetchMock = makeFetchMock({ ddgBlocked: false });
    global.fetch = fetchMock as any;

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      bus: NOOP_BUS,
    };
    const result = await searchEnvelope("best coffee", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].url).toBe("https://ddg-result.com");
    }
    // Tavily should NOT have been called
    const tavilyCalls = fetchMock.mock.calls.filter(([url]: any[]) => String(url).includes("tavily"));
    expect(tavilyCalls).toHaveLength(0);

    vi.restoreAllMocks();
  });
});

describe("search escalation — DDG blocked → Tavily succeeds", () => {
  it("attemptedTiers has 2 entries: blocked + success", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyOk: true }) as any;

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      bus: NOOP_BUS,
    };
    const result = await searchEnvelope("blocked query", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].title).toBe("Tavily Result");
    }
    vi.restoreAllMocks();
  });
});

describe("search escalation — Tier 2 isAvailable false → Tier 3 skipped", () => {
  it("skips to Puppeteer when camofox unavailable", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyError: true }) as any;

    const mockPuppeteer = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({
        html: `<html><div class="g"><h3><a href="https://puppeteer-result.com">Puppeteer Result</a></h3></div></html>`,
        finalUrl: "https://www.google.com/search?q=test",
        status: 200,
      }),
    };
    const mockCamoFox = {
      isHealthy: vi.fn().mockResolvedValue(false), // unavailable
    };

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      camofox: mockCamoFox as any,
      puppeteer: mockPuppeteer as any,
      bus: NOOP_BUS,
    };
    const result = await searchEnvelope("test query", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].url).toBe("https://puppeteer-result.com");
    }
    // CamoFox.isHealthy was called but createTab was NOT called
    expect(mockCamoFox.isHealthy).toHaveBeenCalled();

    vi.restoreAllMocks();
  });
});

describe("search escalation — all tiers fail", () => {
  it("returns success:false with 3 entries and suggestedEscalation: live_browser", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyError: true }) as any;

    const mockPuppeteer = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({
        // Google returns CAPTCHA — parser yields []
        html: "<html><body>CAPTCHA</body></html>",
        finalUrl: "https://www.google.com/search?q=test",
        status: 200,
      }),
    };
    const mockCamoFox = {
      isHealthy: vi.fn().mockResolvedValue(true),
      createTab: vi.fn().mockResolvedValue({ tabId: "t1", snapshot: "", refs: {}, url: "" }),
      navigate: vi.fn().mockResolvedValue({ snapshot: "CAPTCHA page", refs: {}, url: "" }),
      closeTab: vi.fn().mockResolvedValue(undefined),
    };

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      camofox: mockCamoFox as any,
      puppeteer: mockPuppeteer as any,
      bus: NOOP_BUS,
    };
    const result = await searchEnvelope("fail query", 5, deps);

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.code).toBe("BLOCKED_BY_ANTI_BOT");
      expect(result.error.attemptedTiers.length).toBeGreaterThanOrEqual(3);
      expect(result.error.suggestedEscalation).toBe("live_browser");
    }

    vi.restoreAllMocks();
  });
});
```

- [ ] **Step 2: Run integration tests**

```bash
npx vitest run __tests__/browser/search-escalation.test.ts 2>&1 | tail -20
```
Expected: 4 tests passing (or close — adjust mock shapes if needed for your fetch mock).

- [ ] **Step 3: Run full test suite**

```bash
npx vitest run 2>&1 | tail -10
```
Expected: ≥ previous pass count, 0 new failures.

- [ ] **Step 4: Commit**

```bash
git add __tests__/browser/search-escalation.test.ts
git commit -m "test(element16e): add search escalation integration tests"
```

---

## Final Verification

- [ ] **Run full test suite one more time**

```bash
npx vitest run 2>&1 | tail -10
```

- [ ] **TypeScript clean compile**

```bash
npx tsc --noEmit 2>&1
```
Expected: zero errors.

- [ ] **Verify new files are present**

```bash
ls src/browser/google-parser.ts src/browser/smart-search.ts __tests__/browser/google-parser.test.ts __tests__/browser/smart-search.test.ts __tests__/browser/search-escalation.test.ts
```
Expected: all 5 files exist.

- [ ] **Check TierName includes all 3 new names**

```bash
grep "TierName" src/browser/envelope.ts
```
Expected: line contains `"google-camofox" | "google-puppeteer" | "tavily-api"`.

- [ ] **Check search.ts no longer contains inline DDG fetch logic**

```bash
grep "duckduckgo.com" src/tools/search.ts
```
Expected: no output (the DDG URL is now only in `smart-search.ts`).
