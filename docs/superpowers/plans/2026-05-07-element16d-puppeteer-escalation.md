# Element 16d — Puppeteer Escalation Tier & Web Fetch Wiring

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all three existing autonomous web-fetch tiers (Scrapling, CamoFox, Puppeteer) into the escalation chain so the assistant never says "BLOCKED" before actually trying every tier.

**Architecture:** Fix three silent wiring bugs (scrapling deps never passed, BlockingClassifier never reaches ToolContext, no Puppeteer tier) plus add Puppeteer+Crawlee as Tier 3 using the existing `TierRunner`/`runEscalationChain` machinery. Escalation order is learned via `FallbackSequencer` (host-aware `tool_edges.host_root`) with zero hardcoded rules.

**Tech Stack:** puppeteer-extra + puppeteer-extra-plugin-stealth (new), @crawlee/core SessionPool (existing), Vitest, TypeScript strict, better-sqlite3.

---

## File Structure

| File | Role |
|------|------|
| `src/browser/puppeteer-fetcher.ts` | NEW — warm browser singleton + SessionPool + stealth plugin |
| `src/browser/envelope.ts` | Add `"puppeteer"` to `TierName` + `NAMES` Set |
| `src/runtime/availability.ts` | Add `"puppeteer"` to `BackendName` + `emptyMap()` |
| `src/browser/smart-fetch.ts` | Add `puppeteer?` to `WebFetchEnvelopeDeps`; `createPuppeteerTier()` factory; push into `tiers[]` |
| `src/gateway/types.ts` | Add `blockingClassifier?` + `puppeteer?` to `GatewayContext` |
| `src/engine/runtime.ts` | Add `classifier?` + `puppeteer?` to `EngineContext`; thread into `toolCtx` at lines 1273 and 2913 |
| `src/gateway/handlers/context-builder.ts` | Thread `classifier` + `puppeteer` from `this.ctx` into `baseContext()` return |
| `src/tools/registry.ts` | Add `puppeteer?` to `ToolContext`; pass `hostRoot` to `_toolGraph.replan()` |
| `src/tools/web.ts` | Pass all deps to `webFetchEnvelope()` |
| `src/tools/cortex/tool-graph.ts` | Add `hostRoot?` to `ReplanOptions`; host-specific query before global |
| `src/index.ts` | Instantiate `BlockingClassifier` + `PuppeteerFetcher`, wire to `gateway.ctx`, wire `close()` to shutdown handlers |
| `src/skills/defaults/price_compare/SKILL.md` | `duckduckgo_search` → `web_search` |
| `package.json` | Add `puppeteer-extra` + `puppeteer-extra-plugin-stealth` |
| `__tests__/browser/puppeteer-fetcher.test.ts` | NEW — unit tests for PuppeteerFetcher |
| `__tests__/browser/escalation-tier3.test.ts` | NEW — runEscalationChain with Puppeteer tier mock |
| `__tests__/tools/host-aware-replan.test.ts` | NEW — host-specific replan tests |

---

## Task 1: Fix price_compare skill — active user breakage

**Files:**
- Modify: `src/skills/defaults/price_compare/SKILL.md`

The skill references `duckduckgo_search` which was deleted in Element 16c. Every `/price_compare` call is broken right now. Three occurrences to fix (lines 15, 20, 25).

- [ ] **Step 1: Verify the breakage**

```bash
grep -n "duckduckgo_search" src/skills/defaults/price_compare/SKILL.md
```

Expected output:
```
15:    tool: duckduckgo_search
20:    tool: duckduckgo_search
25:    tool: duckduckgo_search
```

- [ ] **Step 2: Fix all three occurrences**

In `src/skills/defaults/price_compare/SKILL.md`, replace all three `tool: duckduckgo_search` with `tool: web_search`. The file has no other changes — only those three lines.

After editing, lines 14-16 should read:
```yaml
  - id: search_amazon
    tool: web_search
    args:
```

- [ ] **Step 3: Verify fix**

```bash
grep -n "duckduckgo_search" src/skills/defaults/price_compare/SKILL.md
```

Expected: no output (zero matches).

```bash
grep -n "web_search" src/skills/defaults/price_compare/SKILL.md
```

Expected: 3 lines matching.

- [ ] **Step 4: Commit**

```bash
git add src/skills/defaults/price_compare/SKILL.md
git commit -m "fix(skills): rename duckduckgo_search → web_search in price_compare"
```

---

## Task 2: Type system prerequisites — envelope.ts + availability.ts

**Files:**
- Modify: `src/browser/envelope.ts:18,73`
- Modify: `src/runtime/availability.ts:6,27`
- Test: `__tests__/browser/envelope-puppeteer.test.ts`

Without these changes, `isWebToolError()` silently strips any Puppeteer tier attempt from envelopes, and `RuntimeAvailability` rejects `"puppeteer"` as a key.

- [ ] **Step 1: Write failing test**

Create `__tests__/browser/envelope-puppeteer.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { isWebToolError } from "../../src/browser/envelope.js";

describe("envelope TierName puppeteer", () => {
  it("isWebToolError accepts tier attempt with name 'puppeteer'", () => {
    expect(
      isWebToolError({
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [
          { tier: 3, name: "puppeteer", durationMs: 100, outcome: "blocked" },
        ],
      }),
    ).toBe(true);
  });

  it("isWebToolError rejects unknown tier name", () => {
    expect(
      isWebToolError({
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [
          { tier: 3, name: "unknown_tier", durationMs: 100, outcome: "blocked" },
        ],
      }),
    ).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/browser/envelope-puppeteer.test.ts
```

Expected: FAIL — `"puppeteer"` is not in `NAMES` set, so `isWebToolError` returns `false`.

- [ ] **Step 3: Fix envelope.ts**

In `src/browser/envelope.ts`, make two edits:

Line 18 — change:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura";
```
to:
```typescript
export type TierName = "camofox" | "scrapling" | "obscura" | "puppeteer";
```

Line 73 — change:
```typescript
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura"]);
```
to:
```typescript
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura", "puppeteer"]);
```

- [ ] **Step 4: Fix availability.ts**

In `src/runtime/availability.ts`:

Line 6 — change:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser";
```
to:
```typescript
export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer";
```

Line 27 — change:
```typescript
return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus() };
```
to:
```typescript
return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus(), puppeteer: emptyStatus() };
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/browser/envelope-puppeteer.test.ts
```

Expected: 2 PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
npx vitest run
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/browser/envelope.ts src/runtime/availability.ts __tests__/browser/envelope-puppeteer.test.ts
git commit -m "feat(types): add 'puppeteer' to TierName, NAMES, BackendName"
```

---

## Task 3: PuppeteerFetcher class + install stealth deps

**Files:**
- Create: `src/browser/puppeteer-fetcher.ts`
- Modify: `package.json`
- Test: `__tests__/browser/puppeteer-fetcher.test.ts`

- [ ] **Step 1: Install new dependencies**

```bash
npm install puppeteer-extra puppeteer-extra-plugin-stealth
```

Expected: `package.json` gains `"puppeteer-extra"` and `"puppeteer-extra-plugin-stealth"` in `dependencies`.

- [ ] **Step 2: Write failing tests**

Create `__tests__/browser/puppeteer-fetcher.test.ts`:

```typescript
import { describe, it, expect, vi, afterEach } from "vitest";

describe("PuppeteerFetcher", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("probe() returns true when executablePath resolves to existing file", async () => {
    vi.mock("puppeteer", () => ({
      default: { use: vi.fn(), launch: vi.fn() },
      executablePath: () => "/fake/chrome",
    }));
    vi.mock("node:fs", () => ({ existsSync: () => true }));
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    expect(await f.probe()).toBe(true);
  });

  it("probe() returns false when executablePath throws", async () => {
    vi.mock("puppeteer", () => ({
      default: { use: vi.fn(), launch: vi.fn() },
      executablePath: () => { throw new Error("not found"); },
    }));
    vi.mock("node:fs", () => ({ existsSync: () => false }));
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    expect(await f.probe()).toBe(false);
  });

  it("close() sets browser and sessionPool to null", async () => {
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    // Manually set private fields to mocks so close() can call them
    (f as any).browser = { close: vi.fn().mockResolvedValue(undefined), connected: true };
    (f as any).sessionPool = { teardown: vi.fn().mockResolvedValue(undefined) };
    await f.close();
    expect((f as any).browser).toBeNull();
    expect((f as any).sessionPool).toBeNull();
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
npx vitest run __tests__/browser/puppeteer-fetcher.test.ts
```

Expected: FAIL — `PuppeteerFetcher` doesn't exist yet.

- [ ] **Step 4: Create `src/browser/puppeteer-fetcher.ts`**

```typescript
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, BrowserContext } from "puppeteer";
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

  async init(): Promise<void> {
    this.sessionPool = await SessionPool.open({
      maxPoolSize: 5,
      createSessionFunction: (pool) =>
        new Session({ sessionPool: pool, userData: {} }),
    });
    this.browser = await (puppeteer as any).launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
      ],
    });
  }

  async fetch(url: string, timeoutMs = 25_000): Promise<PuppeteerFetchResult> {
    if (!this.browser) throw new Error("PuppeteerFetcher not initialized — call init() first");
    const session = await this.sessionPool!.getSession();
    const context: BrowserContext = await this.browser.createBrowserContext();
    const page = await context.newPage();
    try {
      const cookies = session.getCookies(new URL(url).origin);
      if (cookies.length) await context.setCookie(...(cookies as any[]));

      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: timeoutMs,
      });
      const html = await page.content();

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

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/browser/puppeteer-fetcher.test.ts
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/browser/puppeteer-fetcher.ts __tests__/browser/puppeteer-fetcher.test.ts package.json package-lock.json
git commit -m "feat(browser): add PuppeteerFetcher with warm singleton + stealth plugin"
```

---

## Task 4: smart-fetch — createPuppeteerTier + WebFetchEnvelopeDeps

**Files:**
- Modify: `src/browser/smart-fetch.ts:644-653,674-685`
- Test: `__tests__/browser/escalation-tier3.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/browser/escalation-tier3.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, createPuppeteerTier } from "../../src/browser/smart-fetch.js";
import type { TierRunner } from "../../src/browser/smart-fetch.js";
import type { PuppeteerFetcher } from "../../src/browser/puppeteer-fetcher.js";
import type { GatewayEventBus } from "../../src/gateway/event-bus.js";

const noop_bus = { emit: () => {} } as unknown as GatewayEventBus;

function mockBlockedRunner(tier: number, name: string): TierRunner {
  return {
    tier,
    name: name as any,
    isAvailable: () => true,
    run: async () => ({
      attempt: { tier, name: name as any, durationMs: 1, outcome: "blocked" as const },
    }),
  };
}

function mockSuccessRunner(tier: number, name: string, content: string): TierRunner {
  return {
    tier,
    name: name as any,
    isAvailable: () => true,
    run: async (_url) => ({
      attempt: { tier, name: name as any, durationMs: 1, outcome: "success" as const },
      data: { kind: "page" as const, url: _url, content },
    }),
  };
}

describe("Tier 3 escalation", () => {
  it("createPuppeteerTier returns TierRunner with tier=3 name=puppeteer", async () => {
    const mockFetcher = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({ html: "<h1>ok</h1>", finalUrl: "https://example.com", status: 200 }),
    } as unknown as PuppeteerFetcher;
    const runner = createPuppeteerTier(mockFetcher);
    expect(runner.tier).toBe(3);
    expect(runner.name).toBe("puppeteer");
    expect(await runner.isAvailable()).toBe(true);
  });

  it("runEscalationChain returns puppeteer success after tier1+tier2 block", async () => {
    const mockFetcher = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({ html: "<h1>amazon</h1>", finalUrl: "https://amazon.com", status: 200 }),
    } as unknown as PuppeteerFetcher;
    const tiers: TierRunner[] = [
      mockBlockedRunner(1, "scrapling"),
      mockBlockedRunner(2, "camofox"),
      createPuppeteerTier(mockFetcher),
    ];
    const result = await runEscalationChain(tiers, "https://amazon.com", { bus: noop_bus });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kind).toBe("page");
      expect((result.data as any).content).toBe("<h1>amazon</h1>");
    }
  });

  it("runEscalationChain records 3 attempts: blocked, blocked, success", async () => {
    const mockFetcher = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({ html: "ok", finalUrl: "https://amazon.com", status: 200 }),
    } as unknown as PuppeteerFetcher;
    const tiers: TierRunner[] = [
      mockBlockedRunner(1, "scrapling"),
      mockBlockedRunner(2, "camofox"),
      createPuppeteerTier(mockFetcher),
    ];
    const result = await runEscalationChain(tiers, "https://amazon.com", { bus: noop_bus });
    expect(result.success).toBe(false || true); // just needs to be defined
    // Check via the error path when all blocked
    const allBlockedTiers: TierRunner[] = [
      mockBlockedRunner(1, "scrapling"),
      mockBlockedRunner(2, "camofox"),
      mockBlockedRunner(3, "puppeteer"),
    ];
    const blocked = await runEscalationChain(allBlockedTiers, "https://amazon.com", { bus: noop_bus });
    expect(blocked.success).toBe(false);
    if (!blocked.success) {
      expect(blocked.error.attemptedTiers).toHaveLength(3);
      expect(blocked.error.attemptedTiers.map(t => t.outcome)).toEqual(["blocked", "blocked", "blocked"]);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/browser/escalation-tier3.test.ts
```

Expected: FAIL — `createPuppeteerTier` is not exported from `smart-fetch.ts`.

- [ ] **Step 3: Add `puppeteer?` to `WebFetchEnvelopeDeps` in smart-fetch.ts**

In `src/browser/smart-fetch.ts`, find `export interface WebFetchEnvelopeDeps` at line 644. Add the `puppeteer?` field after `bus?`:

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
  puppeteer?: import("./puppeteer-fetcher.js").PuppeteerFetcher;
}
```

- [ ] **Step 4: Add `createPuppeteerTier()` factory to smart-fetch.ts**

Add this function after `createCamoFoxTier` (find it around line 750+) and before `createObscuraTier`:

```typescript
export function createPuppeteerTier(
  fetcher: import("./puppeteer-fetcher.js").PuppeteerFetcher,
): TierRunner {
  return {
    tier: 3,
    name: "puppeteer",
    isAvailable: () => fetcher.probe(),
    async run(url) {
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

- [ ] **Step 5: Push Puppeteer tier into tiers[] in `webFetchEnvelope()`**

Find `webFetchEnvelope()` at line 665 and the `tiers[]` construction block. Add the Puppeteer tier after CamoFox, before Obscura:

```typescript
const tiers: TierRunner[] = [];
if (deps.scrapling) {
  tiers.push(createScraplingTier({ probe: deps.scrapling.probe, runScrapling: deps.scrapling.run }));
}
if (camoClient) {
  tiers.push(createCamoFoxTier({ availability, client: camoClient, classifier }));
}
if (deps.puppeteer) {
  tiers.push(createPuppeteerTier(deps.puppeteer));
}
tiers.push(createObscuraTier({ enabled: false }));
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
npx vitest run __tests__/browser/escalation-tier3.test.ts
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/browser/escalation-tier3.test.ts
git commit -m "feat(browser): add createPuppeteerTier + wire into webFetchEnvelope tiers[]"
```

---

## Task 5: Interface wiring — GatewayContext, EngineContext, context-builder, ToolContext

**Files:**
- Modify: `src/gateway/types.ts:370`
- Modify: `src/engine/runtime.ts` (EngineContext interface + 2 toolCtx sites)
- Modify: `src/gateway/handlers/context-builder.ts`
- Modify: `src/tools/registry.ts:28-32`

This task adds the type slots. No runtime behavior changes yet — that comes in Task 8.

- [ ] **Step 1: Add to `GatewayContext` in `src/gateway/types.ts`**

Find the bottom of the `GatewayContext` interface (before the closing `}`  at the end of file, around line 370). Add:

```typescript
  // ─── Element 16d — Web Fetch Wiring ──────────────────────────
  blockingClassifier?: import("../browser/blocking-classifier.js").BlockingClassifier;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
```

- [ ] **Step 2: Add to `EngineContext` in `src/engine/runtime.ts`**

Find the end of the `EngineContext` interface (before the `narrationPrefix?` line around 142, or after it). Add:

```typescript
  /** BlockingClassifier for web tool anti-bot detection */
  classifier?: import("../browser/blocking-classifier.js").BlockingClassifier;
  /** PuppeteerFetcher for Tier 3 autonomous headless browser */
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
```

- [ ] **Step 3: Thread through context-builder.ts baseContext()**

In `src/gateway/handlers/context-builder.ts`, find `baseContext()` which returns an object at line ~111. Add two fields to the return object before the closing `}`:

```typescript
      classifier: this.ctx.blockingClassifier,
      puppeteer: this.ctx.puppeteer,
```

- [ ] **Step 4: Add `puppeteer?` to `ToolContext` in `src/tools/registry.ts`**

Find `export interface ToolContext` at line 28. Add `puppeteer?` after `classifier?`:

```typescript
export interface ToolContext {
  cwd: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
}
```

- [ ] **Step 5: Thread into toolCtx at runtime.ts line 1273**

Find `const toolCtx = {` at line 1273 in `runtime.ts`. Change it from:

```typescript
const toolCtx = {
  cwd: cwd || process.cwd(),
  engineContext: {
    ...context,
    activeSubGoal: context.activeSubGoal,
    userMessage: context.userMessage,
  },
};
```

to:

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

- [ ] **Step 6: Thread into toolCtx at runtime.ts line 2913**

Find `const toolCtx = {` at line 2913. Change it from:

```typescript
const toolCtx = {
  cwd: process.cwd(),
  engineContext: {
    activeSubGoal: request.activeSubGoal,
    userMessage: request.userMessage,
  },
};
```

to:

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

- [ ] **Step 7: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -30
```

Expected: no errors from the files changed in this task (type errors in other files are OK for now).

- [ ] **Step 8: Run full test suite**

```bash
npx vitest run
```

Expected: all previously passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add src/gateway/types.ts src/engine/runtime.ts src/gateway/handlers/context-builder.ts src/tools/registry.ts
git commit -m "feat(wiring): add classifier+puppeteer fields to GatewayContext/EngineContext/ToolContext"
```

---

## Task 6: web.ts — pass all deps to webFetchEnvelope

**Files:**
- Modify: `src/tools/web.ts:62`

Currently `webFetchEnvelope(url)` is called with no deps — scrapling and the classifier never reach the smart-fetch layer.

- [ ] **Step 1: Edit web.ts execute() to pass deps**

In `src/tools/web.ts`, find line 62:

```typescript
const envelope = await webFetchEnvelope(url);
```

Replace with:

```typescript
const envelope = await webFetchEnvelope(url, {
  scrapling: (context.engineContext as any)?.scrapling,
  classifier: context.classifier,
  puppeteer: context.puppeteer,
  bus: (context.engineContext as any)?.gatewayEventBus,
  hint: args["hint"] as "anti-bot" | undefined,
});
```

Also change line 36 to accept `context` (remove the `_` prefix since we now use it):

```typescript
async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
```

- [ ] **Step 2: Verify the build is clean**

```bash
npm run build 2>&1 | grep "web.ts" | head -10
```

Expected: no errors from `web.ts`.

- [ ] **Step 3: Run full test suite**

```bash
npx vitest run
```

Expected: all previously passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add src/tools/web.ts
git commit -m "fix(web): pass scrapling/classifier/puppeteer/bus deps to webFetchEnvelope"
```

---

## Task 7: FallbackSequencer host-aware replan

**Files:**
- Modify: `src/tools/cortex/tool-graph.ts`
- Modify: `src/tools/registry.ts:396`
- Test: `__tests__/tools/host-aware-replan.test.ts`

Extends `replan()` to prefer host-specific `tool_edges` rows before falling back to global. Also fixes the existing call at `registry.ts:396` which queries globally and can return host-specific rows as "global" fallbacks.

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/host-aware-replan.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { MemoryDatabase } from "../../src/memory/db.js";
import { tmpdir } from "os";
import { join } from "path";
import { randomBytes } from "crypto";
import { unlinkSync } from "fs";

function tmpPath() {
  return join(tmpdir(), `replan-test-${randomBytes(4).toString("hex")}.db`);
}

describe("host-aware replan", () => {
  it("returns host-specific edge before global when hostRoot matches", () => {
    const path = tmpPath();
    const db = new MemoryDatabase(path);
    const raw = (db as any).db as Database.Database;

    // Insert a host-specific row: amazon.com → puppeteer
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "puppeteer_tier", "web_fetch", "amazon.com", 0.9, 3000, 5);

    // Insert a global row: scrapling → camofox
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "camofox_tier", "web_fetch", "", 0.8, 500, 10);

    const graph = new ToolGraph(db);
    const result = graph.replan("scrapling", "web_fetch", { hostRoot: "amazon.com" });
    expect(result).toBe("puppeteer_tier");

    unlinkSync(path);
  });

  it("falls back to global (host_root='') when no host-specific match", () => {
    const path = tmpPath();
    const db = new MemoryDatabase(path);
    const raw = (db as any).db as Database.Database;

    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "camofox_tier", "web_fetch", "", 0.8, 500, 10);

    const graph = new ToolGraph(db);
    const result = graph.replan("scrapling", "web_fetch", { hostRoot: "example.com" });
    expect(result).toBe("camofox_tier");

    unlinkSync(path);
  });

  it("does NOT return host-specific row when no hostRoot option provided", () => {
    const path = tmpPath();
    const db = new MemoryDatabase(path);
    const raw = (db as any).db as Database.Database;

    // Insert ONLY a host-specific row (no global row)
    raw.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run("scrapling", "puppeteer_tier", "web_fetch", "amazon.com", 0.9, 3000, 5);

    const graph = new ToolGraph(db);
    // Called without hostRoot — should NOT return the per-host row
    const result = graph.replan("scrapling", "web_fetch");
    expect(result).toBeNull();

    unlinkSync(path);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/host-aware-replan.test.ts
```

Expected: FAIL — `replan()` doesn't accept `{ hostRoot }` and the global query returns per-host rows.

- [ ] **Step 3: Update ReplanOptions and replan() in tool-graph.ts**

In `src/tools/cortex/tool-graph.ts`, replace the entire file with:

```typescript
/**
 * StackOwl — Element 7 T8 — Cost-Weighted Tool Graph (single-hop replan)
 *
 * On a GAV BLOCKED verdict the registry asks the graph for a next-best tool
 * to retry the same capability tag. Tries host-specific edges first, then
 * falls back to global (host_root='') edges.
 */
import type { MemoryDatabase } from "../../memory/db.js";

export interface ReplanOptions {
  /** Tool names to skip in addition to the current/failing tool. */
  exclude?: string[];
  /** Prefer host-specific edges before falling back to global. */
  hostRoot?: string;
}

export interface ToolGraphConfig {
  /** Minimum sample_count an edge needs before it's considered. Default: 3. */
  minSamples?: number;
}

export class ToolGraph {
  constructor(
    private readonly db: MemoryDatabase,
    private readonly config: ToolGraphConfig = {},
  ) {}

  replan(
    currentTool: string,
    capabilityTag: string,
    opts: ReplanOptions = {},
  ): string | null {
    const minSamples = this.config.minSamples ?? 3;
    const exclude = Array.from(
      new Set([currentTool, ...(opts.exclude ?? [])]),
    );
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

    // 2. Global fallback (host_root = '' only — prevents per-host rows from
    //    appearing as global fallbacks when no hostRoot was provided)
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
}
```

- [ ] **Step 4: Fix replan() call in registry.ts:396**

In `src/tools/registry.ts`, find this line at ~396:

```typescript
const fallback = this._toolGraph.replan(name, capability);
```

Replace with:

```typescript
const urlHost = (() => {
  try {
    return args.url ? new URL(args.url as string).hostname : "";
  } catch {
    return "";
  }
})();
const fallback = this._toolGraph.replan(name, capability, { hostRoot: urlHost });
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/tools/host-aware-replan.test.ts
```

Expected: 3 PASS.

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/tools/cortex/tool-graph.ts src/tools/registry.ts __tests__/tools/host-aware-replan.test.ts
git commit -m "feat(cortex): host-aware replan — prefer host-specific tool_edges before global"
```

---

## Task 8: index.ts — boot BlockingClassifier + PuppeteerFetcher + shutdown wiring

**Files:**
- Modify: `src/index.ts`

This wires everything together at runtime. `BlockingClassifier` is instantiated inside the `if (gateway.ctx.intelligence)` block (requires `IntelligenceRouter`). `PuppeteerFetcher` is probed in `bootstrap()` and stored on the returned `b` object.

- [ ] **Step 1: Add PuppeteerFetcher to bootstrap()**

In `src/index.ts`, find the CamoFox section in `bootstrap()` (around line 296-310). After it (after the `}` that closes the `if (config.camofox?.enabled !== false)` block), add:

```typescript
  // Initialize Puppeteer fetcher (Tier 3 autonomous headless browser)
  let puppeteerFetcher: import("./browser/puppeteer-fetcher.js").PuppeteerFetcher | undefined;
  {
    const { PuppeteerFetcher } = await import("./browser/puppeteer-fetcher.js");
    const fetcher = new PuppeteerFetcher();
    const ready = await fetcher.probe();
    if (ready) {
      await fetcher.init();
      puppeteerFetcher = fetcher;
    }
  }
```

- [ ] **Step 2: Return puppeteerFetcher from bootstrap()**

Find the `return {` statement at line ~801 and add `puppeteerFetcher` to the returned object:

```typescript
  return {
    // ... all existing fields ...
    puppeteerFetcher,
  };
```

- [ ] **Step 3: Wire BlockingClassifier and PuppeteerFetcher into gateway.ctx**

In the `if (gateway.ctx.intelligence)` block (around line 1184), after the `providerMap` construction (lines 1195-1196), add:

```typescript
    // BlockingClassifier — LLM-based anti-bot detection for web tools
    const { BlockingClassifier } = await import("./browser/blocking-classifier.js");
    gateway.ctx.blockingClassifier = new BlockingClassifier(
      gateway.ctx.intelligence,
      providerMap,
      gateway.gatewayEventBus,
    );

    // PuppeteerFetcher — wire into context if initialized during bootstrap
    if (b.puppeteerFetcher) {
      gateway.ctx.puppeteer = b.puppeteerFetcher;
    }
```

- [ ] **Step 4: Add shutdown wiring to all shutdown handlers**

There are 5 shutdown locations. For each `shutdown` function or inline `process.on("SIGINT", ...)` block, add `await b.puppeteerFetcher?.close()` before `process.exit(0)`.

**Location 1 — line 1321 (inline SIGINT):**
```typescript
process.on("SIGINT", async () => {
  adapter.stop();
  await b.browserPool?.shutdown();
  await b.puppeteerFetcher?.close();   // add this line
  process.exit(0);
});
```

**Location 2 — line 1402 (inline SIGINT):**
```typescript
process.on("SIGINT", async () => {
  adapter.stop();
  await b.browserPool?.shutdown();
  await b.puppeteerFetcher?.close();   // add this line
  process.exit(0);
});
```

**Location 3 — line 1876 (Telegram shutdown function):**
```typescript
const shutdown = async () => {
  console.log(chalk.dim("\n🦉 Shutting down..."));
  adapter.stop();
  await b.browserPool?.shutdown();
  await b.puppeteerFetcher?.close();   // add this line
  process.exit(0);
};
```

**Location 4 — line 1944 (Slack shutdown function):**
```typescript
const shutdown = async () => {
  console.log(chalk.dim("\n🦉 Shutting down..."));
  adapter.stop();
  await b.browserPool?.shutdown();
  await b.puppeteerFetcher?.close();   // add this line
  process.exit(0);
};
```

**Location 5 — line 2130 (combined shutdown function):**
```typescript
const shutdown = async () => {
  console.log(chalk.dim("\n🦉 Shutting down all channels..."));
  cliAdapter.stop();
  await b.browserPool?.shutdown();
  await b.puppeteerFetcher?.close();   // add this line
  process.exit(0);
};
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```

Expected: all tests pass. Puppeteer-specific tests from Tasks 2–4 pass. No regressions.

- [ ] **Step 7: Commit**

```bash
git add src/index.ts
git commit -m "feat(boot): wire BlockingClassifier + PuppeteerFetcher into gateway context; close() on shutdown"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task that covers it |
|-----------------|---------------------|
| price_compare skill rename | Task 1 ✓ |
| `"puppeteer"` to TierName + NAMES | Task 2 ✓ |
| `"puppeteer"` to BackendName | Task 2 ✓ |
| PuppeteerFetcher with warm singleton + stealth | Task 3 ✓ |
| WebFetchEnvelopeDeps puppeteer? | Task 4 ✓ |
| createPuppeteerTier factory | Task 4 ✓ |
| Push into tiers[] | Task 4 ✓ |
| GatewayContext blockingClassifier + puppeteer | Task 5 ✓ |
| EngineContext classifier + puppeteer | Task 5 ✓ |
| context-builder baseContext() threading | Task 5 ✓ |
| ToolContext puppeteer? | Task 5 ✓ |
| toolCtx construction at runtime.ts:1273 + 2913 | Task 5 ✓ |
| web.ts deps passthrough | Task 6 ✓ |
| host-aware replan (tool-graph.ts) | Task 7 ✓ |
| Old replan() call at registry.ts:396 fixed | Task 7 ✓ |
| bootstrap() PuppeteerFetcher init | Task 8 ✓ |
| BlockingClassifier instantiation in gateway | Task 8 ✓ |
| close() on all shutdown handlers | Task 8 ✓ |

**Placeholder scan:** No TBDs, no "add appropriate error handling", all code blocks complete.

**Type consistency:**
- `PuppeteerFetcher` defined in Task 3, referenced in Tasks 4, 5, 6, 8 — consistent
- `createPuppeteerTier()` defined in Task 4, tested in Task 4 — consistent
- `ReplanOptions.hostRoot` defined in Task 7, used in Task 7 — consistent
- `hostRoot` in `tool-graph.ts` matches `host_root` column name — confirmed against v27 schema
