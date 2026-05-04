# Element 16 — Web Browsing Honesty & Wiring (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web-fetch umbrella honest. Tools return structured `WebToolResult` envelopes; all 3 tiers actually fire (CamoFox promoted from ghost Tier 4 to primary Tier 2; Scrapling wired as Tier 3); the LLM sees per-tier truth via `<tool_attempt_summary>` instead of a "BLOCKED:" narrative; and the assistant stops declaring "I'm blocked" before it has actually tried everything.

**Architecture:** 3-tier `smart-fetch.ts` (`http → camofox → scrapling`). `live-browser` stays peer to umbrella (interactive only). `WebToolResult` envelope replaces narrative strings. `BlockingClassifier` (IntelligenceRouter cheap-tier with LRU + fail-open) replaces 16 hardcoded keywords. `RuntimeAvailability` map (`~/.stackowl/runtime-availability.json`) tracks installed backends. Install moves from `start.sh:270–333` (deleted) into the onboarding wizard + a new `stackowl backends` CLI subcommand.

**Tech Stack:** TypeScript (strict, ES2023, NodeNext), Vitest, better-sqlite3, IntelligenceRouter, GoalVerifier, ToolTracker, FallbackSequencer, GatewayEventBus, narration-formatter (existing primitives — compose, do not duplicate).

---

## File Map

| Path | Status | Responsibility |
|------|--------|----------------|
| `src/browser/envelope.ts` | NEW | Envelope types + `serializeWebToolResult` / `parseWebToolResult` / `isWebToolResult` / `buildAttemptSummaryXml`. |
| `src/runtime/availability.ts` | NEW | `RuntimeAvailability` class — load/update/isReady/probeAll for camofox / scrapling / live-browser. |
| `src/browser/blocking-classifier.ts` | NEW | `BlockingClassifier` — IntelligenceRouter cheap-tier classification, LRU cache, fail-open. |
| `src/browser/smart-fetch.ts` | MODIFIED | Refactor to 3-tier dispatcher; delete keyword block (lines 101–147); honest `unavailable` outcomes; bus events. |
| `src/browser/camofox-client.ts` | KEEP | No surface change. |
| `src/tools/web.ts` | MODIFIED | Replace BLOCKED narrative (lines 56–65) with envelope. |
| `src/tools/web-unified.ts` | MODIFIED | Add `hint?: 'anti-bot'` param; envelope passthrough. |
| `src/tools/web-scrapling.ts` | MODIFIED | Lazy `probeReadiness()`; return envelope; suggest install on probe failure. |
| `src/tools/camofox.ts` | MODIFIED | Stay `deprecated:true`; consume `RuntimeAvailability`; envelope on errors. |
| `src/tools/registry.ts` | MODIFIED | Lines 411–413: parse envelope; emit `<tool_attempt_summary>`; pass `error.code` to GoalVerifier. |
| `src/tools/goal-verifier.ts` | MODIFIED | SYSTEM_PROMPT cue update; rebuild `userContent` from envelope. |
| `src/tools/tracker.ts` | MODIFIED | Additive `attemptMetadata` recording; subscribe to bus. |
| `src/memory/db.ts` | MODIFIED | Schema bump: `attempt_metadata` JSON column on `tool_executions` (additive). |
| `src/engine/runtime.ts` | MODIFIED | Rewrite Anti-Bot Override directive (line 2367) — generic, envelope-driven. |
| `src/index.ts` | MODIFIED | `initCamoFox()` becomes probe-and-record only (lines 266–273). |
| `src/cli/onboarding.ts` | MODIFIED | New "Stealth web backends" subsection in Section D. |
| `src/cli/commands.ts` | MODIFIED | `stackowl backends list/install/repair/stats` subcommand. |
| `src/gateway/narration-formatter.ts` | MODIFIED | `web:tier_attempted` / `web:tier_blocked` / `web:escalating` arms. |
| `src/gateway/event-bus.ts` | MODIFIED | Four new `web:*` event variants. |
| `start.sh` | MODIFIED | Delete lines 270–333 (camofox install block). |
| `package.json` | MODIFIED | Add `camofox-browser` to `optionalDependencies`. |
| `__tests__/web-envelope.test.ts` | NEW | Envelope round-trip + alias injection + schema. |
| `__tests__/web-runtime-availability.test.ts` | NEW | Availability map tests. |
| `__tests__/web-blocking-classifier.test.ts` | NEW | Classifier behaviour with mocked router. |
| `__tests__/web-smart-fetch.test.ts` | NEW | 3-tier dispatcher unit tests. |
| `__tests__/web-honesty-integration.test.ts` | NEW | End-to-end: 3 tiers mocked, envelope+tracker+narration parity. |

**New file count: 3 (`envelope.ts`, `availability.ts`, `blocking-classifier.ts`)** — within the 3-cap from the spec.

---

## Acceptance Criteria (mirror of spec §11)

1. `grep -r "I'm blocked" src/` returns no narrative-string asserts.
2. `stackowl backends stats` shows non-zero `Tier 2 (camofox)` after the user installs it.
3. Manually uninstalling camofox surfaces `error.code: ALL_TIERS_UNAVAILABLE` with `outcome: 'unavailable'` for camofox in `attemptedTiers[]`.
4. Same fetch on CLI / Telegram / Slack / Web shows identical attempt-list information, channel-appropriate form.
5. `grep -rn "cloudflare\|captcha\|access denied" src/browser/` returns matches only in test fixtures and the classifier's prompt template.
6. `grep -rn "camofox\|scrapling" start.sh` returns empty.
7. No silent-skip path. Every tier attempt produces a `TierAttempt` record.
8. `stackowl backends stats` produces the data needed to decide Phase B kickoff.

---

## Phase Pre — Worktree

### Task 0: Worktree setup

**Files:** none

- [ ] **Step 1: Create isolated worktree**

Run:
```bash
git worktree add .worktrees/element-16-web -b feature/element-16-web main
cd .worktrees/element-16-web
npm install
```
Expected: clean install, baseline tests pass.

- [ ] **Step 2: Verify baseline**

Run: `npm test`
Expected: 0 failures.

- [ ] **Step 3: No commit — proceed to Task 1.**

---

## Phase A — Envelope contract + scaffolding

### Task 1: Envelope types & helpers

**Files:**
- Create: `src/browser/envelope.ts`
- Create: `__tests__/web-envelope.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-envelope.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import {
  serializeWebToolResult,
  parseWebToolResult,
  isWebToolResult,
  buildAttemptSummaryXml,
  type WebToolResult,
} from "../src/browser/envelope.js";

describe("WebToolResult — envelope", () => {
  it("round-trips a success result", () => {
    const r: WebToolResult = {
      success: true,
      data: { kind: "page", url: "https://x.com", title: "X", content: "hello" },
    };
    const s = serializeWebToolResult(r);
    const back = parseWebToolResult(s);
    expect(back).toEqual(r);
  });

  it("injects 'BLOCKED:' alias into error.message for BLOCKED_BY_ANTI_BOT", () => {
    const r: WebToolResult = {
      success: false,
      error: {
        code: "BLOCKED_BY_ANTI_BOT",
        message: "Cloudflare challenge",
        attemptedTiers: [
          { tier: 1, name: "http", durationMs: 100, outcome: "blocked", blockedReason: "cloudflare", httpStatus: 403 },
        ],
      },
    };
    const s = serializeWebToolResult(r);
    const back = parseWebToolResult(s)!;
    expect(back.success).toBe(false);
    if (!back.success) {
      expect(back.error.message.startsWith("BLOCKED:")).toBe(true);
    }
  });

  it("injects 'BLOCKED:' alias for ALL_TIERS_UNAVAILABLE", () => {
    const r: WebToolResult = {
      success: false,
      error: {
        code: "ALL_TIERS_UNAVAILABLE",
        message: "no backends ready",
        attemptedTiers: [],
      },
    };
    const back = parseWebToolResult(serializeWebToolResult(r))!;
    if (!back.success) {
      expect(back.error.message.startsWith("BLOCKED:")).toBe(true);
    }
  });

  it("returns null on invalid JSON", () => {
    expect(parseWebToolResult("not json")).toBeNull();
  });

  it("returns null when error.code is not in the closed enum", () => {
    const fake = JSON.stringify({ success: false, error: { code: "WHATEVER", message: "", attemptedTiers: [] } });
    expect(parseWebToolResult(fake)).toBeNull();
  });

  it("returns null when attemptedTiers is not an array", () => {
    const fake = JSON.stringify({ success: false, error: { code: "TIMEOUT", message: "", attemptedTiers: "no" } });
    expect(parseWebToolResult(fake)).toBeNull();
  });

  it("isWebToolResult guards arbitrary objects", () => {
    expect(isWebToolResult({ foo: 1 })).toBe(false);
    expect(isWebToolResult({ success: true, data: { kind: "page", url: "x", content: "y" } })).toBe(true);
  });

  it("buildAttemptSummaryXml renders one <tier> per attempt", () => {
    const r: WebToolResult = {
      success: false,
      error: {
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [
          { tier: 1, name: "http", durationMs: 80, outcome: "blocked", blockedReason: "cloudflare", httpStatus: 403 },
          { tier: 2, name: "camofox", durationMs: 1200, outcome: "blocked", blockedReason: "cloudflare" },
          { tier: 3, name: "scrapling", durationMs: 2200, outcome: "success" },
        ],
      },
    };
    const xml = buildAttemptSummaryXml(r);
    expect(xml).toContain('<tool_attempt_summary code="BLOCKED_BY_ANTI_BOT">');
    expect(xml).toContain('<tier n="1" name="http" outcome="blocked" reason="cloudflare" httpStatus="403" durationMs="80"/>');
    expect(xml).toContain('<tier n="3" name="scrapling" outcome="success" durationMs="2200"/>');
    expect(xml).toContain("</tool_attempt_summary>");
  });
});
```

- [ ] **Step 2: Run test — confirm FAIL**

Run: `npx vitest run __tests__/web-envelope.test.ts`
Expected: FAIL — `Cannot find module '../src/browser/envelope.js'`.

- [ ] **Step 3: Implement `src/browser/envelope.ts`**

Create `src/browser/envelope.ts`:
```typescript
/**
 * StackOwl — Web Tool Envelope
 *
 * Single source of truth for the WebToolResult contract every web tool returns.
 * The registry parses the JSON; non-web tools are unaffected.
 */

export type WebToolErrorCode =
  | "BLOCKED_BY_ANTI_BOT"
  | "PAYWALL"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "NOT_FOUND"
  | "INVALID_URL"
  | "ALL_TIERS_UNAVAILABLE"
  | "INTERNAL_ERROR";

export type TierName = "http" | "camofox" | "scrapling";

export type TierOutcome =
  | "success"
  | "blocked"
  | "timeout"
  | "unavailable"
  | "error"
  | "skipped-by-hint";

export type BlockedReason =
  | "cloudflare"
  | "captcha"
  | "paywall"
  | "rate-limit"
  | "access-denied"
  | "other";

export interface TierAttempt {
  tier: number;
  name: TierName;
  durationMs: number;
  outcome: TierOutcome;
  blockedReason?: BlockedReason;
  httpStatus?: number;
}

export interface WebToolError {
  code: WebToolErrorCode;
  message: string;
  attemptedTiers: TierAttempt[];
  suggestedEscalation?: string;
}

export type WebToolData =
  | { kind: "page"; url: string; title?: string; content: string; contentType?: string }
  | { kind: "search"; query: string; results: Array<{ title: string; url: string; snippet?: string }> };

export type WebToolResult =
  | { success: true; data: WebToolData }
  | { success: false; error: WebToolError };

const ERROR_CODES: ReadonlySet<string> = new Set<WebToolErrorCode>([
  "BLOCKED_BY_ANTI_BOT",
  "PAYWALL",
  "RATE_LIMITED",
  "TIMEOUT",
  "NOT_FOUND",
  "INVALID_URL",
  "ALL_TIERS_UNAVAILABLE",
  "INTERNAL_ERROR",
]);

const NAMES: ReadonlySet<TierName> = new Set<TierName>(["http", "camofox", "scrapling"]);
const OUTCOMES: ReadonlySet<TierOutcome> = new Set<TierOutcome>([
  "success", "blocked", "timeout", "unavailable", "error", "skipped-by-hint",
]);

const ALIAS_CODES = new Set<WebToolErrorCode>(["BLOCKED_BY_ANTI_BOT", "ALL_TIERS_UNAVAILABLE"]);

export function serializeWebToolResult(result: WebToolResult): string {
  if (!result.success) {
    const needsAlias = ALIAS_CODES.has(result.error.code) && !result.error.message.startsWith("BLOCKED:");
    if (needsAlias) {
      return JSON.stringify({
        success: false,
        error: { ...result.error, message: `BLOCKED: ${result.error.message}` },
      });
    }
  }
  return JSON.stringify(result);
}

export function parseWebToolResult(s: string): WebToolResult | null {
  let parsed: unknown;
  try { parsed = JSON.parse(s); } catch { return null; }
  if (!isWebToolResult(parsed)) return null;
  return parsed;
}

export function isWebToolResult(v: unknown): v is WebToolResult {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  if (o.success === true) {
    const d = o.data as Record<string, unknown> | undefined;
    if (!d || typeof d !== "object") return false;
    if (d.kind === "page") return typeof d.url === "string" && typeof d.content === "string";
    if (d.kind === "search") return typeof d.query === "string" && Array.isArray(d.results);
    return false;
  }
  if (o.success === false) return isWebToolError(o.error);
  return false;
}

export function isWebToolError(v: unknown): v is WebToolError {
  if (!v || typeof v !== "object") return false;
  const e = v as Record<string, unknown>;
  if (typeof e.code !== "string" || !ERROR_CODES.has(e.code)) return false;
  if (typeof e.message !== "string") return false;
  if (!Array.isArray(e.attemptedTiers)) return false;
  for (const t of e.attemptedTiers) {
    if (!t || typeof t !== "object") return false;
    const tt = t as Record<string, unknown>;
    if (typeof tt.tier !== "number") return false;
    if (typeof tt.name !== "string" || !NAMES.has(tt.name as TierName)) return false;
    if (typeof tt.outcome !== "string" || !OUTCOMES.has(tt.outcome as TierOutcome)) return false;
    if (typeof tt.durationMs !== "number") return false;
  }
  return true;
}

export function buildAttemptSummaryXml(result: WebToolResult): string {
  if (result.success) return "";
  const lines: string[] = [];
  lines.push(`<tool_attempt_summary code="${escapeAttr(result.error.code)}">`);
  for (const t of result.error.attemptedTiers) {
    const reason = t.blockedReason ? ` reason="${escapeAttr(t.blockedReason)}"` : "";
    const status = t.httpStatus !== undefined ? ` httpStatus="${t.httpStatus}"` : "";
    lines.push(
      `  <tier n="${t.tier}" name="${escapeAttr(t.name)}" outcome="${escapeAttr(t.outcome)}"${reason}${status} durationMs="${t.durationMs}"/>`,
    );
  }
  if (result.error.suggestedEscalation) {
    lines.push(`  <suggestion>${escapeText(result.error.suggestedEscalation)}</suggestion>`);
  }
  lines.push(`</tool_attempt_summary>`);
  return lines.join("\n");
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
```

- [ ] **Step 4: Run test — confirm PASS**

Run: `npx vitest run __tests__/web-envelope.test.ts`
Expected: 8/8 passing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/envelope.ts __tests__/web-envelope.test.ts
git commit -m "feat(browser): WebToolResult envelope types + serialize/parse helpers"
```

---

### Task 2: Runtime availability map

**Files:**
- Create: `src/runtime/availability.ts`
- Create: `__tests__/web-runtime-availability.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-runtime-availability.test.ts`:
```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RuntimeAvailability } from "../src/runtime/availability.js";

describe("RuntimeAvailability", () => {
  let dir: string;
  let path: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "stackowl-avail-"));
    path = join(dir, "runtime-availability.json");
  });

  it("creates a default file when none exists", async () => {
    const ra = new RuntimeAvailability(path);
    const map = await ra.load();
    expect(existsSync(path)).toBe(true);
    expect(map.camofox.installed).toBe(false);
    expect(map.scrapling.installed).toBe(false);
    expect(map["live-browser"].installed).toBe(false);
  });

  it("update() persists a backend status", async () => {
    const ra = new RuntimeAvailability(path);
    await ra.load();
    await ra.update("camofox", { installed: true, version: "1.2.3", ready: true, lastProbe: new Date().toISOString() });
    const back = JSON.parse(readFileSync(path, "utf8"));
    expect(back.camofox.installed).toBe(true);
    expect(back.camofox.version).toBe("1.2.3");
    expect(back.camofox.ready).toBe(true);
  });

  it("isReady() returns the ready flag", async () => {
    const ra = new RuntimeAvailability(path);
    await ra.load();
    await ra.update("scrapling", { installed: true, ready: true, lastProbe: new Date().toISOString() });
    expect(await ra.isReady("scrapling")).toBe(true);
    expect(await ra.isReady("camofox")).toBe(false);
  });

  it("probeAll() invokes all backend probes and writes results", async () => {
    const ra = new RuntimeAvailability(path);
    // Inject a stub probe map (constructor optional second arg)
    const probes = {
      camofox: async () => ({ installed: true, ready: true, version: "0.9" }),
      scrapling: async () => ({ installed: false, ready: false, lastError: "import failed" }),
      "live-browser": async () => ({ installed: true, ready: true }),
    };
    const ra2 = new RuntimeAvailability(path, probes);
    const map = await ra2.probeAll();
    expect(map.camofox.ready).toBe(true);
    expect(map.scrapling.installed).toBe(false);
    expect(map.scrapling.lastError).toBe("import failed");
    expect(map["live-browser"].ready).toBe(true);
    const back = JSON.parse(readFileSync(path, "utf8"));
    expect(back.scrapling.lastError).toBe("import failed");
  });

  afterEach?.(() => rmSync(dir, { recursive: true, force: true }));
});
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `npx vitest run __tests__/web-runtime-availability.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/runtime/availability.ts`**

```typescript
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { homedir } from "node:os";
import { existsSync } from "node:fs";

export type BackendName = "camofox" | "scrapling" | "live-browser";

export interface BackendStatus {
  installed: boolean;
  version?: string;
  lastProbe: string;
  ready: boolean;
  lastError?: string;
}

export type AvailabilityMap = Record<BackendName, BackendStatus>;

export type ProbeFn = () => Promise<Partial<BackendStatus>>;
export type ProbeMap = Record<BackendName, ProbeFn>;

const DEFAULT_PATH = `${homedir()}/.stackowl/runtime-availability.json`;

function emptyStatus(): BackendStatus {
  return { installed: false, ready: false, lastProbe: new Date(0).toISOString() };
}
function emptyMap(): AvailabilityMap {
  return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus() };
}

export class RuntimeAvailability {
  constructor(private path: string = DEFAULT_PATH, private probes?: ProbeMap) {}

  async load(): Promise<AvailabilityMap> {
    if (!existsSync(this.path)) {
      const fresh = emptyMap();
      await this.write(fresh);
      return fresh;
    }
    try {
      const raw = await readFile(this.path, "utf8");
      const parsed = JSON.parse(raw) as Partial<AvailabilityMap>;
      return { ...emptyMap(), ...parsed };
    } catch {
      const fresh = emptyMap();
      await this.write(fresh);
      return fresh;
    }
  }

  async update(backend: BackendName, status: Partial<BackendStatus>): Promise<void> {
    const map = await this.load();
    map[backend] = { ...map[backend], ...status, lastProbe: status.lastProbe ?? new Date().toISOString() };
    await this.write(map);
  }

  async isReady(backend: BackendName): Promise<boolean> {
    const map = await this.load();
    return Boolean(map[backend]?.ready);
  }

  async probeAll(): Promise<AvailabilityMap> {
    const map = await this.load();
    if (!this.probes) return map;
    const now = new Date().toISOString();
    for (const name of Object.keys(this.probes) as BackendName[]) {
      try {
        const partial = await this.probes[name]();
        map[name] = { ...map[name], ...partial, lastProbe: now };
      } catch (err) {
        map[name] = { ...map[name], installed: false, ready: false, lastProbe: now,
                      lastError: err instanceof Error ? err.message : String(err) };
      }
    }
    await this.write(map);
    return map;
  }

  private async write(map: AvailabilityMap): Promise<void> {
    const dir = dirname(this.path);
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(this.path, JSON.stringify(map, null, 2), "utf8");
  }
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-runtime-availability.test.ts`
Expected: 4/4 passing.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/availability.ts __tests__/web-runtime-availability.test.ts
git commit -m "feat(runtime): RuntimeAvailability map for stealth backend status"
```

---

### Task 3: BlockingClassifier (IntelligenceRouter cheap-tier)

**Files:**
- Create: `src/browser/blocking-classifier.ts`
- Create: `__tests__/web-blocking-classifier.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-blocking-classifier.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { BlockingClassifier } from "../src/browser/blocking-classifier.js";

function makeRouter(content: string, latencyMs = 5) {
  return {
    resolve: () => ({ provider: "stub", model: "tiny" }),
  } as unknown as import("../src/intelligence/router.js").IntelligenceRouter;
}
function makeProvider(content: string, latencyMs = 5) {
  return new Map([["stub", {
    name: "stub",
    chat: vi.fn(async () => {
      await new Promise(r => setTimeout(r, latencyMs));
      return { content };
    }),
  } as any]]);
}
const bus = { emit: vi.fn() } as any;

describe("BlockingClassifier", () => {
  it("calls cheap tier and returns parsed verdict", async () => {
    const c = new BlockingClassifier(makeRouter(""), makeProvider(`{"blocked":true,"reason":"cloudflare","confidence":0.9}`), bus);
    const v = await c.classify({ url: "https://x.com", httpStatus: 403, bodyPreview: "Just a moment", headers: {} });
    expect(v.blocked).toBe(true);
    expect(v.reason).toBe("cloudflare");
    expect(v.source).toBe("router");
  });

  it("returns cached result on second call (same key)", async () => {
    const providers = makeProvider(`{"blocked":true,"reason":"cloudflare","confidence":0.9}`);
    const c = new BlockingClassifier(makeRouter(""), providers, bus);
    const input = { url: "https://x.com", httpStatus: 403, bodyPreview: "Just a moment" };
    await c.classify(input);
    const v2 = await c.classify(input);
    expect(v2.source).toBe("cache");
    expect((providers.get("stub") as any).chat.mock.calls.length).toBe(1);
  });

  it("fails open on router timeout (>200ms)", async () => {
    const c = new BlockingClassifier(makeRouter(""), makeProvider(`{"blocked":true,"reason":"cloudflare","confidence":1}`, 500), bus);
    const v = await c.classify({ url: "https://x.com", httpStatus: 403, bodyPreview: "anything" });
    expect(v.blocked).toBe(false);
    expect(v.source).toBe("fallback");
  });

  it("fails open on invalid JSON", async () => {
    const c = new BlockingClassifier(makeRouter(""), makeProvider("not json"), bus);
    const v = await c.classify({ url: "https://x.com", httpStatus: 403, bodyPreview: "x" });
    expect(v.blocked).toBe(false);
    expect(v.source).toBe("fallback");
  });

  it("emits web:blocking_classified on every classification", async () => {
    const localBus = { emit: vi.fn() } as any;
    const c = new BlockingClassifier(makeRouter(""), makeProvider(`{"blocked":false,"reason":"other","confidence":0.5}`), localBus);
    await c.classify({ url: "https://x.com", httpStatus: 200, bodyPreview: "ok" });
    expect(localBus.emit).toHaveBeenCalledWith(expect.objectContaining({ type: "web:blocking_classified" }));
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `npx vitest run __tests__/web-blocking-classifier.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement classifier**

Create `src/browser/blocking-classifier.ts`:
```typescript
import { createHash } from "node:crypto";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider } from "../providers/base.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";

export interface ClassifyInput {
  url: string;
  httpStatus: number;
  bodyPreview: string;
  headers?: Record<string, string>;
}

export interface BlockingClassification {
  blocked: boolean;
  reason?: "cloudflare" | "captcha" | "paywall" | "rate-limit" | "access-denied" | "other";
  confidence: number;
  source: "cache" | "router" | "fallback";
}

interface CacheEntry { v: BlockingClassification; expiresAt: number }

const CLASSIFY_BUDGET_MS = 200;
const CACHE_TTL_MS = 60 * 60 * 1000;
const CACHE_CAP = 1000;

const SYSTEM_PROMPT = `You are a web-blocking classifier. Given an HTTP response (status, body preview, headers), reply ONLY with JSON:
{"blocked": boolean, "reason": "cloudflare"|"captcha"|"paywall"|"rate-limit"|"access-denied"|"other", "confidence": number}
Confidence is 0..1. If uncertain, set "blocked": false.`;

export class BlockingClassifier {
  private cache = new Map<string, CacheEntry>();

  constructor(
    private router: IntelligenceRouter,
    private providers: Map<string, ModelProvider>,
    private bus: GatewayEventBus,
  ) {}

  async classify(input: ClassifyInput): Promise<BlockingClassification> {
    const start = Date.now();
    const key = this.cacheKey(input);
    const hit = this.cache.get(key);
    if (hit && hit.expiresAt > Date.now()) {
      const v: BlockingClassification = { ...hit.v, source: "cache" };
      this.emit(input, v, Date.now() - start);
      return v;
    }

    let resolved: { provider: string; model: string };
    try { resolved = this.router.resolve("classification") as any; }
    catch { return this.fallback(input, start); }
    const provider = this.providers.get(resolved.provider);
    if (!provider) return this.fallback(input, start);

    const userContent = `URL: ${input.url}\nHTTP status: ${input.httpStatus}\nBody preview (first 2KB):\n${input.bodyPreview.slice(0, 2048)}`;
    const budget = new Promise<{ timeout: true }>(r => setTimeout(() => r({ timeout: true }), CLASSIFY_BUDGET_MS));
    const call = provider.chat([
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userContent },
    ], resolved.model, { temperature: 0 });

    let raced: any;
    try { raced = await Promise.race([call, budget]); }
    catch { return this.fallback(input, start); }
    if (raced && (raced as any).timeout) return this.fallback(input, start);

    const parsed = this.parse((raced as { content: string }).content);
    if (!parsed) return this.fallback(input, start);
    const v: BlockingClassification = { ...parsed, source: "router" };
    this.cacheSet(key, v);
    this.emit(input, v, Date.now() - start);
    return v;
  }

  private parse(s: string): Omit<BlockingClassification, "source"> | null {
    try {
      const m = s.match(/\{[\s\S]*\}/);
      if (!m) return null;
      const p = JSON.parse(m[0]) as any;
      if (typeof p.blocked !== "boolean") return null;
      const allowed = ["cloudflare","captcha","paywall","rate-limit","access-denied","other"];
      const reason = allowed.includes(p.reason) ? p.reason : "other";
      const confidence = typeof p.confidence === "number" ? Math.max(0, Math.min(1, p.confidence)) : 0;
      return { blocked: p.blocked, reason, confidence };
    } catch { return null; }
  }

  private fallback(input: ClassifyInput, start: number): BlockingClassification {
    const v: BlockingClassification = { blocked: false, confidence: 0, source: "fallback" };
    this.emit(input, v, Date.now() - start);
    return v;
  }

  private cacheKey(i: ClassifyInput): string {
    const host = new URL(i.url).host;
    const bodyHash = createHash("sha1").update(i.bodyPreview.slice(0, 2048)).digest("hex").slice(0, 12);
    return `${host}|${i.httpStatus}|${bodyHash}`;
  }

  private cacheSet(key: string, v: BlockingClassification): void {
    if (this.cache.size >= CACHE_CAP) {
      const first = this.cache.keys().next().value;
      if (first) this.cache.delete(first);
    }
    this.cache.set(key, { v, expiresAt: Date.now() + CACHE_TTL_MS });
  }

  private emit(input: ClassifyInput, v: BlockingClassification, latency: number): void {
    try {
      this.bus.emit({
        type: "web:blocking_classified",
        url: input.url, source: v.source, latency, blocked: v.blocked, reason: v.reason ?? null,
      } as any);
    } catch { /* fail-open on bus */ }
  }
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-blocking-classifier.test.ts`
Expected: 5/5 passing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/blocking-classifier.ts __tests__/web-blocking-classifier.test.ts
git commit -m "feat(browser): BlockingClassifier (cheap-tier router + LRU + fail-open)"
```

---

## Phase B — 3-tier dispatcher in `smart-fetch.ts`

### Task 4: TierRunner interface + dispatcher

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Create: `__tests__/web-smart-fetch.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-smart-fetch.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../src/browser/smart-fetch.js";

const noopBus = { emit: vi.fn() } as any;

describe("runEscalationChain — dispatcher", () => {
  it("returns success on first winning tier", async () => {
    const r1: TierRunner = {
      tier: 1, name: "http",
      isAvailable: () => true,
      run: async () => ({
        attempt: { tier: 1, name: "http", durationMs: 10, outcome: "success" },
        data: { kind: "page", url: "https://x.com", content: "hi" },
      }),
    };
    const r2: TierRunner = {
      tier: 2, name: "camofox", isAvailable: () => true,
      run: async () => { throw new Error("should not be called"); },
    };
    const result = await runEscalationChain([r1, r2], "https://x.com", { bus: noopBus });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.kind).toBe("page");
  });

  it("returns ALL_TIERS_UNAVAILABLE when no tier is available", async () => {
    const r1: TierRunner = { tier: 1, name: "http", isAvailable: () => false, run: async () => { throw new Error(); } };
    const r2: TierRunner = { tier: 2, name: "camofox", isAvailable: () => false, run: async () => { throw new Error(); } };
    const r3: TierRunner = { tier: 3, name: "scrapling", isAvailable: () => false, run: async () => { throw new Error(); } };
    const result = await runEscalationChain([r1, r2, r3], "https://x.com", { bus: noopBus });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.code).toBe("ALL_TIERS_UNAVAILABLE");
      expect(result.error.attemptedTiers.every(t => t.outcome === "unavailable")).toBe(true);
    }
  });

  it("collects every tier attempt in order", async () => {
    const r1: TierRunner = { tier:1, name:"http", isAvailable:()=>true,
      run: async () => ({ attempt:{ tier:1, name:"http", durationMs:5, outcome:"blocked", blockedReason:"cloudflare", httpStatus:403 } }) };
    const r2: TierRunner = { tier:2, name:"camofox", isAvailable:()=>true,
      run: async () => ({ attempt:{ tier:2, name:"camofox", durationMs:50, outcome:"blocked", blockedReason:"cloudflare" } }) };
    const r3: TierRunner = { tier:3, name:"scrapling", isAvailable:()=>true,
      run: async () => ({ attempt:{ tier:3, name:"scrapling", durationMs:100, outcome:"error" } }) };
    const result = await runEscalationChain([r1, r2, r3], "https://x.com", { bus: noopBus });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.attemptedTiers).toHaveLength(3);
      expect(result.error.attemptedTiers.map(t => t.outcome)).toEqual(["blocked","blocked","error"]);
    }
  });

  it("hint='anti-bot' marks tier 1 as skipped-by-hint", async () => {
    const r1: TierRunner = { tier:1, name:"http", isAvailable:()=>true,
      run: async () => { throw new Error("should not run"); } };
    const r2: TierRunner = { tier:2, name:"camofox", isAvailable:()=>true,
      run: async () => ({ attempt:{ tier:2, name:"camofox", durationMs:10, outcome:"success" },
                         data:{ kind:"page", url:"https://x.com", content:"ok" } }) };
    const r3: TierRunner = { tier:3, name:"scrapling", isAvailable:()=>true,
      run: async () => { throw new Error("should not run"); } };
    const result = await runEscalationChain([r1, r2, r3], "https://x.com", { bus: noopBus, hint: "anti-bot" });
    expect(result.success).toBe(true);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: FAIL — `runEscalationChain` is not exported.

- [ ] **Step 3: Add the dispatcher to `src/browser/smart-fetch.ts`**

At the bottom of `src/browser/smart-fetch.ts` (do not yet remove the legacy `webFetch`), append:
```typescript
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
    catch (err) {
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
```

Also add this `web:*` event union at the top of `src/gateway/event-bus.ts` (insert before `engine:turn_complete`):
```typescript
  | { type: "web:tier_attempted"; tier: number; name: string; url: string; startedAt: number }
  | { type: "web:tier_blocked"; tier: number; name: string; blockedReason: string; durationMs: number }
  | { type: "web:escalating"; fromTier: number; toTier: number; reason: string }
  | { type: "web:blocking_classified"; url: string; source: "cache" | "router" | "fallback"; latency: number; blocked: boolean; reason: string | null }
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: 4/4 passing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts src/gateway/event-bus.ts __tests__/web-smart-fetch.test.ts
git commit -m "feat(browser): TierRunner dispatcher with attempt-list assembly"
```

---

### Task 5: HTTP tier (Tier 1) as a TierRunner

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Modify: `__tests__/web-smart-fetch.test.ts`

- [ ] **Step 1: Append failing tests**

Append to `__tests__/web-smart-fetch.test.ts`:
```typescript
import { createHttpTier } from "../src/browser/smart-fetch.js";

describe("createHttpTier", () => {
  it("returns success without invoking classifier on clean 200", async () => {
    const classifier = { classify: vi.fn() } as any;
    const fetcher = vi.fn(async () => ({ status: 200, body: "<title>OK</title><body>hello</body>", contentType: "text/html" }));
    const tier = createHttpTier({ classifier, fetcher });
    const out = await tier.run("https://ok.example", { bus: noopBus });
    expect(out.attempt.outcome).toBe("success");
    expect(classifier.classify).not.toHaveBeenCalled();
  });

  it("invokes classifier on 403 and reports blocked", async () => {
    const classifier = { classify: vi.fn(async () => ({ blocked: true, reason: "cloudflare", source: "router", confidence: 0.9 })) } as any;
    const fetcher = vi.fn(async () => ({ status: 403, body: "Just a moment...", contentType: "text/html" }));
    const tier = createHttpTier({ classifier, fetcher });
    const out = await tier.run("https://block.example", { bus: noopBus });
    expect(classifier.classify).toHaveBeenCalledOnce();
    expect(out.attempt.outcome).toBe("blocked");
    expect(out.attempt.blockedReason).toBe("cloudflare");
    expect(out.data).toBeUndefined();
  });

  it("classifier 'not blocked' → returns success even with 403", async () => {
    const classifier = { classify: vi.fn(async () => ({ blocked: false, source: "router", confidence: 0.8 })) } as any;
    const fetcher = vi.fn(async () => ({ status: 403, body: "<body>real content</body>", contentType: "text/html" }));
    const tier = createHttpTier({ classifier, fetcher });
    const out = await tier.run("https://x.example", { bus: noopBus });
    expect(out.attempt.outcome).toBe("success");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `createHttpTier is not a function`.

- [ ] **Step 3: Implement `createHttpTier` in `src/browser/smart-fetch.ts`**

Append:
```typescript
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
      catch (err) {
        return { attempt: { tier: 1, name: "http", durationMs: Date.now() - t0, outcome: "timeout" } };
      }

      const trigger = TRIGGER_STATUSES.has(resp.status) || resp.body.length < 1024 || resp.status >= 500;
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
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: all passing (3 new + 4 prior = 7).

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/web-smart-fetch.test.ts
git commit -m "feat(browser): HTTP tier runner with classifier-driven blocking detection"
```

---

### Task 6: CamoFox tier (Tier 2)

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Modify: `__tests__/web-smart-fetch.test.ts`

- [ ] **Step 1: Append failing tests**

Append:
```typescript
import { createCamoFoxTier } from "../src/browser/smart-fetch.js";

describe("createCamoFoxTier", () => {
  it("reports unavailable when availability map says ready=false", async () => {
    const ra = { isReady: vi.fn(async () => false) } as any;
    const tier = createCamoFoxTier({ availability: ra, client: null as any });
    expect(await tier.isAvailable()).toBe(false);
  });

  it("returns success when client snapshot returns content", async () => {
    const ra = { isReady: vi.fn(async () => true) } as any;
    const client = {
      createTab: vi.fn(async () => ({ tabId: "t1" })),
      snapshot: vi.fn(async () => ({ url: "https://x.com", snapshot: "Body text" })),
      closeTab: vi.fn(async () => {}),
    } as any;
    const tier = createCamoFoxTier({ availability: ra, client });
    const out = await tier.run("https://x.com", { bus: noopBus });
    expect(out.attempt.outcome).toBe("success");
    expect(out.data?.kind).toBe("page");
  });

  it("reports outcome:blocked when classifier flags the snapshot", async () => {
    const ra = { isReady: vi.fn(async () => true) } as any;
    const client = {
      createTab: vi.fn(async () => ({ tabId: "t1" })),
      snapshot: vi.fn(async () => ({ url: "https://x.com", snapshot: "Just a moment..." })),
      closeTab: vi.fn(async () => {}),
    } as any;
    const classifier = { classify: vi.fn(async () => ({ blocked: true, reason: "cloudflare", source: "router", confidence: 0.9 })) } as any;
    const tier = createCamoFoxTier({ availability: ra, client, classifier });
    const out = await tier.run("https://x.com", { bus: noopBus });
    expect(out.attempt.outcome).toBe("blocked");
    expect(out.attempt.blockedReason).toBe("cloudflare");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `createCamoFoxTier is not a function`.

- [ ] **Step 3: Implement Tier 2**

Append to `src/browser/smart-fetch.ts`:
```typescript
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
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: 10 passing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/web-smart-fetch.test.ts
git commit -m "feat(browser): CamoFox tier runner consumes RuntimeAvailability"
```

---

### Task 7: Scrapling tier (Tier 3)

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Modify: `__tests__/web-smart-fetch.test.ts`

- [ ] **Step 1: Append failing tests**

Append:
```typescript
import { createScraplingTier } from "../src/browser/smart-fetch.js";

describe("createScraplingTier", () => {
  it("probes once per session; subsequent calls reuse cached probe", async () => {
    const probe = vi.fn(async () => ({ ok: true, version: "0.2" }));
    const runner = vi.fn(async () => ({ title: "OK", url: "https://x.com", content: "data" }));
    const tier = createScraplingTier({ probe, runScrapling: runner });
    expect(await tier.isAvailable()).toBe(true);
    await tier.run("https://x.com", { bus: noopBus });
    await tier.run("https://x.com", { bus: noopBus });
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("reports unavailable when probe fails and surfaces install hint", async () => {
    const probe = vi.fn(async () => ({ ok: false, error: "ModuleNotFoundError: scrapling" }));
    const tier = createScraplingTier({ probe, runScrapling: vi.fn() });
    expect(await tier.isAvailable()).toBe(false);
    // attemptUnavailable() returns the suggestion the dispatcher will use
    const hint = tier.installHint();
    expect(hint).toContain("pip install");
    expect(hint).toContain("scrapling");
  });

  it("returns success on subprocess result", async () => {
    const tier = createScraplingTier({
      probe: async () => ({ ok: true }),
      runScrapling: async () => ({ title: "T", url: "https://x.com", content: "C" }),
    });
    const out = await tier.run("https://x.com", { bus: noopBus });
    expect(out.attempt.outcome).toBe("success");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `createScraplingTier is not a function`.

- [ ] **Step 3: Implement Tier 3**

Append:
```typescript
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
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: 13 passing.

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/web-smart-fetch.test.ts
git commit -m "feat(browser): Scrapling tier with lazy session probe"
```

---

### Task 8: Delete the hardcoded keyword block

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Modify: `__tests__/web-smart-fetch.test.ts`

- [ ] **Step 1: Write the failing regression test**

Append:
```typescript
import { readFileSync } from "node:fs";
describe("smart-fetch — keyword block deletion", () => {
  it("does not contain runtime-keyword fingerprints", () => {
    const src = readFileSync("src/browser/smart-fetch.ts", "utf8");
    // Allow `BlockingClassifier` references; forbid runtime substring matches on body text.
    expect(src).not.toMatch(/lText\.includes\("cloudflare"\)/);
    expect(src).not.toMatch(/lText\.includes\("captcha"\)/);
    expect(src).not.toMatch(/lTitle\.includes\("just a moment"\)/);
    expect(src).not.toMatch(/lTitle\.includes\("access denied"\)/);
    expect(src).not.toMatch(/function detectBlocking/);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: regex matches found (`detectBlocking` still defined).

- [ ] **Step 3: Delete lines 94–147 of `src/browser/smart-fetch.ts`**

Remove the `BlockingStatus` interface (lines 96–99) and the entire `detectBlocking` function (lines 101–147). Also remove every call to `detectBlocking(...)` inside `fetchWithBrowser`, `fetchWithBrowserRetry`, and the legacy `webFetch`. Leave the legacy `webFetch` body in place for now (Task 26 deletes it after the umbrella has migrated).

After deletion, replace each call site like `const blocking = detectBlocking(...)` with `const blocking = { blocked: false } as { blocked: boolean; type?: string };` so the legacy path compiles without keyword references. Phase D will drop the legacy `webFetch` entirely.

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-smart-fetch.test.ts`
Expected: all passing.

Run: `grep -nE "cloudflare|captcha|just a moment|access denied" src/browser/smart-fetch.ts`
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/web-smart-fetch.test.ts
git commit -m "refactor(browser): delete hardcoded blocking keyword block from smart-fetch"
```

---

## Phase C — Tools return envelopes

### Task 9: `web.ts` returns envelope

**Files:**
- Modify: `src/tools/web.ts`
- Create: `__tests__/web-tool-envelope.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-tool-envelope.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { parseWebToolResult } from "../src/browser/envelope.js";

vi.mock("../src/browser/smart-fetch.js", async () => {
  const real = await vi.importActual<any>("../src/browser/smart-fetch.js");
  return {
    ...real,
    webFetch: vi.fn(async () => ({ blocked: true, blockType: "all_tiers_failed", title: "", url: "https://x.com", text: "", length: 0, source: "fetch" })),
  };
});
import { WebCrawlTool } from "../src/tools/web.js";

describe("WebCrawlTool returns WebToolResult envelope", () => {
  it("emits a parseable envelope with BLOCKED: alias on failure", async () => {
    const out = await WebCrawlTool.execute({ url: "https://x.com" }, {} as any);
    const env = parseWebToolResult(out);
    expect(env).not.toBeNull();
    expect(env!.success).toBe(false);
    if (env && !env.success) {
      expect(env.error.code === "BLOCKED_BY_ANTI_BOT" || env.error.code === "ALL_TIERS_UNAVAILABLE").toBe(true);
      expect(env.error.message.startsWith("BLOCKED:")).toBe(true);
    }
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `env` is `null` (current tool returns the legacy "BLOCKED:" narrative string).

- [ ] **Step 3: Rewrite `src/tools/web.ts:35–76`**

Replace the entire `execute` function with:
```typescript
  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const { serializeWebToolResult } = await import("../browser/envelope.js");
    let url = args["url"] as string;
    if (!url) {
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: "URL is required", attemptedTiers: [] },
      });
    }
    try {
      const parsedUrl = new URL(url);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        return serializeWebToolResult({
          success: false,
          error: { code: "INVALID_URL", message: "Only http(s) URLs are supported", attemptedTiers: [] },
        });
      }
      url = parsedUrl.toString();
    } catch {
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: `Invalid URL: ${url}`, attemptedTiers: [] },
      });
    }

    try {
      const result = await webFetch(url, { maxLength: 25000, timeout: 30000 });
      if (result.blocked) {
        return serializeWebToolResult({
          success: false,
          error: {
            code: "ALL_TIERS_UNAVAILABLE",
            message: `${url} — bot/CAPTCHA protection (${result.blockType ?? "unknown"})`,
            attemptedTiers: [{ tier: 1, name: "http", durationMs: 0, outcome: "blocked", blockedReason: "other" }],
          },
        });
      }
      return serializeWebToolResult({
        success: true,
        data: { kind: "page", url: result.url, title: result.title, content: result.text },
      });
    } catch (error) {
      return serializeWebToolResult({
        success: false,
        error: {
          code: "INTERNAL_ERROR",
          message: error instanceof Error ? error.message : String(error),
          attemptedTiers: [],
        },
      });
    }
  },
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-tool-envelope.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/web.ts __tests__/web-tool-envelope.test.ts
git commit -m "refactor(tools/web): return WebToolResult envelope with BLOCKED: alias"
```

---

### Task 10: `web-unified.ts` accepts `hint` and routes envelope

**Files:**
- Modify: `src/tools/web-unified.ts`
- Modify: `__tests__/web-tool-envelope.test.ts`

- [ ] **Step 1: Append failing test**

Append:
```typescript
import { createWebUnifiedTool } from "../src/tools/web-unified.js";

describe("createWebUnifiedTool — hint", () => {
  it("threads hint:'anti-bot' to the fetch impl", async () => {
    const captured: any = {};
    const tool = createWebUnifiedTool({
      fetch: async (args) => { captured.hint = args["hint"]; return JSON.stringify({ success: true, data: { kind: "page", url: "x", content: "" } }); },
    });
    await tool.execute({ action: "fetch", url: "https://x.com", hint: "anti-bot" }, {} as any);
    expect(captured.hint).toBe("anti-bot");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `captured.hint` is `undefined` (`hint` not in schema, gets stripped).

- [ ] **Step 3: Add `hint` parameter to schema**

In `src/tools/web-unified.ts`, modify the `parameters.properties` object (lines 33–58). Add this entry after the `js` property:
```typescript
          hint: {
            type: "string",
            enum: ["anti-bot"],
            description:
              "Optional pre-selection: 'anti-bot' starts the escalation chain at Tier 2 (camofox), skipping Tier 1 HTTP. Use only when you have prior evidence the URL is anti-bot protected. Skipped tiers still appear in attemptedTiers[] with outcome:'skipped-by-hint'.",
          },
```

The existing `execute` already passes `args` through to the impl unchanged, so no body change is required.

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-tool-envelope.test.ts`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/tools/web-unified.ts __tests__/web-tool-envelope.test.ts
git commit -m "feat(tools/web-unified): add closed-enum hint parameter"
```

---

### Task 11: `web-scrapling.ts` lazy probe + envelope

**Files:**
- Modify: `src/tools/web-scrapling.ts`
- Create: `__tests__/web-scrapling-envelope.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-scrapling-envelope.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { parseWebToolResult } from "../src/browser/envelope.js";
import { ScraplingTool, probeReadiness } from "../src/tools/web-scrapling.js";

describe("ScraplingTool envelope", () => {
  it("probeReadiness returns ok=false with reason when import fails", async () => {
    // The default probe runs python3 — fake it via injection
    const r = await probeReadiness({ runImportCheck: async () => { throw new Error("ModuleNotFoundError: scrapling"); } });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/ModuleNotFound/);
  });

  it("returns envelope with suggestedEscalation on probe failure", async () => {
    const out = await ScraplingTool.execute({ url: "https://x.com" }, { _scraplingProbe: async () => ({ ok: false, error: "no module" }) } as any);
    const env = parseWebToolResult(out)!;
    expect(env.success).toBe(false);
    if (!env.success) {
      expect(env.error.suggestedEscalation).toContain("pip install");
    }
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `probeReadiness` not exported.

- [ ] **Step 3: Add `probeReadiness()` and rewrite `execute`**

Append to `src/tools/web-scrapling.ts`:
```typescript
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const pexec = promisify(execFile);

export async function probeReadiness(opts?: { runImportCheck?: () => Promise<string> }): Promise<{ ok: boolean; version?: string; error?: string }> {
  try {
    const stdout = opts?.runImportCheck
      ? await opts.runImportCheck()
      : (await pexec("python3", ["-c", "import scrapling; print(scrapling.__version__)"], { timeout: 5000 })).stdout;
    return { ok: true, version: stdout.trim() };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

const SCRAPLING_INSTALL_HINT_TOOL = "pip install 'scrapling[all]' && patchright install chromium";
```

Then replace the `execute` body (currently `web-scrapling.ts:228–337`) with:
```typescript
  async execute(args: Record<string, unknown>, ctx: ToolContext): Promise<string> {
    const { serializeWebToolResult } = await import("../browser/envelope.js");
    const url = args.url as string;
    if (!url) return serializeWebToolResult({ success: false, error: { code: "INVALID_URL", message: "URL is required", attemptedTiers: [] } });

    const probeFn = (ctx as any)._scraplingProbe ?? probeReadiness;
    const probe = await probeFn();
    if (!probe.ok) {
      return serializeWebToolResult({
        success: false,
        error: {
          code: "ALL_TIERS_UNAVAILABLE",
          message: `Scrapling not installed: ${probe.error ?? "unknown"}`,
          attemptedTiers: [{ tier: 3, name: "scrapling", durationMs: 0, outcome: "unavailable" }],
          suggestedEscalation: SCRAPLING_INSTALL_HINT_TOOL,
        },
      });
    }

    const mode = (args.mode as FetcherMode) || "basic";
    const selector = args.selector as string | undefined;
    const waitFor = args.wait_for as string | undefined;
    const headless = args.headless as boolean | undefined;
    try {
      const script = buildFetchScript(url, mode, { selector, waitFor, headless });
      const output = await runPython(script);
      const result = JSON.parse(output.trim()) as { title: string; url: string; length: number; content: string };
      return serializeWebToolResult({ success: true, data: { kind: "page", url: result.url, title: result.title, content: result.content } });
    } catch (err) {
      return serializeWebToolResult({
        success: false,
        error: { code: "INTERNAL_ERROR", message: err instanceof Error ? err.message : String(err), attemptedTiers: [{ tier: 3, name: "scrapling", durationMs: 0, outcome: "error" }] },
      });
    }
  },
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-scrapling-envelope.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/web-scrapling.ts __tests__/web-scrapling-envelope.test.ts
git commit -m "refactor(tools/web-scrapling): probeReadiness + envelope returns"
```

---

### Task 12: `camofox.ts` consumes RuntimeAvailability

**Files:**
- Modify: `src/tools/camofox.ts`
- Create: `__tests__/web-camofox-envelope.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-camofox-envelope.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { parseWebToolResult } from "../src/browser/envelope.js";
import { CamoFoxTool } from "../src/tools/camofox.js";

describe("CamoFoxTool envelope", () => {
  it("returns ALL_TIERS_UNAVAILABLE envelope when availability ready=false", async () => {
    const out = await CamoFoxTool.execute(
      { action: "navigate", url: "https://x.com" },
      { _availability: { isReady: async () => false } } as any,
    );
    const env = parseWebToolResult(out);
    expect(env).not.toBeNull();
    if (env && !env.success) expect(env.error.code).toBe("ALL_TIERS_UNAVAILABLE");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: env is null (current tool returns plain string narratives).

- [ ] **Step 3: Add availability gate at the top of `CamoFoxTool.execute`**

In `src/tools/camofox.ts`, find the `execute(args, context)` function (the body that dispatches on `action`). At the very top of the body, before any `switch (action)`/`if (action === ...)`, insert:
```typescript
    const { serializeWebToolResult } = await import("../browser/envelope.js");
    const availability = (context as any)?._availability;
    if (availability && !(await availability.isReady("camofox"))) {
      return serializeWebToolResult({
        success: false,
        error: {
          code: "ALL_TIERS_UNAVAILABLE",
          message: "CamoFox is not installed/ready. Run `stackowl backends install camofox`.",
          attemptedTiers: [{ tier: 2, name: "camofox", durationMs: 0, outcome: "unavailable" }],
          suggestedEscalation: "stackowl backends install camofox",
        },
      });
    }
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-camofox-envelope.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/camofox.ts __tests__/web-camofox-envelope.test.ts
git commit -m "feat(tools/camofox): availability gate + envelope on unavailable"
```

---

## Phase D — Tracker, registry, GoalVerifier coupling

### Task 13: Tracker schema migration (additive `attempt_metadata`)

**Files:**
- Modify: `src/memory/db.ts`
- Create: `__tests__/web-tracker-migration.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-tracker-migration.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import { applyV26WebAttemptMetadataMigration } from "../src/memory/db.js";

describe("V26 web attempt_metadata migration", () => {
  it("adds attempt_metadata column to tool_executions", () => {
    const db = new Database(":memory:");
    db.exec(`CREATE TABLE tool_executions (id INTEGER PRIMARY KEY, tool_name TEXT, success INTEGER, duration_ms INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);`);
    applyV26WebAttemptMetadataMigration(db);
    const cols = db.prepare(`PRAGMA table_info(tool_executions)`).all() as Array<{ name: string }>;
    expect(cols.some(c => c.name === "attempt_metadata")).toBe(true);
  });

  it("is idempotent", () => {
    const db = new Database(":memory:");
    db.exec(`CREATE TABLE tool_executions (id INTEGER PRIMARY KEY, tool_name TEXT, success INTEGER, duration_ms INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);`);
    applyV26WebAttemptMetadataMigration(db);
    expect(() => applyV26WebAttemptMetadataMigration(db)).not.toThrow();
  });

  it("round-trips a JSON attempt_metadata payload", () => {
    const db = new Database(":memory:");
    db.exec(`CREATE TABLE tool_executions (id INTEGER PRIMARY KEY, tool_name TEXT, success INTEGER, duration_ms INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);`);
    applyV26WebAttemptMetadataMigration(db);
    const meta = JSON.stringify([{ tier: 1, name: "http", outcome: "blocked", durationMs: 100 }]);
    db.prepare(`INSERT INTO tool_executions (tool_name, success, duration_ms, attempt_metadata) VALUES (?,?,?,?)`).run("web", 0, 100, meta);
    const row = db.prepare(`SELECT attempt_metadata FROM tool_executions WHERE tool_name='web'`).get() as { attempt_metadata: string };
    expect(JSON.parse(row.attempt_metadata)[0].name).toBe("http");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `applyV26WebAttemptMetadataMigration is not a function`.

- [ ] **Step 3: Add migration to `src/memory/db.ts`**

At the bottom of `src/memory/db.ts`, append:
```typescript
import type DatabaseT from "better-sqlite3";

export function applyV26WebAttemptMetadataMigration(db: DatabaseT.Database): void {
  const cols = db.prepare(`PRAGMA table_info(tool_executions)`).all() as Array<{ name: string }>;
  if (!cols.some(c => c.name === "attempt_metadata")) {
    db.exec(`ALTER TABLE tool_executions ADD COLUMN attempt_metadata TEXT;`);
  }
}
```

Register the migration in the same place v25 is registered (search for `applyV25Migration` and add `applyV26WebAttemptMetadataMigration` immediately after it in the migration runner).

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-tracker-migration.test.ts`
Expected: 3/3 passing.

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/web-tracker-migration.test.ts
git commit -m "feat(memory): v26 migration — attempt_metadata column on tool_executions"
```

---

### Task 14: Tracker subscribes to bus + records attempt_metadata

**Files:**
- Modify: `src/tools/tracker.ts`
- Create: `__tests__/web-tracker-attempt-metadata.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-tracker-attempt-metadata.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import { applyV26WebAttemptMetadataMigration } from "../src/memory/db.js";
import { ToolTracker } from "../src/tools/tracker.js";

function makeDb() {
  const db = new Database(":memory:");
  db.exec(`CREATE TABLE tool_executions (id INTEGER PRIMARY KEY, tool_name TEXT, success INTEGER, duration_ms INTEGER, error_code TEXT, error_message TEXT, session_id TEXT, subgoal_id TEXT, attempt_metadata TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);`);
  applyV26WebAttemptMetadataMigration(db);
  return db;
}

describe("ToolTracker.recordSuccess attemptMetadata", () => {
  it("persists attempt list as JSON", () => {
    const db = makeDb();
    const fakeMemoryDb = {
      recordToolExecution: (rec: any) => {
        db.prepare(`INSERT INTO tool_executions (tool_name, success, duration_ms, error_code, error_message, session_id, subgoal_id, attempt_metadata) VALUES (?,?,?,?,?,?,?,?)`)
          .run(rec.toolName, rec.success ? 1 : 0, rec.durationMs, rec.errorCode ?? null, rec.errorMessage ?? null, rec.sessionId ?? null, rec.subgoalId ?? null, rec.attemptMetadata ?? null);
      },
      getToolStats: () => null,
      rawDb: db,
    } as any;
    const t = new ToolTracker(fakeMemoryDb);
    t.recordSuccess("web", 1234, { sessionId: "s1", attemptMetadata: [{ tier: 1, name: "http", outcome: "blocked", durationMs: 50 }, { tier: 2, name: "camofox", outcome: "success", durationMs: 1180 }] });
    const row = db.prepare(`SELECT attempt_metadata FROM tool_executions WHERE tool_name='web'`).get() as { attempt_metadata: string };
    const parsed = JSON.parse(row.attempt_metadata);
    expect(parsed).toHaveLength(2);
    expect(parsed[1].outcome).toBe("success");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `Cannot find name 'attemptMetadata'` or persisted column is null because `recordSuccess` doesn't accept it.

- [ ] **Step 3: Extend `recordSuccess` and `recordFailure` signatures**

In `src/tools/tracker.ts`, change `recordSuccess` to:
```typescript
  recordSuccess(
    toolName: string,
    durationMs: number,
    ctx: { subgoalId?: string; sessionId?: string; attemptMetadata?: unknown[] } = {},
  ): void {
    this.db.recordToolExecution({
      toolName,
      success: true,
      durationMs,
      subgoalId: ctx.subgoalId,
      sessionId: ctx.sessionId,
      attemptMetadata: ctx.attemptMetadata ? JSON.stringify(ctx.attemptMetadata) : undefined,
    });
  }
```

Mirror in `recordFailure`. Also extend `MemoryDatabase.recordToolExecution` to forward the optional `attemptMetadata` string into the insert (find the existing INSERT and add the column).

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-tracker-attempt-metadata.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/tracker.ts src/memory/db.ts __tests__/web-tracker-attempt-metadata.test.ts
git commit -m "feat(tracker): persist optional attempt_metadata JSON column"
```

---

### Task 15: `registry.ts:411–413` envelope-aware rewrite

**Files:**
- Modify: `src/tools/registry.ts`
- Create: `__tests__/registry-web-envelope.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/registry-web-envelope.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { ToolRegistry } from "../src/tools/registry.js";
import { serializeWebToolResult } from "../src/browser/envelope.js";

describe("registry execute — envelope-aware wrapping", () => {
  it("emits <tool_attempt_summary> when result parses as WebToolResult error", async () => {
    const reg = new ToolRegistry();
    reg.registerAll([{
      definition: { name: "fakeweb", description: "x", parameters: { type: "object", properties: {} } },
      execute: async () => serializeWebToolResult({
        success: false,
        error: {
          code: "BLOCKED_BY_ANTI_BOT",
          message: "blocked",
          attemptedTiers: [{ tier: 1, name: "http", durationMs: 50, outcome: "blocked", blockedReason: "cloudflare", httpStatus: 403 }],
        },
      }),
    } as any]);
    const out = await reg.execute("fakeweb", {}, { engineContext: { sessionId: "s", activeSubGoal: { description: "test", goalId: "g" }, userMessage: "hi" } } as any);
    expect(out).toContain("<tool_attempt_summary");
    expect(out).toContain('code="BLOCKED_BY_ANTI_BOT"');
    expect(out).not.toContain("<tool_result_warning");
  });

  it("falls back to legacy <tool_result_warning> for non-envelope results", async () => {
    const reg = new ToolRegistry();
    reg.registerAll([{
      definition: { name: "ordinary", description: "x", parameters: { type: "object", properties: {} } },
      execute: async () => "plain string output",
    } as any]);
    // No GoalVerifier wired, so neither tag should appear; just check no envelope tag emitted
    const out = await reg.execute("ordinary", {}, { engineContext: { sessionId: "s" } } as any);
    expect(out).not.toContain("<tool_attempt_summary");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: output does not contain `<tool_attempt_summary>`.

- [ ] **Step 3: Rewrite registry.ts:410–413**

In `src/tools/registry.ts`, locate the block at lines 410–413. Replace the entire `if (verification.verdict === "BLOCKED" || verification.verdict === "PARTIAL")` line and its body with:
```typescript
          // Envelope-aware: web tools return JSON-stringified WebToolResult.
          // For envelope errors, emit <tool_attempt_summary>; for non-envelope tools, keep legacy <tool_result_warning>.
          const { parseWebToolResult, buildAttemptSummaryXml } = await import("../browser/envelope.js");
          const envelope = parseWebToolResult(result);
          if (envelope && !envelope.success) {
            result = result + "\n\n" + buildAttemptSummaryXml(envelope);
          } else if (verification.verdict === "BLOCKED" || verification.verdict === "PARTIAL") {
            result = result + `\n\n<tool_result_warning verdict="${verification.verdict}">${verification.reason}${verification.suggestion ? ` Suggestion: ${verification.suggestion}` : ""}</tool_result_warning>`;
          }
```

Also add an unconditional envelope check **before** the GoalVerifier branch (so envelopes take effect even when no sub-goal is active). Just before line 347 (the `if (this._goalVerifier && context.engineContext?.activeSubGoal)` line), insert:
```typescript
      // Envelope passthrough — emit <tool_attempt_summary> regardless of GAV
      try {
        const { parseWebToolResult, buildAttemptSummaryXml } = await import("../browser/envelope.js");
        const env = parseWebToolResult(result);
        if (env && !env.success && !result.includes("<tool_attempt_summary")) {
          result = result + "\n\n" + buildAttemptSummaryXml(env);
        }
      } catch { /* envelope parse is best-effort */ }
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/registry-web-envelope.test.ts`
Expected: 2/2 passing.

- [ ] **Step 5: Commit**

```bash
git add src/tools/registry.ts __tests__/registry-web-envelope.test.ts
git commit -m "feat(registry): emit <tool_attempt_summary> for WebToolResult errors"
```

---

### Task 16: `goal-verifier.ts` re-targets off `error.code`

**Files:**
- Modify: `src/tools/goal-verifier.ts`
- Create: `__tests__/web-goal-verifier.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-goal-verifier.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { GoalVerifier } from "../src/tools/goal-verifier.js";
import { serializeWebToolResult } from "../src/browser/envelope.js";

function routerReturning(content: string) {
  return {
    resolve: () => ({ chat: vi.fn(async () => ({ content })) }),
  } as any;
}

describe("GoalVerifier — envelope-driven verdicts", () => {
  it("BLOCKED_BY_ANTI_BOT envelope → BLOCKED verdict", async () => {
    const v = new GoalVerifier(routerReturning(`{"verdict":"BLOCKED","reason":"anti-bot","suggestion":"try later"}`));
    const result = serializeWebToolResult({
      success: false,
      error: { code: "BLOCKED_BY_ANTI_BOT", message: "blocked", attemptedTiers: [] },
    });
    const v2 = await v.verify({ toolName: "web", toolArgs: {}, toolResult: result, subGoal: { description: "find x" } as any, userMessage: "find x" });
    expect(v2.verdict).toBe("BLOCKED");
  });

  it("TIMEOUT envelope → PARTIAL verdict", async () => {
    const v = new GoalVerifier(routerReturning(`{"verdict":"PARTIAL","reason":"timed out partial result"}`));
    const result = serializeWebToolResult({
      success: false,
      error: { code: "TIMEOUT", message: "timed out", attemptedTiers: [] },
    });
    const v2 = await v.verify({ toolName: "web", toolArgs: {}, toolResult: result, subGoal: { description: "fetch x" } as any, userMessage: "fetch x" });
    expect(v2.verdict).toBe("PARTIAL");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: tests fail because the verifier currently sees the raw JSON string, and the SYSTEM_PROMPT doesn't reference `error.code`.

- [ ] **Step 3: Update SYSTEM_PROMPT and rebuild userContent**

In `src/tools/goal-verifier.ts`, replace the `SYSTEM_PROMPT` constant (lines 41–49) with:
```typescript
const SYSTEM_PROMPT = `You are a tool execution verifier. Given a tool's result and the active sub-goal, classify whether the result advances the goal.

Respond with JSON only:
{"verdict": "ADVANCES"|"PARTIAL"|"BLOCKED"|"NEUTRAL", "reason": "one sentence", "suggestion": "optional, only for BLOCKED"}

- ADVANCES: result clearly provides information that moves toward the sub-goal
- PARTIAL: result provides some relevant information but is incomplete
- BLOCKED: tool failed, hit a paywall, returned irrelevant content, or actively cannot help
- NEUTRAL: tool succeeded but result is unrelated to the sub-goal

If the tool reports success:false with error.code: BLOCKED_BY_ANTI_BOT or ALL_TIERS_UNAVAILABLE, classify as BLOCKED.
If error.code: TIMEOUT, classify as PARTIAL.
If success:true, classify based on whether data answers the goal.`;
```

Then in `verify()`, replace the `userContent` block (lines 94–98) with:
```typescript
    let userContent: string;
    try {
      const env = JSON.parse(toolResult);
      if (env && typeof env === "object" && "success" in env) {
        if (env.success === false && env.error) {
          const tierSummary = Array.isArray(env.error.attemptedTiers)
            ? env.error.attemptedTiers.map((t: any) => `${t.name}:${t.outcome}`).join(", ")
            : "(none)";
          userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool error.code: ${env.error.code}
Tool error.message: ${env.error.message}
Tiers attempted: [${tierSummary}]`;
        } else if (env.success === true && env.data) {
          userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool result data (first 500 chars): ${JSON.stringify(env.data).slice(0, 500)}`;
        } else {
          throw new Error("not envelope");
        }
      } else { throw new Error("not envelope"); }
    } catch {
      userContent = `Sub-goal: ${subGoal.description}
User request: ${userMessage}
Tool used: ${toolName}
Tool args: ${JSON.stringify(toolArgs)}
Tool result (first 500 chars): ${toolResult.slice(0, 500)}`;
    }
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-goal-verifier.test.ts`
Expected: 2/2 passing.

- [ ] **Step 5: Commit**

```bash
git add src/tools/goal-verifier.ts __tests__/web-goal-verifier.test.ts
git commit -m "feat(goal-verifier): re-key verdicts off envelope error.code"
```

---

## Phase E — Bus events + narration

### Task 17: Bus events fire from smart-fetch

**Files:**
- Modify: `src/browser/smart-fetch.ts` (already imports bus)
- Create: `__tests__/web-bus-events.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-bus-events.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../src/browser/smart-fetch.js";

describe("smart-fetch bus events", () => {
  it("emits web:tier_attempted before each tier and web:tier_blocked on blocks", async () => {
    const events: any[] = [];
    const bus = { emit: (e: any) => events.push(e) } as any;
    const r1: TierRunner = { tier:1, name:"http", isAvailable:()=>true,
      run: async () => ({ attempt: { tier:1, name:"http", durationMs:5, outcome:"blocked", blockedReason:"cloudflare", httpStatus:403 } }) };
    const r2: TierRunner = { tier:2, name:"camofox", isAvailable:()=>true,
      run: async () => ({ attempt: { tier:2, name:"camofox", durationMs:50, outcome:"success" }, data: { kind:"page", url:"https://x.com", content:"y" } }) };
    await runEscalationChain([r1, r2], "https://x.com", { bus });
    const types = events.map(e => e.type);
    expect(types).toContain("web:tier_attempted");
    expect(types).toContain("web:tier_blocked");
  });
});
```

- [ ] **Step 2: Run — confirm PASS** (bus emit was already wired in Task 4 — this is a guard test).

If FAIL, ensure `runEscalationChain` emits `web:tier_attempted` immediately after `isAvailable()` succeeds. Already implemented; this task is verification.

- [ ] **Step 3: Add `web:escalating` between tier transitions**

In `runEscalationChain`, after pushing a `blocked` attempt and *before* the next iteration, emit:
```typescript
ctx.bus.emit({ type: "web:escalating", fromTier: r.tier, toTier: r.tier + 1, reason: res.attempt.blockedReason ?? "other" } as any);
```

- [ ] **Step 4: Re-run test — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/web-bus-events.test.ts
git commit -m "feat(browser): emit web:escalating between tier transitions"
```

---

### Task 18: Narration templates (channel-parity)

**Files:**
- Modify: `src/gateway/narration-formatter.ts`
- Create: `__tests__/web-narration.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-narration.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { formatWebAttempts } from "../src/gateway/narration-formatter.js";

const attempts = [
  { tier: 1, name: "http", outcome: "blocked", blockedReason: "cloudflare", httpStatus: 403, durationMs: 50 },
  { tier: 2, name: "camofox", outcome: "blocked", blockedReason: "cloudflare", durationMs: 1100 },
  { tier: 3, name: "scrapling", outcome: "success", durationMs: 2200 },
];

describe("formatWebAttempts — channel parity", () => {
  it("CLI: streams inline arrows", () => {
    const s = formatWebAttempts(attempts as any, "cli");
    expect(s).toContain("→ http (403, cloudflare)");
    expect(s).toContain("→ scrapling (success)");
  });

  it("Telegram: single sentence aggregate", () => {
    const s = formatWebAttempts(attempts as any, "telegram");
    expect(s.match(/\n/g)?.length ?? 0).toBeLessThan(2);
    expect(s).toContain("3 ways");
  });

  it("Web UI: collapsible per-tier breakdown", () => {
    const s = formatWebAttempts(attempts as any, "web");
    expect(s).toContain("<details");
    expect(s).toContain("scrapling");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `formatWebAttempts is not a function`.

- [ ] **Step 3: Add to `src/gateway/narration-formatter.ts`**

Append:
```typescript
import type { TierAttempt } from "../browser/envelope.js";

export type NarrationChannel = "cli" | "telegram" | "slack" | "web";

export function formatWebAttempts(attempts: TierAttempt[], channel: NarrationChannel): string {
  if (channel === "cli") {
    return attempts.map(a => {
      const ext = a.httpStatus ? `${a.httpStatus}, ${a.blockedReason ?? a.outcome}` : (a.blockedReason ?? a.outcome);
      return `  → ${a.name} (${ext})`;
    }).join("\n");
  }
  if (channel === "telegram" || channel === "slack") {
    return `Tried ${attempts.length} ways: ${attempts.map(a => `${a.name} ${a.outcome}`).join(", ")}.`;
  }
  // web
  const rows = attempts.map(a => `<li>${a.tier}. ${a.name} — ${a.outcome}${a.blockedReason ? " (" + a.blockedReason + ")" : ""}</li>`).join("");
  return `<details><summary>Tier attempts (${attempts.length})</summary><ol>${rows}</ol></details>`;
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-narration.test.ts`
Expected: 3/3 passing.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/narration-formatter.ts __tests__/web-narration.test.ts
git commit -m "feat(narration): channel-parity formatter for tier attempt lists"
```

---

## Phase F — Anti-Bot Override + boot probe

### Task 19: Rewrite `runtime.ts:2367` directive

**Files:**
- Modify: `src/engine/runtime.ts`
- Create: `__tests__/web-runtime-prompt.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-runtime-prompt.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

describe("runtime.ts Anti-Bot Override directive", () => {
  it("does not enumerate deprecated tools by name", () => {
    const src = readFileSync("src/engine/runtime.ts", "utf8");
    const idx = src.indexOf("Anti-Bot Override");
    expect(idx).toBeGreaterThan(0);
    const slice = src.slice(idx, idx + 800);
    expect(slice).not.toMatch(/scrapling_fetch/);
    expect(slice).not.toMatch(/`camofox`/);
  });

  it("references <tool_attempt_summary>", () => {
    const src = readFileSync("src/engine/runtime.ts", "utf8");
    const idx = src.indexOf("Anti-Bot Override");
    const slice = src.slice(idx, idx + 800);
    expect(slice).toContain("tool_attempt_summary");
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: matches `scrapling_fetch` and `` `camofox` ``.

- [ ] **Step 3: Rewrite the line at runtime.ts:2367**

Replace the entire `Anti-Bot Override` line in `src/engine/runtime.ts` (within the `prompt += ...` builder around line 2367) with:
```typescript
      prompt += `- **Anti-Bot Override:** Web fetches return a structured envelope. If you see a \`<tool_attempt_summary>\` showing a tier as \`unavailable\`, surface its install command in \`suggestedEscalation\` to the user — never claim a tier was tried when it was not. If all tiers were tried and blocked, tell the user honestly which tiers failed and why; offer to try \`live_browser\` if the site might require login or visual interaction.\n`;
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-runtime-prompt.test.ts`
Expected: 2/2 passing.

- [ ] **Step 5: Commit**

```bash
git add src/engine/runtime.ts __tests__/web-runtime-prompt.test.ts
git commit -m "refactor(runtime): generic Anti-Bot Override directive driven by envelope"
```

---

### Task 20: `index.ts:266–273` `initCamoFox()` becomes probe-only

**Files:**
- Modify: `src/index.ts`
- Create: `__tests__/web-boot-probe.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/web-boot-probe.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { probeCamoFoxAtBoot } from "../src/index.js";

describe("probeCamoFoxAtBoot", () => {
  it("writes ready=false to availability when server unreachable", async () => {
    const updates: any[] = [];
    const ra = { update: vi.fn(async (k: any, v: any) => { updates.push({ k, v }); }) } as any;
    await probeCamoFoxAtBoot(ra, { baseUrl: "http://127.0.0.1:1" });
    expect(updates[0].k).toBe("camofox");
    expect(updates[0].v.ready).toBe(false);
  });

  it("never throws when server is unreachable", async () => {
    const ra = { update: async () => {} } as any;
    await expect(probeCamoFoxAtBoot(ra, { baseUrl: "http://127.0.0.1:1" })).resolves.not.toThrow();
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `probeCamoFoxAtBoot is not a function`.

- [ ] **Step 3: Rewrite `index.ts:266–273`**

Replace lines 266–273 of `src/index.ts` with:
```typescript
  // Probe CamoFox availability (no auto-install; runtime simply records readiness)
  const { RuntimeAvailability } = await import("./runtime/availability.js");
  const runtimeAvailability = new RuntimeAvailability();
  await probeCamoFoxAtBoot(runtimeAvailability, {
    baseUrl: config.camofox?.baseUrl ?? "http://localhost:9377",
  });
```

And export `probeCamoFoxAtBoot` from `src/index.ts` (top-level, near the other helpers):
```typescript
export async function probeCamoFoxAtBoot(
  availability: { update: (backend: "camofox", status: any) => Promise<void> },
  cfg: { baseUrl: string },
): Promise<void> {
  let ready = false;
  let lastError: string | undefined;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(`${cfg.baseUrl}/tabs`, { signal: ctrl.signal }).finally(() => clearTimeout(t));
    ready = r.ok;
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err);
  }
  await availability.update("camofox", {
    installed: ready,
    ready,
    lastProbe: new Date().toISOString(),
    lastError,
  });
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/web-boot-probe.test.ts`
Expected: 2/2 passing.

- [ ] **Step 5: Commit**

```bash
git add src/index.ts __tests__/web-boot-probe.test.ts
git commit -m "feat(boot): probe-only CamoFox initialization writing availability map"
```

---

## Phase G — Onboarding + CLI subcommand

### Task 21: Onboarding wizard "Stealth web backends" subsection

**Files:**
- Modify: `src/cli/onboarding.ts`
- Create: `__tests__/cli-onboarding-backends.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/cli-onboarding-backends.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { renderStealthBackendsSubsection, installSelectedBackend } from "../src/cli/onboarding.js";

describe("Onboarding — Stealth web backends", () => {
  it("renderStealthBackendsSubsection lists camofox/scrapling/live-browser", () => {
    const s = renderStealthBackendsSubsection();
    expect(s).toContain("camofox");
    expect(s).toContain("scrapling");
    expect(s).toContain("live-browser");
  });

  it("installSelectedBackend dispatches per backend via injected installer", async () => {
    const calls: string[] = [];
    const installer = { camofox: async () => { calls.push("c"); return true; }, scrapling: async () => { calls.push("s"); return true; } } as any;
    await installSelectedBackend("camofox", installer);
    await installSelectedBackend("scrapling", installer);
    expect(calls).toEqual(["c", "s"]);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: functions not exported.

- [ ] **Step 3: Add to `src/cli/onboarding.ts`**

Append:
```typescript
export function renderStealthBackendsSubsection(): string {
  return [
    "Stealth web backends",
    "  [ ] camofox       — anti-detection Firefox (Tier 2)",
    "  [ ] scrapling     — Python anti-bot scraper (Tier 3)",
    "  [ ] live-browser  — desktop browser control (interactive)",
  ].join("\n");
}

export interface BackendInstaller {
  camofox?: () => Promise<boolean>;
  scrapling?: () => Promise<boolean>;
  "live-browser"?: () => Promise<boolean>;
}

export async function installSelectedBackend(
  backend: "camofox" | "scrapling" | "live-browser",
  installer: BackendInstaller,
): Promise<boolean> {
  const fn = installer[backend];
  if (!fn) return false;
  return await fn();
}
```

In the existing `runOnboarding` function (Section D — Features), insert a call to `renderStealthBackendsSubsection()` and the multi-select prompt that drives `installSelectedBackend`. (The CLI prompt library is already used in this file — match the existing prompt patterns.)

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/cli-onboarding-backends.test.ts`
Expected: 2/2 passing.

- [ ] **Step 5: Commit**

```bash
git add src/cli/onboarding.ts __tests__/cli-onboarding-backends.test.ts
git commit -m "feat(cli/onboarding): stealth-web-backends subsection"
```

---

### Task 22: `stackowl backends` CLI subcommand

**Files:**
- Modify: `src/cli/commands.ts`
- Create: `__tests__/cli-backends-cmd.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/cli-backends-cmd.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { backendsCommand } from "../src/cli/commands.js";

describe("stackowl backends subcommand", () => {
  it("list prints availability map", async () => {
    const dir = mkdtempSync(join(tmpdir(), "owl-cli-"));
    const path = join(dir, "runtime-availability.json");
    const out = await backendsCommand(["list"], { availabilityPath: path });
    expect(out).toContain("camofox");
    expect(out).toContain("ready");
    rmSync(dir, { recursive: true, force: true });
  });

  it("repair re-probes and writes the map", async () => {
    const dir = mkdtempSync(join(tmpdir(), "owl-cli-"));
    const path = join(dir, "runtime-availability.json");
    const out = await backendsCommand(["repair"], {
      availabilityPath: path,
      probes: { camofox: async () => ({ installed: true, ready: true }), scrapling: async () => ({ installed: false, ready: false }), "live-browser": async () => ({ installed: true, ready: true }) },
    });
    expect(out).toContain("repair");
    rmSync(dir, { recursive: true, force: true });
  });

  it("stats prints per-tier success rates", async () => {
    const out = await backendsCommand(["stats"], { trackerStats: { http: { success: 10, total: 20 }, camofox: { success: 7, total: 9 }, scrapling: { success: 1, total: 4 } } });
    expect(out).toContain("Tier 1 (http)");
    expect(out).toContain("Tier 2 (camofox)");
    expect(out).toContain("Tier 3 (scrapling)");
  });

  it("install dispatches per backend", async () => {
    const calls: string[] = [];
    const out = await backendsCommand(["install", "camofox"], {
      installer: { camofox: async () => { calls.push("c"); return true; } },
      availabilityPath: "/tmp/t.json",
    });
    expect(calls).toEqual(["c"]);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `backendsCommand is not a function`.

- [ ] **Step 3: Implement `backendsCommand` in `src/cli/commands.ts`**

Append to `src/cli/commands.ts`:
```typescript
export interface BackendsCommandDeps {
  availabilityPath?: string;
  probes?: import("../runtime/availability.js").ProbeMap;
  installer?: { camofox?: () => Promise<boolean>; scrapling?: () => Promise<boolean>; "live-browser"?: () => Promise<boolean> };
  trackerStats?: Record<string, { success: number; total: number }>;
}

export async function backendsCommand(argv: string[], deps: BackendsCommandDeps = {}): Promise<string> {
  const { RuntimeAvailability } = await import("../runtime/availability.js");
  const ra = new RuntimeAvailability(deps.availabilityPath, deps.probes);
  const sub = argv[0] ?? "list";
  if (sub === "list") {
    const map = await ra.load();
    return Object.entries(map).map(([k, v]) =>
      `${k.padEnd(14)} installed=${v.installed} ready=${v.ready} ${v.version ? "v" + v.version : ""}`).join("\n");
  }
  if (sub === "repair") {
    const map = await ra.probeAll();
    return `repair complete:\n${Object.entries(map).map(([k, v]) => `  ${k}: ready=${v.ready}`).join("\n")}`;
  }
  if (sub === "stats") {
    const s = deps.trackerStats ?? {};
    const fmt = (name: string, label: string) => {
      const r = s[name]; if (!r) return `${label}: no data`;
      return `${label}: success rate ${Math.round((r.success / r.total) * 100)}%  (n=${r.success}/${r.total})`;
    };
    return ["=== Web fetch stats (last 7 days) ===",
      fmt("http", "Tier 1 (http)       "),
      fmt("camofox", "Tier 2 (camofox)    "),
      fmt("scrapling", "Tier 3 (scrapling)  ")].join("\n");
  }
  if (sub === "install") {
    const which = argv[1];
    if (!which || !(which in (deps.installer ?? {}))) return `usage: stackowl backends install <camofox|scrapling|live-browser>`;
    const ok = await deps.installer![which as keyof typeof deps.installer]!();
    return `install ${which}: ${ok ? "ok" : "failed"}`;
  }
  return `usage: stackowl backends list|install|repair|stats`;
}
```

Wire `backendsCommand` into the existing CLI command dispatcher (the `commander` setup) so `stackowl backends ...` invokes it.

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/cli-backends-cmd.test.ts`
Expected: 4/4 passing.

- [ ] **Step 5: Commit**

```bash
git add src/cli/commands.ts __tests__/cli-backends-cmd.test.ts
git commit -m "feat(cli): stackowl backends list|install|repair|stats subcommand"
```

---

### Task 23: Delete `start.sh:270–333`

**Files:**
- Modify: `start.sh`
- Create: `__tests__/start-sh-no-camofox.test.ts`

- [ ] **Step 1: Write failing regression test**

Create `__tests__/start-sh-no-camofox.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

describe("start.sh — no camofox install logic", () => {
  it("contains no references to camofox or scrapling install", () => {
    const src = readFileSync("start.sh", "utf8");
    expect(src).not.toMatch(/setup_camofox/);
    expect(src).not.toMatch(/start_camofox_npx/);
    expect(src).not.toMatch(/npm install -g camofox-browser/);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: matches found.

- [ ] **Step 3: Delete the camofox block from `start.sh`**

Delete lines 270–333 of `start.sh`:
- The `setup_camofox()` function definition
- The `start_camofox_npx()` function definition

Also delete any call site that invokes `setup_camofox` (search the file). Replace each call site with: `# CamoFox install moved to: stackowl backends install camofox`.

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/start-sh-no-camofox.test.ts`
Expected: 1/1 passing.

Run: `bash -n start.sh`
Expected: no parse errors.

- [ ] **Step 5: Commit**

```bash
git add start.sh __tests__/start-sh-no-camofox.test.ts
git commit -m "chore(start): delete camofox install block (moved to stackowl backends)"
```

---

### Task 24: `package.json` `optionalDependencies`

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Update `package.json`**

In the `optionalDependencies` block (lines 73–76), add `"camofox-browser": "^0.1.0"` (use the version `start.sh` was previously installing — check `npm view camofox-browser version` if unsure; pin to the latest published release):
```json
  "optionalDependencies": {
    "camofox-browser": "^0.1.0",
    "naudiodon": "^2.3.6",
    "nodejs-whisper": "^0.3.0"
  },
```

- [ ] **Step 2: Run install**

Run: `npm install`
Expected: completes (camofox-browser may be skipped on platforms where it doesn't have prebuilt binaries — that is acceptable for an optional dep).

- [ ] **Step 3: Verify lockfile updated**

Run: `git diff package.json package-lock.json | head -40`
Expected: shows the new optional dep entry.

- [ ] **Step 4: No new test required**

The Task 23 test already verifies start.sh no longer installs it. The Task 20 test verifies probe-only behaviour.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json
git commit -m "chore(deps): camofox-browser to optionalDependencies"
```

---

## Phase H — End-to-end integration

### Task 25: 3-tier integration test (envelope + tracker + narration parity)

**Files:**
- Create: `__tests__/web-honesty-integration.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/web-honesty-integration.test.ts`:
```typescript
import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../src/browser/smart-fetch.js";
import { parseWebToolResult, serializeWebToolResult } from "../src/browser/envelope.js";
import { formatWebAttempts } from "../src/gateway/narration-formatter.js";

describe("Element 16 — end-to-end honesty", () => {
  const tier1: TierRunner = { tier:1, name:"http", isAvailable:()=>true,
    run: async () => ({ attempt: { tier:1, name:"http", durationMs:60, outcome:"blocked", blockedReason:"cloudflare", httpStatus:403 } }) };
  const tier2: TierRunner = { tier:2, name:"camofox", isAvailable:()=>true,
    run: async () => ({ attempt: { tier:2, name:"camofox", durationMs:1100, outcome:"blocked", blockedReason:"cloudflare" } }) };
  const tier3: TierRunner = { tier:3, name:"scrapling", isAvailable:()=>true,
    run: async () => ({ attempt: { tier:3, name:"scrapling", durationMs:2200, outcome:"success" }, data: { kind:"page", url:"https://x.com", content:"REAL CONTENT" } }) };
  const bus = { emit: vi.fn() } as any;

  it("dispatcher returns success, attempt list has 3 tiers, third is success", async () => {
    const r = await runEscalationChain([tier1, tier2, tier3], "https://x.com", { bus });
    expect(r.success).toBe(true);
  });

  it("blocked-only chain produces envelope with all 3 attempts", async () => {
    const tier3blocked: TierRunner = { tier:3, name:"scrapling", isAvailable:()=>true,
      run: async () => ({ attempt: { tier:3, name:"scrapling", durationMs:1500, outcome:"blocked", blockedReason:"captcha" } }) };
    const r = await runEscalationChain([tier1, tier2, tier3blocked], "https://x.com", { bus });
    const env = parseWebToolResult(serializeWebToolResult(r))!;
    expect(env.success).toBe(false);
    if (!env.success) {
      expect(env.error.attemptedTiers).toHaveLength(3);
      expect(env.error.code).toBe("BLOCKED_BY_ANTI_BOT");
      expect(env.error.message.startsWith("BLOCKED:")).toBe(true);
    }
  });

  it("ALL_TIERS_UNAVAILABLE when no backend ready", async () => {
    const downs: TierRunner[] = [
      { tier:1, name:"http", isAvailable:()=>false, run: async () => ({ attempt:{ tier:1, name:"http", durationMs:0, outcome:"unavailable" } }) },
      { tier:2, name:"camofox", isAvailable:()=>false, run: async () => ({ attempt:{ tier:2, name:"camofox", durationMs:0, outcome:"unavailable" } }) },
      { tier:3, name:"scrapling", isAvailable:()=>false, run: async () => ({ attempt:{ tier:3, name:"scrapling", durationMs:0, outcome:"unavailable" } }) },
    ];
    const r = await runEscalationChain(downs, "https://x.com", { bus });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.code).toBe("ALL_TIERS_UNAVAILABLE");
  });

  it("channel parity — same attempt list renders for CLI / Telegram / Web", async () => {
    const r = await runEscalationChain([tier1, tier2, tier3], "https://x.com", { bus });
    // Reconstruct attempt list by reading bus events (real path) or from envelope
    const env = parseWebToolResult(serializeWebToolResult(r))!;
    // Success path → re-build attempts from bus events for narration
    const events = (bus.emit as any).mock.calls.map((c: any) => c[0]).filter((e: any) => e.type === "web:tier_attempted");
    expect(events.length).toBeGreaterThan(0);
    // Channels
    const cliS = formatWebAttempts([{ tier:1,name:"http",outcome:"blocked",blockedReason:"cloudflare",httpStatus:403,durationMs:60 } as any], "cli");
    const telS = formatWebAttempts([{ tier:1,name:"http",outcome:"blocked",blockedReason:"cloudflare",httpStatus:403,durationMs:60 } as any], "telegram");
    const webS = formatWebAttempts([{ tier:1,name:"http",outcome:"blocked",blockedReason:"cloudflare",httpStatus:403,durationMs:60 } as any], "web");
    expect(cliS).toContain("http");
    expect(telS).toContain("http");
    expect(webS).toContain("http");
    expect(cliS).not.toEqual(telS);
    expect(telS).not.toEqual(webS);
  });
});
```

- [ ] **Step 2: Run — confirm PASS**

If the prior tasks compiled correctly, all 4 should pass.
Run: `npx vitest run __tests__/web-honesty-integration.test.ts`
Expected: 4/4 passing.

- [ ] **Step 3: Run full test suite**

Run: `npm test`
Expected: 0 failures across the entire repo.

- [ ] **Step 4: Run lint**

Run: `npm run lint`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add __tests__/web-honesty-integration.test.ts
git commit -m "test(web): end-to-end Phase A honesty integration test"
```

---

## Phase I — Final cleanup

### Task 26: Remove legacy `webFetch` paths and wire the dispatcher

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Modify: `src/tools/web.ts`

- [ ] **Step 1: Wire the new dispatcher into `webFetch()`**

Replace the body of the legacy `webFetch()` function in `src/browser/smart-fetch.ts` with a call to `runEscalationChain` using the three runners. Preserve the existing `FetchResult` shape so any other caller still compiles. Pseudocode replacement:
```typescript
export async function webFetch(url: string, options?: SmartFetchOptions): Promise<FetchResult> {
  const { default: bus } = await import("../gateway/event-bus.js"); // or accept injection
  const classifier = getOrCreateBlockingClassifier();
  const availability = getOrCreateAvailability();
  const tiers: TierRunner[] = [
    createHttpTier({ classifier }),
    createCamoFoxTier({ availability, client: getCamoFoxClient(), classifier }),
    createScraplingTier({ probe: scraplingProbe, runScrapling: realScrapling }),
  ];
  const result = await runEscalationChain(tiers, url, { bus });
  if (result.success && result.data.kind === "page") {
    return { title: result.data.title ?? "", url: result.data.url, text: result.data.content, length: result.data.content.length, source: "browser", blocked: false };
  }
  return { title: "", url, text: "", length: 0, source: "fetch", blocked: true, blockType: result.success ? undefined : (result.error.code === "ALL_TIERS_UNAVAILABLE" ? "all_tiers_unavailable" : "blocked") };
}
```

- [ ] **Step 2: Run all tests**

Run: `npm test`
Expected: all green.

- [ ] **Step 3: Update `web.ts` to surface the envelope directly (no more `webFetch` shim)**

Replace `src/tools/web.ts` `execute` so it calls `runEscalationChain` directly and returns the envelope (the Task 9 implementation already produces an envelope from `webFetch`'s legacy shape; this task removes the lossy reshape and lets the dispatcher's envelope flow through unchanged).

- [ ] **Step 4: Re-run full test suite**

Run: `npm test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts src/tools/web.ts
git commit -m "refactor(web): wire dispatcher; webFetch returns envelope passthrough"
```

---

## Verification (mirrors spec §11)

| # | Spec acceptance | Automated check or manual command |
|---|---|---|
| 1 | The "I'm blocked" lie is gone | `__tests__/web-tool-envelope.test.ts` (no narrative); `__tests__/web-runtime-prompt.test.ts` ensures prompt no longer asserts pre-tried tiers |
| 2 | CamoFox actually runs | `__tests__/web-honesty-integration.test.ts` — Tier 2 attempt produces a `TierAttempt` record; manual: `stackowl backends stats` after a real session |
| 3 | Honesty under failure | `__tests__/web-honesty-integration.test.ts` "ALL_TIERS_UNAVAILABLE when no backend ready" |
| 4 | Channel parity | `__tests__/web-narration.test.ts` + `__tests__/web-honesty-integration.test.ts` "channel parity" arm |
| 5 | No keyword arrays | `__tests__/web-smart-fetch.test.ts` "smart-fetch — keyword block deletion" regex regression test |
| 6 | start.sh is dev-only | `__tests__/start-sh-no-camofox.test.ts` |
| 7 | Tier 4 ghost is gone | `__tests__/web-smart-fetch.test.ts` proves every tier emits a `TierAttempt` record (no silent skip) |
| 8 | Telemetry gates Phase B | `__tests__/web-tracker-attempt-metadata.test.ts` + `__tests__/cli-backends-cmd.test.ts` "stats" subcommand renders per-tier success rates |

## Spec coverage map (§3 D1–D6, §4–§11)

| Spec section | Implemented in task(s) |
|---|---|
| §3 D1 — Envelope contract | Task 1 |
| §3 D2 — Umbrella stays only LLM-visible web tool + `hint` param | Task 10 |
| §3 D3 — CamoFox bootstrap (optionalDependencies + availability + onboarding) | Tasks 2, 20, 21, 24 |
| §3 D4 — Scrapling lazy probe | Tasks 7, 11 |
| §3 D5 — GoalVerifier coupling re-keyed off `error.code` | Tasks 15, 16 |
| §3 D6 — Anti-Bot Override directive rewrite | Task 19 |
| §4 — 3-tier escalation chain | Tasks 4, 5, 6, 7, 26 |
| §5 — `BlockingClassifier` + delete keyword block | Tasks 3, 8 |
| §6.1 — Tracker `attemptMetadata` | Tasks 13, 14 |
| §6.2 — Bus events | Tasks 4, 17 (also event-bus.ts union extended in Task 4) |
| §6.3 — Narration channel parity | Task 18 |
| §6.4 — `stackowl backends stats` | Task 22 |
| §7 — `RuntimeAvailability` map | Task 2 |
| §8 — Envelope helpers | Task 1 |
| §9 — File touch surface | All tasks |
| §10 — Risk register | Mitigations distributed: R1 alias (Task 1), R2 schema validation (Task 1), R3 fail-open (Task 3), R4 LRU TTL (Task 3), R5 repair (Task 22), R6 probe (Task 7), R7 envelope plain text (Task 11), R8 retention (Task 13 — additive only), R9 lastProbe (Task 2), R10 7-day window (Task 22 stats) |
| §11 — Verification | Verification section above |

## Spec ambiguities flagged (not improvised; left for engineer/Boss judgement)

1. **§5 latency budget — 200ms vs 180ms.** The architecture review (§4) sets a hard 180ms timeout; the spec body says "<200ms p95". Plan uses 200ms (matches the spec) — the architect can tighten to 180ms if user-visible latency needs more headroom. Test in Task 3 uses 200ms.
2. **§6.4 stats window source.** `stackowl backends stats` reads tracker rows; the spec doesn't specify whether unaggregated rows are summed in SQL or in code. Plan uses an injected `trackerStats` shape so either implementation can satisfy the test. The implementer chooses.
3. **§3 D2 — what is `web` umbrella's "fetch" arm wired to in production?** Currently `index.ts:695` wires `fetch` to the deprecated `web_crawl` execute. The plan rewrites `web.ts` (Task 9, 26) so `web_crawl` is also envelope-emitting, but the umbrella's `fetch` impl could equally be rewritten to call `runEscalationChain` directly and skip `web_crawl` entirely. Plan keeps the existing wiring (no change to `web-unified` `fetch` impl) — engineer may inline the dispatcher if preferred.
4. **§3 D3 — `package.json optionalDependencies` version pin.** The spec says "add `camofox-browser` under `optionalDependencies`" without a version. Task 24 uses `^0.1.0` as a placeholder; the implementer should `npm view camofox-browser version` and pin to the actual latest.
5. **`live_browser` availability probing.** The availability map includes a `"live-browser"` slot, but neither spec nor architecture review specifies its probe. Plan ships an empty default for the `live-browser` probe (always returns `{ installed: true, ready: true }` if the tool registers cleanly at boot). Phase B can refine if needed.

## Spec gaps detected and patched in the plan

1. **The spec declares `web:blocking_classified` event but does not specify the exact field types.** The plan locks the schema in Task 4 (event-bus union extension) — `{ url, source, latency, blocked, reason }` with `reason: string | null`. Self-consistent across Task 3 emission and the union definition.
2. **The spec does not say where `runEscalationChain` lives.** The plan places it in `src/browser/smart-fetch.ts` alongside the tier runners — keeps related code colocated; satisfies the "≤3 new files" constraint (only `envelope.ts`, `availability.ts`, `blocking-classifier.ts` are new).
3. **The spec doesn't specify how the registry decides which tools are "web tools" for envelope parsing.** The plan uses behaviour, not allow-listing: registry tries to parse every tool's result via `parseWebToolResult` and only mutates the result if parsing succeeds. Non-web tools never accidentally match because `parseWebToolResult` requires the closed-enum `error.code` and the `attemptedTiers[]` array shape — collision probability ≈ 0.
