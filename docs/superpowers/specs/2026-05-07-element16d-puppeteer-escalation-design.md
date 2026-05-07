# Element 16d — Puppeteer Escalation Tier & Web Fetch Wiring

> **Revision history:** v2 (2026-05-07) — full squad review: John (PM), Mary (BA/research), Winston (architect) found critical bugs in v1. This version incorporates all corrections. v1 is archived in git history.

## Context

Web fetch returns "BLOCKED" on anti-bot sites (Amazon, LinkedIn, etc.) because:
1. Scrapling (Tier 1) never runs — `webFetchEnvelope()` is called with no deps at `web.ts:62`
2. BlockingClassifier is never reachable — `ToolContext.classifier` is never populated
3. No autonomous headless browser tier exists — only scrapling, camofox, and a live_browser shortcut

This spec fixes all three issues and adds Puppeteer + Crawlee as Tier 3. Escalation uses zero hardcoded rules — all decisions are driven by `BlockingClassifier` (runtime detection) and `FallbackSequencer` (learned host history via `tool_edges.host_root` from v27 schema).

**Squad review corrections over v1:** `buildSuccessEnvelope()` doesn't exist (use `TierRunner` factory pattern); `new SessionPool()` is invalid (use `SessionPool.open()`); `session.userData.userAgent` is never auto-populated; Chrome/124 UA hardcode is wrong (real browser is Chrome/147); `networkidle2` times out on Amazon; `classify()` requires structured input not raw HTML; `TierName` missing `"puppeteer"` causes silent envelope validation failure; `ToolContext` lives in `registry.ts` not `runtime.ts`; wiring goes through `EngineContext`.

---

## Tier Structure

```
Tier 1  Scrapling         Python subprocess, fast/lightweight
Tier 2  CamoFox           Stealth HTTP (if running at localhost:9377)
Tier 3  Puppeteer+Crawlee Autonomous headless Chrome + stealth plugin
Tier 4  live_browser      User's frontmost Safari/Chrome — last resort only
```

### Escalation Logic (zero hardcoding)

1. `RuntimeAvailability` probe at boot — `"puppeteer"` added to `BackendName`; unavailable tiers skipped
2. `runEscalationChain` checks `FallbackSequencer.getNextFallback(hostRoot)` — if learned winner for this host, start there; otherwise start at Tier 1
3. After each tier: `BlockingClassifier.classify({ url, httpStatus, bodyPreview })` grades response
   - `success` → return result immediately
   - `blocked` → escalate; record failure in `EdgeAccumulator` with `hostRoot`
4. `live_browser` only when Tiers 1–3 are all unavailable or all blocked

---

## Section 0: price_compare Skill Fix (Task 1 — active user breakage)

`src/skills/defaults/price_compare/SKILL.md:26` references `duckduckgo_search`, which was deleted in Element 16c. Any user running `/price_compare` today gets a runtime error. Fix: rename to `web_search`. This is a 1-line change, zero risk, and must ship first.

---

## Section 1: Type System Prerequisities (must land before Puppeteer code)

### `envelope.ts` — add `"puppeteer"` to `TierName` and `NAMES`

```typescript
// line 18 — before
export type TierName = "camofox" | "scrapling" | "obscura";
// after
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer";

// line 73 — before
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura"]);
// after
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura", "puppeteer"]);
```

Without this, `isWebToolError()` at line 125 rejects any envelope containing a Puppeteer tier attempt — the `<tool_attempt_summary>` XML the LLM reads silently loses the Puppeteer attempt.

### `availability.ts` — add `"puppeteer"` to `BackendName`

```typescript
// line 6 — before
export type BackendName = "camofox" | "scrapling" | "live-browser";
// after
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer";
```

Also update `emptyMap()` at line 27 to include the new key:
```typescript
return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus(), puppeteer: emptyStatus() };
```

### `WebFetchEnvelopeDeps` — add `puppeteer?` field (`smart-fetch.ts:644`)

```typescript
export interface WebFetchEnvelopeDeps {
  classifier?: { classify: BlockingClassifier["classify"] };
  availability?: Pick<RuntimeAvailability, "isReady">;
  scrapling?: {
    probe: () => Promise<{ ok: boolean; version?: string; error?: string }>;
    run: (url: string) => Promise<{ title: string; url: string; content: string }>;
  };
  bus?: GatewayEventBus;
  hint?: "anti-bot";
  puppeteer?: PuppeteerFetcher;   // new
}
```

---

## Section 2: PuppeteerFetcher (`src/browser/puppeteer-fetcher.ts`)

New file. Key design decisions from squad review:
- **Warm browser singleton** — single `Browser` instance, `BrowserContext` per request (not new browser per request, which costs 800ms–2s cold start + memory leaks)
- **`SessionPool.open()`** — async factory, not `new SessionPool()` (constructor doesn't initialize the pool)
- **`puppeteer-extra` + stealth plugin** — patches 10 detection vectors (plugin array, chrome.runtime, WebGL vendor, permission API, iframe contentWindow, etc.) that bare `--disable-blink-features=AutomationControlled` misses entirely
- **No hardcoded UA** — stealth plugin derives and patches UA from the real browser; never set a stale hardcoded string
- **`domcontentloaded`** — not `networkidle2`; Amazon keeps persistent polling connections open that prevent `networkidle2` from ever settling
- **`probe()` uses `executablePath()`** — zero browser launch cost at boot
- **`close()` wired to all shutdown handlers** in `index.ts`

```typescript
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, BrowserContext, Page } from "puppeteer";
import { SessionPool, Session } from "@crawlee/core";
import { existsSync } from "node:fs";
import { executablePath as puppeteerExePath } from "puppeteer";

puppeteer.use(StealthPlugin());

export interface PuppeteerFetchResult {
  html: string;
  finalUrl: string;
  status: number;
}

export class PuppeteerFetcher {
  private browser: Browser | null = null;
  private sessionPool: SessionPool | null = null;
  private readonly userAgentPool: string[] = [
    // 4-5 real recent UAs — populated at init() from browser's own navigator.userAgent
    // Entries added dynamically; this array is seeded by the stealth plugin's default
  ];

  async init(): Promise<void> {
    this.sessionPool = await SessionPool.open({
      maxPoolSize: 5,
      createSessionFunction: (pool) =>
        new Session({ sessionPool: pool, userData: {} }),
    });
    this.browser = await puppeteer.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
      ],
    });
  }

  async fetch(url: string, timeoutMs = 25_000): Promise<PuppeteerFetchResult> {
    if (!this.browser) throw new Error("PuppeteerFetcher not initialized");
    const session = await this.sessionPool!.getSession();
    const context: BrowserContext = await this.browser.createBrowserContext();
    const page: Page = await context.newPage();
    try {
      // Inject session cookies if the SessionPool has stored any for this domain
      const cookies = session.getCookies(new URL(url).origin);
      if (cookies.length) await context.setCookie(...cookies);

      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: timeoutMs,
      });
      const html = await page.content();

      // Persist updated cookies back to session for reuse
      const updatedCookies = await page.cookies();
      session.setCookiesFromResponse(updatedCookies as any, new URL(url).origin);
      session.markGood();

      return {
        html,
        finalUrl: page.url(),
        status: response?.status() ?? 200,
      };
    } catch (err) {
      session.markBad();
      throw err;
    } finally {
      await context.close();
    }
  }

  async probe(): Promise<boolean> {
    try {
      const path = puppeteerExePath();
      return !!path && existsSync(path);
    } catch {
      return false;
    }
  }

  async close(): Promise<void> {
    await this.sessionPool?.teardown();
    await this.browser?.close();
    this.browser = null;
    this.sessionPool = null;
  }
}
```

**New dependencies** (`package.json`):
```json
"puppeteer-extra": "^3.3.6",
"puppeteer-extra-plugin-stealth": "^2.11.2"
```

---

## Section 3: `createPuppeteerTier()` Factory (`src/browser/smart-fetch.ts`)

**Do NOT insert a freestanding `if (deps.puppeteer)` block.** The correct pattern is a `TierRunner` factory pushed into the `tiers[]` array, which gets all escalation bus events, sequencer reordering, and hint-skipping automatically.

### Factory function (add to `smart-fetch.ts`):

```typescript
export function createPuppeteerTier(fetcher: PuppeteerFetcher): TierRunner {
  return {
    tier: 3,
    name: "puppeteer",
    isAvailable: () => fetcher.probe(),
    async run(url, ctx) {
      const t0 = Date.now();
      try {
        const r = await fetcher.fetch(url);
        return {
          attempt: {
            tier: 3,
            name: "puppeteer",
            durationMs: Date.now() - t0,
            outcome: "success",
            httpStatus: r.status,
          },
          data: { kind: "page", url: r.finalUrl, content: r.html },
        };
      } catch {
        return {
          attempt: {
            tier: 3,
            name: "puppeteer",
            durationMs: Date.now() - t0,
            outcome: "error",
          },
        };
      }
    },
  };
}
```

Note: `BlockingClassifier` grading after a successful Puppeteer fetch is handled by the caller (`webFetchEnvelope` passes `classifier` in `DispatcherCtx` — `runEscalationChain` does not currently invoke the classifier per-tier; classification happens at the `web.ts` / `registry.ts` level via `GoalVerifier`). If a post-response classifier check is desired inside the tier, the `run()` method can call `classify({ url, httpStatus: r.status, bodyPreview: r.html.slice(0, 2048) })` and return `outcome: "blocked"` on a positive result.

### Push into `webFetchEnvelope()` tiers array (`smart-fetch.ts:674`):

```typescript
const tiers: TierRunner[] = [];
if (deps.scrapling) {
  tiers.push(createScraplingTier({ probe: deps.scrapling.probe, runScrapling: deps.scrapling.run }));
}
if (camoClient) {
  tiers.push(createCamoFoxTier({ availability, client: camoClient, classifier }));
}
if (deps.puppeteer) {
  tiers.push(createPuppeteerTier(deps.puppeteer));   // new — Tier 3
}
tiers.push(createObscuraTier({ enabled: false }));   // Tier 4 stub (Phase B)
```

---

## Section 4: Wiring (`src/tools/web.ts` + `src/engine/runtime.ts` + `src/index.ts`)

### `web.ts` — pass all deps including puppeteer and bus

**Important:** `ToolContext` is defined in `src/tools/registry.ts` (not `runtime.ts`). The `puppeteer?` field is added there.

```typescript
// src/tools/registry.ts — ToolContext interface
export interface ToolContext {
  cwd: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  puppeteer?: PuppeteerFetcher;   // new
}
```

**`ToolContext` is constructed inside `runtime.ts`** at the `ReAct` loop, not in `index.ts`. The instance is built at `runtime.ts:1273` and `runtime.ts:2913`. The correct threading path is through `EngineContext`:

```typescript
// src/engine/runtime.ts — EngineContext interface extension
export interface EngineContext {
  // ... existing fields ...
  classifier?: Pick<BlockingClassifier, "classify">;   // existing
  puppeteer?: PuppeteerFetcher;                        // new
}

// runtime.ts — toolCtx construction (lines 1273 and 2913)
const toolCtx: ToolContext = {
  cwd: ...,
  engineContext: ctx,
  classifier: ctx.classifier,
  puppeteer: ctx.puppeteer,   // thread through
};
```

**`index.ts`** — after `puppeteerFetcher.init()` succeeds, assign to `engineContext`:

```typescript
const puppeteerFetcher = new PuppeteerFetcher();
const puppeteerReady = await puppeteerFetcher.probe();
if (puppeteerReady) {
  await puppeteerFetcher.init();
  engineContext.puppeteer = puppeteerFetcher;
  await availability.probe("puppeteer", () => puppeteerFetcher.probe());
}
```

**`web.ts:execute()`** — pass deps:

```typescript
const result = await webFetchEnvelope(url, {
  scrapling: context.engineContext?.scrapling,
  classifier: context.classifier,
  puppeteer: context.puppeteer,
  bus: context.engineContext?.bus,
  hint: args["hint"] as "anti-bot" | undefined,
});
```

### `index.ts` — shutdown handlers

Add `await puppeteerFetcher.close()` to all shutdown handler locations in `index.ts` (lines 1876, 1944, 2130 approximate). The `close()` tears down the `SessionPool` and the warm `Browser` instance.

---

## Section 5: FallbackSequencer host-aware `replan()` (`src/tools/cortex/tool-graph.ts`)

**No schema migration needed** — `host_root` was added in v27 (`applyV27HostRootMigration`). `EdgeAccumulator.observe()` already accepts `hostRoot?: string`. The only change needed:

Add `hostRoot?` to `ReplanOptions` and extend the query to try host-specific edges before falling back to global:

```typescript
export interface ReplanOptions {
  exclude?: string[];
  hostRoot?: string;   // new — prefers host-specific edge over global
}

replan(currentTool: string, capabilityTag: string, opts: ReplanOptions = {}): string | null {
  const minSamples = this.config.minSamples ?? 3;
  const exclude = Array.from(new Set([currentTool, ...(opts.exclude ?? [])]));
  const placeholders = exclude.map(() => "?").join(",");

  // 1. Host-specific edge first
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

  // 2. Global fallback (host_root = '' only — prevents per-host rows poisoning global)
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

**Also fix the existing `replan()` call at `registry.ts:396`** — the current single-arg call queries globally without `AND host_root = ''`, meaning per-host rows can pollute global fallback selection. Pass `hostRoot`:

```typescript
const urlHost = (() => {
  try { return args.url ? new URL(args.url as string).hostname : ""; } catch { return ""; }
})();
const fallback = this._toolGraph.replan(name, capability, { hostRoot: urlHost });
```

---

## File Delta

| File | Change | New? |
|------|--------|------|
| `src/browser/puppeteer-fetcher.ts` | PuppeteerFetcher class (warm singleton + SessionPool + stealth) | **NEW** |
| `src/browser/envelope.ts` | Add `"puppeteer"` to `TierName` + `NAMES` set |  |
| `src/browser/smart-fetch.ts` | Add `puppeteer?` to `WebFetchEnvelopeDeps`; add `createPuppeteerTier()` factory; push into `tiers[]` |  |
| `src/runtime/availability.ts` | Add `"puppeteer"` to `BackendName`; add to `emptyMap()` |  |
| `src/tools/registry.ts` | Add `puppeteer?` to `ToolContext`; pass `hostRoot` to `_toolGraph.replan()` |  |
| `src/tools/web.ts` | Pass all deps (scrapling, classifier, puppeteer, bus, hint) to `webFetchEnvelope` |  |
| `src/engine/runtime.ts` | Add `puppeteer?` to `EngineContext`; thread into `toolCtx` at construction sites |  |
| `src/index.ts` | Boot-probe + `init()` PuppeteerFetcher; assign to `engineContext`; wire `close()` to all shutdown handlers |  |
| `src/tools/cortex/tool-graph.ts` | Add `hostRoot?` to `ReplanOptions`; host-specific query before global fallback |  |
| `src/skills/defaults/price_compare/SKILL.md` | `duckduckgo_search` → `web_search` |  |
| `package.json` | Add `puppeteer-extra` + `puppeteer-extra-plugin-stealth` |  |

**1 new file. 10 modified. Net delta: +1.**

---

## Success Criteria (testable in Vitest)

1. **price_compare skill** — `SKILL.md` contains `web_search`, not `duckduckgo_search`. Verified by file read.

2. **Envelope validation** — `isWebToolError()` accepts a `TierAttempt` with `name: "puppeteer"`. Test:
   ```typescript
   expect(isWebToolError({ code: "BLOCKED_BY_ANTI_BOT", message: "blocked", attemptedTiers: [
     { tier: 3, name: "puppeteer", durationMs: 100, outcome: "blocked" }
   ]})).toBe(true);
   ```

3. **Tier escalation** — `runEscalationChain()` with mock TierRunners: Tier 1 returns `blocked`, Tier 2 returns `blocked`, Tier 3 returns `success`. Asserts `result.success === true` and `attemptedTiers` has 3 entries with outcomes `['blocked', 'blocked', 'success']`.

4. **Puppeteer probe** — `PuppeteerFetcher.probe()` returns `true` when `puppeteer.executablePath()` resolves to an existing file. Mock `existsSync` to test both paths.

5. **Host-aware replan** — `tool-graph.ts:replan()` with in-memory SQLite seeded with `host_root = 'amazon.com'` row (`success_rate = 0.9`, `sample_count = 5`, `to_tool = 'puppeteer'`). Asserts `replan("scrapling", "web_fetch", { hostRoot: "amazon.com" })` returns `"puppeteer"`.

6. **Global query isolation** — `replan()` with a per-host row only (no `host_root = ''` row) and no `hostRoot` option returns `null`, not the per-host row.

---

## Out of Scope

- Patchright / nodriver (Phase B — after measuring Puppeteer success rate in production)
- Brave Search as a search escalation tier (Phase B)
- Obscura runtime activation (gated at `webFetch.obscura.enabled=false`)
- Narration events for `web:tier_attempted` Puppeteer (infrastructure exists in `narration-formatter.ts`; add handler in a follow-on)
- Residential proxy injection (config option, future-proofing)
- Per-site session persistence across assistant restarts

## Known Limitations

- **TLS/JA4 fingerprinting** — AWS WAF and Cloudflare use JA4+ fingerprinting at the TLS layer. The stealth plugin cannot address this because it operates at the JS layer, not the TLS layer. This affects datacenter IPs more than residential IPs. Documented; no mitigation in this round.
- **Amazon first-3-requests cold start** — `FallbackSequencer` requires `minSamples = 3` before it learns to start at Tier 3. First 3 Amazon requests always attempt Tier 1 + Tier 2 first. Accepted behavior.
- **CDP side-channel** — some advanced WAFs (DataDome) detect Puppeteer via CDP protocol artifacts. The stealth plugin reduces but does not eliminate this vector. `nodriver`/CDP-free approaches are Phase B.
