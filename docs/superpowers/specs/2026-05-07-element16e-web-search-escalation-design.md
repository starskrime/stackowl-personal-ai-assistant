# Element 16e — Web Search Escalation (Phase B)

**Date:** 2026-05-07  
**Status:** Approved  
**Builds on:** Element 16c (web_fetch envelope), Element 16d (Puppeteer tier, wiring chain)

---

## Problem

`web_search` has a single DDG-HTML tier. When DuckDuckGo blocks (CAPTCHA, rate-limit, anti-bot), the tool returns `BLOCKED_BY_ANTI_BOT` immediately. No escalation fires. The assistant tells the user it's blocked before trying anything else.

`web_fetch` solved the same problem in 16d with a 3-tier `runEscalationChain`. This element applies the same pattern to the search path.

---

## Locked Decisions (non-negotiable)

- **Tier chain:** DDG-HTML (T1) → Google-via-CamoFox (T2) → Google-via-Puppeteer (T3)
- **Parsing strategy:** Option A+ — ranked HTML selector fallback (JSON-LD → `div.g h3 a` → `[data-sokoban-container]`). No `page.$$eval()` refactor — PuppeteerFetcher closes the page before returning html.
- **No new search engine APIs** — no Brave, no SerpAPI, no Serper.dev
- **live_browser stays out** — `suggestedEscalation: "live_browser"` only when all 3 tiers fail
- **No hardcoded keyword arrays** — BlockingClassifier handles all blocking detection
- **Channel parity** — web_search must work identically across CLI/Telegram/Slack
- **File budget:** max 2 new `src/` files, net delta ≤ 0

---

## Architecture

`web_search` currently runs a single inline DDG scrape with a `try/catch`. The refactor replaces that with `searchEnvelope(query, num, deps)` which builds a `tiers[]` array and calls `runEscalationChain()` — the same function used by `web_fetch`. All bus events, sequencer reordering, hint-skipping, and envelope building come for free.

```
search.ts execute()
  → searchEnvelope(query, num, deps)
    → tiers = [createDdgHtmlTier(), createGoogleCamoFoxTier(camofox), createGooglePuppeteerTier(puppeteer)]
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
1. **JSON-LD** — `<script type="application/ld+json">` blocks containing `@type: SearchResultsPage` or `ItemList`. Most stable — Google preserves this for crawlers.
2. **`div.g h3 a[href]`** — classic result container. `href` is a direct URL (not a redirect). Snippet from sibling `span`.
3. **`[data-sokoban-container] h3 a[href]`** — attribute-based fallback, more resilient than class names.

Returns `[]` if all three yield zero results — never throws. Callers treat `[]` + `BlockingClassifier` signal as a block.

#### `src/browser/smart-search.ts`
Tier factories + `searchEnvelope()` entry point. Parallel to `smart-fetch.ts`.

```typescript
export interface SearchEnvelopeDeps {
  camofox?: CamoFoxClient;
  puppeteer?: PuppeteerFetcher;
  classifier?: BlockingClassifier;
  bus?: GatewayEventBus;
}

export function createDdgHtmlTier(): TierRunner
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
| `createDdgHtmlTier` | 1 | `"scrapling"` | always true | fetch `https://html.duckduckgo.com/html/?q=<query>` + existing DDG parser |
| `createGoogleCamoFoxTier` | 2 | `"google-camofox"` | `client.isHealthy()` | `client.navigate(INTERNAL_SESSION, "@google_search", query)` → `snapshot()` → `parseGoogleHtml()` |
| `createGooglePuppeteerTier` | 3 | `"google-puppeteer"` | `fetcher.probe()` | `fetcher.fetch("https://www.google.com/search?q=" + encodeURIComponent(query))` → `parseGoogleHtml()` |

**CamoFox session:** Uses fixed internal session ID `"search-tier-internal"`. Tab is created, snapshot taken, tab closed within `run()`. No user session required.

**`searchEnvelope()` flow:**
1. Build `tiers[]` — only push Tier 2 if `deps.camofox` provided; only push Tier 3 if `deps.puppeteer` provided
2. Call `runEscalationChain(tiers, query, { bus: deps.bus })`
3. Return `WebToolResult` directly

### Modified Files (4)

#### `src/browser/envelope.ts`
Add to `TierName` union and `NAMES` Set:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer" | "google-camofox" | "google-puppeteer";
const NAMES = new Set<TierName>([..., "google-camofox", "google-puppeteer"]);
```

#### `src/runtime/availability.ts`
Add to `BackendName` union and `emptyMap()`:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer" | "google-camofox" | "google-puppeteer";
// emptyMap() gains: "google-camofox": emptyStatus(), "google-puppeteer": emptyStatus()
```

#### `src/tools/search.ts`
Replace the inline DDG scrape + try/catch block with:
```typescript
const result = await searchEnvelope(query, num, {
  camofox: context.camofox,
  puppeteer: context.puppeteer,
  classifier: context.classifier,
  bus: (context.engineContext as any)?.eventBus,
});
return serializeWebToolResult(result);
```

#### `src/index.ts`
Wire `camofoxClient` into `gateway.ctx`:
```typescript
// In the intelligence block, after BlockingClassifier instantiation:
if (b.camofoxClient) {
  gateway.ctx.camofox = b.camofoxClient;
}
```
Also add `camofox?: CamoFoxClient` to `GatewayContext` in `gateway/types.ts` and thread through `context-builder.ts` → `EngineContext` → `toolCtx` (same pattern as `puppeteer` in 16d Task 5).

---

## Data Flow

### Happy path (DDG succeeds)
```
Tier 1 runs → DDG HTML → parseResults() → results[] → WebToolResult { success: true }
```

### DDG blocked → Google via CamoFox
```
Tier 1 → BlockingClassifier → outcome: "blocked"
Tier 2 → CamoFoxClient.navigate("search-tier-internal", "@google_search", query)
        → snapshot() html → parseGoogleHtml()
        → JSON-LD match → SearchResult[] → WebToolResult { success: true }
```

### CamoFox unavailable → Puppeteer
```
Tier 2 isAvailable() → false → skipped (outcome: "unavailable")
Tier 3 → PuppeteerFetcher.fetch("https://www.google.com/search?q=" + query)
        → html → parseGoogleHtml() → results → WebToolResult { success: true }
```

### All tiers fail
```
WebToolResult {
  success: false,
  error: {
    code: "BLOCKED_BY_ANTI_BOT",
    attemptedTiers: [
      { tier:1, name:"scrapling", outcome:"blocked" },
      { tier:2, name:"google-camofox", outcome:"blocked" },
      { tier:3, name:"google-puppeteer", outcome:"blocked" }
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
| CamoFox `navigate()` throws | `outcome: "error"` → escalate |
| Puppeteer `fetch()` times out | `outcome: "timeout"` → escalate |
| `parseGoogleHtml` returns `[]`, not blocked | `success: true, results: []` — genuine empty SERP |
| `parseGoogleHtml` returns `[]`, blocked | BlockingClassifier → `outcome: "blocked"` → escalate |
| All tiers exhausted | `BLOCKED_BY_ANTI_BOT` envelope, `suggestedEscalation: "live_browser"` |

---

## Wiring Chain

```
index.ts bootstrap()
  → b.camofoxClient (already exists)
  → gateway.ctx.camofox = b.camofoxClient      ← new

gateway/types.ts GatewayContext
  → camofox?: CamoFoxClient                    ← new

context-builder.ts baseContext()
  → camofox: this.ctx.camofox                  ← new

runtime.ts EngineContext
  → camofox?: CamoFoxClient                    ← new

runtime.ts toolCtx (×2)
  → camofox: context.camofox                   ← new

registry.ts ToolContext
  → camofox?: CamoFoxClient                    ← new

search.ts execute()
  → context.camofox  (first-class ToolContext field, same pattern as context.puppeteer in 16d)
```

Note: `puppeteer` and `classifier` are already on `ToolContext` from 16d — no additional wiring needed for those.

---

## Testing

### `__tests__/browser/google-parser.test.ts` (new)
- Parses JSON-LD fixture → correct `{ title, url, snippet }[]`
- Falls back to `div.g h3 a` when no JSON-LD
- Falls back to `[data-sokoban-container]` as third option
- Returns `[]` on CAPTCHA HTML — no throw
- Decodes percent-encoded URLs in href attributes

### `__tests__/browser/smart-search.test.ts` (new)
- `createDdgHtmlTier()` → `tier:1, name:"scrapling"`
- `createGoogleCamoFoxTier()` → `tier:2, name:"google-camofox"`, `isAvailable()` delegates to `client.isHealthy()`
- `createGooglePuppeteerTier()` → `tier:3, name:"google-puppeteer"`, `isAvailable()` delegates to `fetcher.probe()`
- `searchEnvelope()` returns Google results when DDG mock returns blocked
- `searchEnvelope()` omits Tier 2 when `deps.camofox` is undefined
- `searchEnvelope()` omits Tier 3 when `deps.puppeteer` is undefined

### `__tests__/browser/search-escalation.test.ts` (new)
- DDG blocked → CamoFox succeeds → `attemptedTiers` has 2 entries (blocked + success)
- All 3 blocked → `success: false`, 3 attempts, `suggestedEscalation: "live_browser"`
- Tier 2 `isAvailable()` false → skipped, Tier 3 fires directly
- Tier 1 succeeds → Tiers 2 and 3 never called

### Updated: existing `search.ts` tests
- Pass mock `SearchEnvelopeDeps` to refactored `execute()` call
- No tests deleted

---

## File Delta

| File | Action |
|------|--------|
| `src/browser/google-parser.ts` | **NEW** |
| `src/browser/smart-search.ts` | **NEW** |
| `src/browser/envelope.ts` | modify — add 2 TierName values |
| `src/runtime/availability.ts` | modify — add 2 BackendName values |
| `src/tools/search.ts` | modify — replace inline scrape with searchEnvelope() |
| `src/gateway/types.ts` | modify — add camofox? to GatewayContext |
| `src/engine/runtime.ts` | modify — add camofox? to EngineContext + 2 toolCtx sites |
| `src/gateway/handlers/context-builder.ts` | modify — thread camofox in baseContext() |
| `src/tools/registry.ts` | modify — add camofox? to ToolContext |
| `src/index.ts` | modify — wire camofoxClient to gateway.ctx.camofox |

**Net: +2 new files, 0 deleted. Within budget.**

---

## Out of Scope

- Brave Search revival — removed in 16c, not returning
- SerpAPI / Serper.dev — paid dependency, unjustified for personal assistant scale
- `page.$$eval()` live DOM parsing — requires PuppeteerFetcher refactor, deferred
- Search result caching / deduplication across tiers
- Tier 2 `@google_search` macro replacement with direct URL navigation (macro is sufficient)
- web_search Tier 4 (live_browser direct call) — live_browser stays out of the automated chain
