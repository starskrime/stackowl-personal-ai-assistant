# Element 16e — Web Search Escalation (Phase B)

**Date:** 2026-05-07  
**Status:** Approved (revised 2026-05-07 after squad research review)  
**Builds on:** Element 16c (web_fetch envelope), Element 16d (Puppeteer tier, wiring chain)

---

## Problem

`web_search` has a single DDG-HTML tier. When DuckDuckGo blocks (CAPTCHA, rate-limit, anti-bot), the tool returns `BLOCKED_BY_ANTI_BOT` immediately. No escalation fires. The assistant tells the user it's blocked before trying anything else.

`web_fetch` solved the same problem in 16d with a 3-tier `runEscalationChain`. This element applies the same pattern to the search path.

**Squad research findings (2026-05-07) that revised the original design:**
1. **CamoFox is 100% detected by Google as of Q3 2025** (GitHub Issue #388) — C++-level TLS/canvas fingerprint validation. CamoFox remains useful for non-Google sites but cannot reliably fetch Google SERPs. Demoted from T2 to T3.
2. **Google requires JavaScript execution to populate `div.g`** (January 2025 enforcement). Raw HTML returned immediately after `domcontentloaded` is incomplete. Puppeteer tier must wait for result selector before calling `page.content()`.
3. **Tavily Search API** offers 1,000 free queries/month, no credit card, TypeScript SDK, LLM-ready structured output — ideal as an optional T2 that requires zero scraping.

---

## Locked Decisions (non-negotiable)

- **Tier chain:** DDG-HTML (T1) → Tavily API (T2, optional) → Google-via-CamoFox (T3, optional) → Google-via-Puppeteer (T4, optional)
- **T2 Tavily gating:** only pushed into `tiers[]` when `deps.tavilyApiKey` is set — zero-config deployments skip it
- **Parsing strategy:** Option A+ — ranked HTML selector fallback (JSON-LD → `div.g h3 a` → `[data-hveid] h3 a`). `data-sokoban-container` removed (internal/undocumented per squad research). JSON-LD `@type` must match both scalar and array forms.
- **Google JS wait:** Puppeteer tier waits for `div.g` selector (up to 5s) before `page.content()` — ensures JS-rendered results are present
- **DDG jitter:** 300–900ms random delay in `createDdgHtmlTier.run()` — reduces DDG 202 block rate (datacenter IP success: 61% without jitter, 94% with residential; jitter improves datacenter headroom)
- **No hardcoded keyword arrays** — BlockingClassifier handles all blocking detection
- **live_browser stays out** — `suggestedEscalation: "live_browser"` only when all tiers fail
- **Channel parity** — web_search must work identically across CLI/Telegram/Slack
- **File budget:** max 2 new `src/` files, net delta ≤ 0

---

## Architecture

`web_search` currently runs a single inline DDG scrape with a `try/catch`. The refactor replaces that with `searchEnvelope(query, num, deps)` which builds a `tiers[]` array and calls `runEscalationChain()` — the same function used by `web_fetch`. All bus events, sequencer reordering, hint-skipping, and envelope building come for free.

```
search.ts execute()
  → searchEnvelope(query, num, deps)
    → tiers = [
        createDdgHtmlTier(),                              // T1: always
        createTavilyApiTier(apiKey),                      // T2: if deps.tavilyApiKey
        createGoogleCamoFoxTier(camofox),                 // T3: if deps.camofox
        createGooglePuppeteerTier(puppeteer),             // T4: if deps.puppeteer
      ]
    → runEscalationChain(tiers, query, ctx)
    → first successful tier returns WebToolResult { success: true, data: { kind: "search", ... } }
```

---

## Components

### New Files (2)

#### `src/browser/google-parser.ts`
Single responsibility: parse a Google SERP HTML string into `SearchResult[]`.

```typescript
export interface SearchResult {
  title: string;
  url: string;
  snippet?: string;
}

export function parseGoogleHtml(html: string, query: string): SearchResult[]
```

**Ranked fallback strategy (in order):**
1. **JSON-LD** — `<script type="application/ld+json">` blocks where `@type` is `"SearchResultsPage"` or `"ItemList"` (check both scalar string and array). Most stable — Google preserves this for crawlers.
2. **`div.g h3 a[href]`** — classic result container. `href` is a direct URL (not a redirect). Snippet from sibling `span`.
3. **`[data-hveid] h3 a[href]`** — attribute-based fallback. `data-hveid` is a result-position tracking attribute that has remained stable across Google redesigns.

Returns `[]` if all three yield zero results — never throws. Callers treat `[]` + `BlockingClassifier` signal as a block.

**Note:** `data-sokoban-container` removed — squad research found no public documentation; likely internal Google tooling attribute not reliable across environments.

#### `src/browser/smart-search.ts`
Tier factories + `searchEnvelope()` entry point. Parallel to `smart-fetch.ts`.

```typescript
export interface SearchEnvelopeDeps {
  tavilyApiKey?: string;
  camofox?: CamoFoxClient;
  puppeteer?: PuppeteerFetcher;
  classifier?: BlockingClassifier;
  bus?: GatewayEventBus;
}

export function createDdgHtmlTier(): TierRunner
export function createTavilyApiTier(apiKey: string): TierRunner
export function createGoogleCamoFoxTier(client: CamoFoxClient): TierRunner
export function createGooglePuppeteerTier(fetcher: PuppeteerFetcher): TierRunner

export async function searchEnvelope(
  query: string,
  num: number,
  deps: SearchEnvelopeDeps,
): Promise<WebToolResult>
```

**Tier specifications:**

| Factory | tier | name | isAvailable | run |
|---------|------|------|-------------|-----|
| `createDdgHtmlTier` | 1 | `"scrapling"` | always true | fetch `https://html.duckduckgo.com/html/?q=<query>` + existing DDG parser; random 300–900ms jitter before request |
| `createTavilyApiTier` | 2 | `"tavily-api"` | always true (key checked at construction) | POST `https://api.tavily.com/search` with `{ query, max_results: num }` → map `results[]` to `SearchResult[]` |
| `createGoogleCamoFoxTier` | 3 | `"google-camofox"` | `client.isHealthy()` | `client.navigate(INTERNAL_SESSION, "@google_search", query)` → `snapshot()` → `parseGoogleHtml()` |
| `createGooglePuppeteerTier` | 4 | `"google-puppeteer"` | `fetcher.probe()` | `fetcher.fetch("https://www.google.com/search?q=" + encodeURIComponent(query))` → wait for `div.g` (5s) → `parseGoogleHtml()` |

**Tavily API call format:**
```typescript
const resp = await fetch("https://api.tavily.com/search", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ api_key: apiKey, query, max_results: num, search_depth: "basic" }),
});
const json = await resp.json();
// json.results: Array<{ title, url, content, score }>
return json.results.map((r: any): SearchResult => ({ title: r.title, url: r.url, snippet: r.content }));
```

**CamoFox session:** Uses fixed internal session ID `"search-tier-internal"`. Tab is created, snapshot taken, tab closed within `run()`. No user session required.

**Puppeteer Google wait strategy:**
```typescript
// Inside createGooglePuppeteerTier.run():
const { html } = await fetcher.fetch(url);
// fetcher already ran page.goto with domcontentloaded.
// For Google SERPs, we need an additional wait.
// PuppeteerFetcher.fetch() must accept an optional waitForSelector param:
const { html } = await fetcher.fetch(url, { waitForSelector: "div.g", waitForSelectorTimeout: 5000 });
```
This requires a minor extension to `PuppeteerFetcher.fetch()` — add optional `waitForSelector?: string` and `waitForSelectorTimeout?: number` to its options. If the selector doesn't appear within the timeout, `fetch()` proceeds with whatever HTML is available (not an error — may return `[]` from parser).

**`searchEnvelope()` flow:**
1. Build `tiers[]`:
   - Always push `createDdgHtmlTier()`
   - If `deps.tavilyApiKey` → push `createTavilyApiTier(deps.tavilyApiKey)`
   - If `deps.camofox` → push `createGoogleCamoFoxTier(deps.camofox)`
   - If `deps.puppeteer` → push `createGooglePuppeteerTier(deps.puppeteer)`
2. Call `runEscalationChain(tiers, query, { bus: deps.bus })`
3. Return `WebToolResult` directly

### Modified Files (5 + PuppeteerFetcher extension)

#### `src/browser/envelope.ts`
Add to `TierName` union and `NAMES` Set:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer" | "google-camofox" | "google-puppeteer" | "tavily-api";
const NAMES = new Set<TierName>([..., "google-camofox", "google-puppeteer", "tavily-api"]);
```

#### `src/runtime/availability.ts`
Add to `BackendName` union and `emptyMap()`:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer" | "google-camofox" | "google-puppeteer" | "tavily-api";
// emptyMap() gains: "google-camofox": emptyStatus(), "google-puppeteer": emptyStatus(), "tavily-api": emptyStatus()
```

#### `src/browser/puppeteer-fetcher.ts`
Extend `PuppeteerFetchOptions` and `fetch()` signature:
```typescript
export interface PuppeteerFetchOptions {
  timeoutMs?: number;
  waitForSelector?: string;          // ← new
  waitForSelectorTimeout?: number;   // ← new (default: 5000)
}

async fetch(url: string, opts: PuppeteerFetchOptions = {}): Promise<PuppeteerFetchResult> {
  // ... existing goto logic with domcontentloaded ...
  if (opts.waitForSelector) {
    try {
      await page.waitForSelector(opts.waitForSelector, { timeout: opts.waitForSelectorTimeout ?? 5000 });
    } catch {
      // selector not found — proceed with available HTML, parser will return []
    }
  }
  const html = await page.content();
  // ...
}
```

#### `src/tools/search.ts`
Replace the inline DDG scrape + try/catch block with:
```typescript
const result = await searchEnvelope(query, num, {
  tavilyApiKey: context.tavilyApiKey,
  camofox: context.camofox,
  puppeteer: context.puppeteer,
  classifier: context.classifier,
  bus: (context.engineContext as any)?.eventBus,
});
return serializeWebToolResult(result);
```

#### `src/index.ts`
Wire `camofoxClient` and `tavilyApiKey` into `gateway.ctx`:
```typescript
// In the intelligence block, after BlockingClassifier instantiation:
if (b.camofoxClient) {
  gateway.ctx.camofox = b.camofoxClient;
}
const tavilyKey = process.env.TAVILY_API_KEY ?? b.config?.webSearch?.tavilyApiKey;
if (tavilyKey) {
  gateway.ctx.tavilyApiKey = tavilyKey;
}
```

---

## Data Flow

### Happy path (DDG succeeds)
```
Tier 1 runs → DDG HTML (with jitter) → parseResults() → results[] → WebToolResult { success: true }
```

### DDG blocked → Tavily API (if key configured)
```
Tier 1 → BlockingClassifier → outcome: "blocked"
Tier 2 → POST api.tavily.com/search → json.results[] → SearchResult[] → WebToolResult { success: true }
```

### Tavily unavailable → Google via CamoFox
```
Tier 2 not in tiers[] (no tavilyApiKey) or outcome: "error"
Tier 3 → CamoFoxClient.navigate("search-tier-internal", "@google_search", query)
        → snapshot() html → parseGoogleHtml()
        → JSON-LD match → SearchResult[] → WebToolResult { success: true }
```

### CamoFox detected → Puppeteer
```
Tier 3 → BlockingClassifier → outcome: "blocked" (Google CAPTCHA / detection)
Tier 4 → PuppeteerFetcher.fetch(googleUrl, { waitForSelector: "div.g" })
        → html (after JS execution) → parseGoogleHtml() → results → WebToolResult { success: true }
```

### All tiers fail
```
WebToolResult {
  success: false,
  error: {
    code: "BLOCKED_BY_ANTI_BOT",
    attemptedTiers: [
      { tier:1, name:"scrapling", outcome:"blocked" },
      { tier:2, name:"tavily-api", outcome:"error" },
      { tier:3, name:"google-camofox", outcome:"blocked" },
      { tier:4, name:"google-puppeteer", outcome:"blocked" }
    ],
    suggestedEscalation: "live_browser"
  }
}
```

### Zero results (not blocked)
`parseGoogleHtml` returns `[]` → BlockingClassifier checks HTML → if not blocked signal → return `WebToolResult { success: true, data: { kind: "search", results: [] } }`. LLM sees empty results, not a block.

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| DDG returns CAPTCHA HTML | BlockingClassifier → `outcome: "blocked"` → escalate |
| DDG returns 202 (rate-limited) | `outcome: "blocked"` → escalate |
| Tavily API returns non-200 | `outcome: "error"` → escalate |
| Tavily API key invalid / quota exceeded | `outcome: "error"` → escalate |
| CamoFox `navigate()` throws | `outcome: "error"` → escalate |
| Puppeteer `fetch()` times out | `outcome: "timeout"` → escalate |
| Puppeteer `waitForSelector` times out | proceed with partial HTML; parser returns `[]`; BlockingClassifier decides outcome |
| `parseGoogleHtml` returns `[]`, not blocked | `success: true, results: []` — genuine empty SERP |
| `parseGoogleHtml` returns `[]`, blocked | BlockingClassifier → `outcome: "blocked"` → escalate |
| All tiers exhausted | `BLOCKED_BY_ANTI_BOT` envelope, `suggestedEscalation: "live_browser"` |

---

## Wiring Chain

```
index.ts bootstrap()
  → b.camofoxClient (already exists)
  → gateway.ctx.camofox = b.camofoxClient           ← new
  → gateway.ctx.tavilyApiKey = tavilyKey             ← new

gateway/types.ts GatewayContext
  → camofox?: CamoFoxClient                         ← new
  → tavilyApiKey?: string                            ← new

context-builder.ts baseContext()
  → camofox: this.ctx.camofox                        ← new
  → tavilyApiKey: this.ctx.tavilyApiKey              ← new

runtime.ts EngineContext
  → camofox?: CamoFoxClient                         ← new
  → tavilyApiKey?: string                            ← new

runtime.ts toolCtx (×2)
  → camofox: context.camofox                         ← new
  → tavilyApiKey: context.tavilyApiKey               ← new

registry.ts ToolContext
  → camofox?: CamoFoxClient                         ← new
  → tavilyApiKey?: string                            ← new

search.ts execute()
  → context.camofox, context.tavilyApiKey  (first-class ToolContext fields)
```

Note: `puppeteer` and `classifier` are already on `ToolContext` from 16d — no additional wiring needed for those.

**Config source for `tavilyApiKey`:** `TAVILY_API_KEY` env var OR `config.webSearch.tavilyApiKey` string in `stackowl.config.json`. Env var takes precedence. Neither is required — all tiers are optional.

---

## Testing

### `__tests__/browser/google-parser.test.ts` (new)
- Parses JSON-LD fixture with scalar `@type: "SearchResultsPage"` → correct `{ title, url, snippet }[]`
- Parses JSON-LD fixture with array `@type: ["SearchResultsPage", "ItemList"]` → same result
- Falls back to `div.g h3 a` when no JSON-LD
- Falls back to `[data-hveid] h3 a` as third option when no `div.g`
- Returns `[]` on CAPTCHA HTML — no throw
- Decodes percent-encoded URLs in href attributes

### `__tests__/browser/smart-search.test.ts` (new)
- `createDdgHtmlTier()` → `tier:1, name:"scrapling"`
- `createTavilyApiTier(key)` → `tier:2, name:"tavily-api"`, maps `json.results` to `SearchResult[]`
- `createGoogleCamoFoxTier()` → `tier:3, name:"google-camofox"`, `isAvailable()` delegates to `client.isHealthy()`
- `createGooglePuppeteerTier()` → `tier:4, name:"google-puppeteer"`, `isAvailable()` delegates to `fetcher.probe()`
- `searchEnvelope()` returns Tavily results when DDG mock returns blocked
- `searchEnvelope()` omits Tier 2 when `deps.tavilyApiKey` is undefined
- `searchEnvelope()` omits Tier 3 when `deps.camofox` is undefined
- `searchEnvelope()` omits Tier 4 when `deps.puppeteer` is undefined

### `__tests__/browser/search-escalation.test.ts` (new)
- DDG blocked → Tavily succeeds → `attemptedTiers` has 2 entries (blocked + success)
- DDG blocked, no Tavily key, CamoFox succeeds → `attemptedTiers` has 2 entries
- All 4 blocked → `success: false`, 4 attempts, `suggestedEscalation: "live_browser"`
- Tier 3 `isAvailable()` false → skipped, Tier 4 fires directly
- Tier 1 succeeds → Tiers 2, 3, 4 never called

### Updated: existing `search.ts` tests
- Pass mock `SearchEnvelopeDeps` (including `tavilyApiKey`) to refactored `execute()` call
- No tests deleted

---

## File Delta

| File | Action |
|------|--------|
| `src/browser/google-parser.ts` | **NEW** |
| `src/browser/smart-search.ts` | **NEW** |
| `src/browser/envelope.ts` | modify — add 3 TierName values (`"google-camofox"`, `"google-puppeteer"`, `"tavily-api"`) |
| `src/runtime/availability.ts` | modify — add 3 BackendName values |
| `src/browser/puppeteer-fetcher.ts` | modify — add `waitForSelector` option to `fetch()` |
| `src/tools/search.ts` | modify — replace inline scrape with `searchEnvelope()` |
| `src/gateway/types.ts` | modify — add `camofox?` and `tavilyApiKey?` to GatewayContext |
| `src/engine/runtime.ts` | modify — add `camofox?` and `tavilyApiKey?` to EngineContext + 2 toolCtx sites |
| `src/gateway/handlers/context-builder.ts` | modify — thread `camofox` and `tavilyApiKey` in `baseContext()` |
| `src/tools/registry.ts` | modify — add `camofox?` and `tavilyApiKey?` to ToolContext |
| `src/index.ts` | modify — wire `camofoxClient` and `tavilyApiKey` to `gateway.ctx` |

**Net: +2 new files, 0 deleted. Within budget.**

---

## Out of Scope

- Brave Search revival — removed in 16c, not returning
- SerpAPI / Serper.dev / Exa.ai — additional paid APIs beyond Tavily (one is sufficient)
- `page.$$eval()` live DOM parsing — requires PuppeteerFetcher architectural refactor, deferred
- Search result caching / deduplication across tiers
- Tier 3 `@google_search` macro replacement with direct URL navigation (macro is sufficient)
- web_search Tier 5 (live_browser direct call) — live_browser stays out of the automated chain
- Nodriver (Python CDP, successor to undetected-chromedriver) — most effective vs Google but Python subprocess integration deferred to Phase C
- Firecrawl self-hosted search — Docker dependency, deferred to Phase D
- Circuit breaker per-tier (track consecutive failures to short-circuit `isAvailable()`) — valuable but out of scope; add to Element 16f backlog
- Residential proxy rotation for DDG — improves T1 success 61%→94% but adds infrastructure dependency
