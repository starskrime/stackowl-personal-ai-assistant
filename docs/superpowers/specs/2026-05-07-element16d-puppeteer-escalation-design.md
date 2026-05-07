# Element 16d — Puppeteer Escalation Tier & Web Fetch Wiring

## Context

Web fetch has been returning "BLOCKED" to users on anti-bot sites (Amazon, LinkedIn, etc.) because:
1. Scrapling (Tier 1) is never called — `webFetchEnvelope()` is invoked without deps in `web.ts:62`
2. BlockingClassifier is constructed but never reaches ToolContext, so tools can't detect blocking
3. There is no autonomous headless browser tier — only scrapling, camofox, and a live_browser shortcut

This spec fixes all three issues and adds Puppeteer + Crawlee as Tier 3 in the escalation chain. No hardcoded domain lists or routing rules anywhere — all escalation decisions are driven by BlockingClassifier (runtime detection) and FallbackSequencer (learned history).

---

## Design

### Tier Structure

```
Tier 1  Scrapling         Python subprocess, fast/lightweight
Tier 2  CamoFox           Stealth HTTP (if running at localhost:9377)
Tier 3  Puppeteer+Crawlee Autonomous headless Chrome, real JS execution
Tier 4  live_browser      User's frontmost Safari/Chrome — last resort only
```

### Escalation Logic (zero hardcoding)

1. **RuntimeAvailability probe** at boot — `"scrapling"`, `"camofox"`, `"puppeteer"` marked available/unavailable. Unavailable tiers are skipped.
2. **FallbackSequencer url_host lookup** — before attempting Tier 1, query `tool_edges` for a `url_host` match with `success_rate > 0.5`. If found, start at the learned winning tier.
3. **Run tier → BlockingClassifier grades response:**
   - `success` → return result immediately
   - `partial` → return with `<tool_result_warning>`
   - `blocked` → escalate to next available tier; record failure in FallbackSequencer
4. **After any success** — `EdgeAccumulator.observe({ fromTool, toTool, urlHost, success, durationMs })` updates `tool_edges`. Next request to the same host starts at the winning tier.
5. **live_browser** escalated to only when Tiers 1–3 are all unavailable or all return `blocked`.

---

## Section 1: Wiring Fixes

### Bug 1 — Scrapling never called (`src/tools/web.ts:62`)

```typescript
// before
const result = await webFetchEnvelope(url);

// after
const result = await webFetchEnvelope(url, {
  scrapling: context.scrapling,
  classifier: context.classifier,
  puppeteer: context.puppeteer,
});
```

`smart-fetch.ts:675` already gates on `if (deps.scrapling)` — it just never receives the instance.

### Bug 2 — BlockingClassifier not in ToolContext (`src/index.ts`)

`BlockingClassifier` is constructed in GatewayContext but `ToolContext.classifier` is never set. Fix at ToolContext construction:

```typescript
toolContext.classifier = gatewayContext.classifier;
toolContext.scrapling = scraplingInstance;   // from probeReadiness() at boot
toolContext.puppeteer = puppeteerFetcher;    // new — probed at boot
```

### Bug 3 — price_compare skill references deleted tool

`src/skills/defaults/price_compare/SKILL.md:26`: rename `duckduckgo_search` → `web_search`.

---

## Section 2: PuppeteerFetcher (`src/browser/puppeteer-fetcher.ts`)

New file. Uses `puppeteer` (already installed v24) and `@crawlee/puppeteer`'s `SessionPool` (already installed v3.16) for session rotation — each repeat visit to the same host uses a rotated user-agent and cookie jar, reducing re-detection.

```typescript
import puppeteer from "puppeteer";
import { SessionPool } from "@crawlee/puppeteer";

export interface PuppeteerFetchResult {
  html: string;
  finalUrl: string;
  status: number;
}

export class PuppeteerFetcher {
  private sessionPool: SessionPool;

  constructor() {
    this.sessionPool = new SessionPool({ maxPoolSize: 5 });
  }

  async fetch(url: string, timeoutMs = 30_000): Promise<PuppeteerFetchResult> {
    const session = await this.sessionPool.getSession();
    const browser = await puppeteer.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
      ],
    });
    try {
      const page = await browser.newPage();
      await page.setUserAgent(session.userData.userAgent ?? defaultUserAgent());
      await page.setExtraHTTPHeaders({ "Accept-Language": "en-US,en;q=0.9" });
      const response = await page.goto(url, {
        waitUntil: "networkidle2",
        timeout: timeoutMs,
      });
      const html = await page.content();
      return {
        html,
        finalUrl: page.url(),
        status: response?.status() ?? 200,
      };
    } finally {
      await browser.close();
    }
  }

  async probe(): Promise<boolean> {
    try {
      const b = await puppeteer.launch({ headless: true });
      await b.close();
      return true;
    } catch {
      return false;
    }
  }

  async close(): Promise<void> {
    await this.sessionPool.teardown();
  }
}

function defaultUserAgent(): string {
  return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
}
```

**ToolContext extension** (`src/engine/runtime.ts` or `src/tools/registry.ts` — wherever `ToolContext` is defined):

```typescript
export interface ToolContext {
  cwd: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  scrapling?: ScraplingFetcher;      // existing, now wired
  puppeteer?: PuppeteerFetcher;      // new
}
```

---

## Section 3: smart-fetch Tier 3 Block (`src/browser/smart-fetch.ts`)

After the CamoFox block (around line 678), before the `ALL_TIERS_UNAVAILABLE` return:

```typescript
// Tier 3: Puppeteer autonomous headless browser
if (deps.puppeteer) {
  tierAttempts.push({ tier: "puppeteer", startedAt: Date.now() });
  try {
    const r = await deps.puppeteer.fetch(url);
    const classified = deps.classifier
      ? await deps.classifier.classify(r.html)
      : { blocked: false };
    if (!classified.blocked) {
      return buildSuccessEnvelope(r.html, "puppeteer", tierAttempts);
    }
    tierAttempts[tierAttempts.length - 1].blockedReason = classified.reason;
  } catch (err) {
    tierAttempts[tierAttempts.length - 1].error = String(err);
  }
}
```

---

## Section 4: FallbackSequencer URL-Pattern Learning

**Most of this is already built.** Schema v27 (`applyV27HostRootMigration`) already added `host_root TEXT NOT NULL DEFAULT ''` as part of the `tool_edges` PRIMARY KEY `(from_tool, to_tool, capability_tag, host_root)`. `EdgeAccumulator.observe()` already accepts `hostRoot?: string` and writes it to `tool_edges`. No schema migration needed.

The only missing piece: `tool-graph.ts:replan()` does not filter by `host_root` — it queries globally. Fix: add `hostRoot?` to `ReplanOptions` and extend the query to try the host-specific row first.

### `tool-graph.ts` change (`src/tools/cortex/tool-graph.ts`)

```typescript
export interface ReplanOptions {
  exclude?: string[];
  hostRoot?: string;   // new — try host-specific edges before falling back to global
}

replan(currentTool: string, capabilityTag: string, opts: ReplanOptions = {}): string | null {
  const minSamples = this.config.minSamples ?? 3;
  const exclude = Array.from(new Set([currentTool, ...(opts.exclude ?? [])]));
  const placeholders = exclude.map(() => "?").join(",");

  // 1. Try host-specific edge first
  if (opts.hostRoot) {
    const hostRow = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
         WHERE capability_tag = ? AND host_root = ?
           AND sample_count >= ?
           AND to_tool NOT IN (${placeholders})
         ORDER BY success_rate DESC, sample_count DESC LIMIT 1`,
      )
      .get(capabilityTag, opts.hostRoot, minSamples, ...exclude) as
      | { to_tool: string }
      | undefined;
    if (hostRow) return hostRow.to_tool;
  }

  // 2. Fall back to global (host_root = '')
  const row = this.db.rawDb
    .prepare(
      `SELECT to_tool FROM tool_edges
       WHERE capability_tag = ? AND host_root = ''
         AND sample_count >= ?
         AND to_tool NOT IN (${placeholders})
       ORDER BY success_rate DESC, sample_count DESC, avg_duration_ms ASC
       LIMIT 1`,
    )
    .get(capabilityTag, minSamples, ...exclude) as
    | { to_tool: string }
    | undefined;

  return row?.to_tool ?? null;
}
```

### Passing `hostRoot` into `replan()` from `registry.ts`

In `registry.ts:execute()`, the call to `this._toolGraph.replan(name, capability)` must be extended to pass the URL host extracted from `args`:

```typescript
const urlHost = extractHost(args.url as string | undefined);
const fallback = this._toolGraph.replan(name, capability, { hostRoot: urlHost });
```

```typescript
function extractHost(url?: string): string {
  if (!url) return "";
  try { return new URL(url).hostname; } catch { return ""; }
}
```

---

## File Delta

| File | Change | New? |
|------|--------|------|
| `src/browser/puppeteer-fetcher.ts` | PuppeteerFetcher class | **NEW** |
| `src/browser/smart-fetch.ts` | Add Tier 3 Puppeteer block |  |
| `src/tools/web.ts` | Pass scrapling + classifier + puppeteer deps |  |
| `src/index.ts` | Boot-probe PuppeteerFetcher; wire into ToolContext; register in RuntimeAvailability |  |
| `src/tools/registry.ts` | Pass `hostRoot` to `_toolGraph.replan()`; add `extractHost()` helper |  |
| `src/tools/cortex/tool-graph.ts` | Add `hostRoot?` to `ReplanOptions`; host-specific query before global fallback |  |
| `src/engine/runtime.ts` | Add `puppeteer?` to ToolContext interface |  |
| `src/skills/defaults/price_compare/SKILL.md` | `duckduckgo_search` → `web_search` |  |

**1 new file. 7 modified. Net delta: +1.** No schema migration needed — `host_root` already in v27.

---

## Success Criteria

- `npx vitest run` passes — no regressions
- `web_fetch("https://amazon.com/dp/B0XXXXX")` progresses through Tiers 1 → 2 → 3 before reaching live_browser
- BlockingClassifier receives actual response content and returns a verdict
- `tool_edges` gains `url_host` column; after 3 Amazon requests, FallbackSequencer starts at Puppeteer instead of scrapling
- `price_compare` skill no longer references `duckduckgo_search`
- No hardcoded domain lists or routing rules anywhere in changed files

---

## Out of Scope

- Patchright / Playwright-stealth upgrades (Phase B, after measuring Puppeteer success rate)
- Brave Search as a search tier (Phase B)
- Obscura runtime activation (gated at `webFetch.obscura.enabled=false`, reserved)
- Per-site session persistence across assistant restarts
