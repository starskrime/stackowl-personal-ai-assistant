---
title: Element 16 — Web Browsing Honesty & Wiring (Phase A) — Design Spec
date: 2026-05-04
phase: Phase 4 of 5 — feeds the implementation plan (Phase 5)
inputs:
  - _bmad-output/planning-artifacts/element16-web-tools-audit-2026-05-04.md
  - _bmad-output/planning-artifacts/research/market-element16-web-tools-research-2026-05-04.md
  - _bmad-output/planning-artifacts/element16-web-tools-architecture-review-2026-05-04.md
status: HALT — awaiting Boss approval before Phase 5 (writing-plans)
constraints:
  - Phase A only — honesty + wiring. No new stealth backends.
  - Max 3 NEW files. Extend existing where possible.
  - No hardcoded keyword arrays — IntelligenceRouter cheap-tier only.
  - Compose existing primitives (IntelligenceRouter, GoalVerifier, ToolTracker, FallbackSequencer, GatewayEventBus, narration-formatter, onboarding wizard).
  - Channel parity (CLI / Telegram / Slack / Web).
  - start.sh is dev-only — no production install logic in start.sh.
  - Obscura, Patchright, and other new backends are Phase B-gated; out of scope.
---

# Element 16 — Web Browsing Honesty & Wiring (Phase A)

## 1. Why this exists

The user's most user-visible failure mode today is web fetching. When asked to look something up, the assistant frequently returns "I'm blocked" or "couldn't access that" — and the failure narrative is dishonest:

- **Tier 4 (CamoFox) is a ghost.** It's referenced in the escalation chain but never actually runs (`camofox-browser` not in `package.json`, default config has no `camofox` block, `start.sh:331–333` exits gracefully on install timeout without surfacing failure).
- **Tools return narrative strings, not structured errors.** `web.ts:56–65` returns `"BLOCKED: {url} — bot/CAPTCHA protection detected. The smart fetch layer tried HTTP → stealth Chromium → CamoFox..."`, claiming tiers were tried that never executed.
- **The dishonesty propagates.** `registry.ts:411–413` substring-matches `"BLOCKED:"` → wraps with `<tool_result_warning verdict="BLOCKED">` → LLM sees both narrative and verdict tag → echoes "I'm blocked" to the user.
- **Scrapling is unreachable.** `web-scrapling.ts:179–337` is a fully functional scraper but `deprecated:true`, so the LLM can't call it directly, and `web-unified.ts:22–84` doesn't dispatch to it either.
- **Prompt-vs-registry contradiction.** `runtime.ts:2367` "Anti-Bot Override" prompt directs the LLM to `scrapling_fetch` and `camofox` — both deprecated and hidden from `getAllDefinitions()`.

Phase A's mandate: **make the system honest about what it tried and what's available, wire CamoFox/Scrapling so they actually fire, and stop the assistant from declaring "blocked" before it has actually tried everything.** No new backends. Phase B (Obscura, Patchright, others) is gated on Phase A telemetry.

## 2. Scope

| In scope (Phase A) | Out of scope (Phase B or later) |
|---|---|
| Structured envelope contract for every web tool | New stealth backends (Obscura, Patchright as Tier replacements) |
| 3-tier `smart-fetch.ts` (http → camofox → scrapling) | `force_tier` parameter / explicit tier override flag |
| Promote camofox to Tier 2 (was Tier 4) | Multi-engine fingerprint randomization |
| `live-browser` stays peer to umbrella (interactive only) | Cross-tier fingerprint coordination |
| Replace hardcoded keyword arrays with IntelligenceRouter classifier | Captcha-solving service integrations |
| Onboarding wizard installs backends | Residential proxy rotation |
| `stackowl backends` CLI subcommand | Browserbase / commercial managed-stealth integration |
| Per-tier telemetry (`attemptMetadata`) | Phase B/C decision automation |
| Channel-parity narration templates | |
| Delete start.sh's camofox install block (lines 270–333) | |

## 3. Six locked architectural decisions

These are inherited from Winston's Phase 3 architecture review and refined by brainstorming. Phase 5 implementation does not negotiate them.

### D1 — Envelope contract

Every web tool returns a JSON-stringified `WebToolResult`:

```ts
type WebToolResult =
  | { success: true;  data: WebToolData }
  | { success: false; error: WebToolError };

type WebToolError = {
  code: WebToolErrorCode;          // canonical signal
  message: string;                 // one-sentence human summary
  attemptedTiers: TierAttempt[];   // ordered, every tier we actually tried
  suggestedEscalation?: string;    // optional next-action hint for the LLM
};

type TierAttempt = {
  tier: number;                    // 1..3 (Phase A: http/camofox/scrapling)
  name: 'http' | 'camofox' | 'scrapling';
  durationMs: number;
  outcome: 'success' | 'blocked' | 'timeout' | 'unavailable' | 'error' | 'skipped-by-hint';
  blockedReason?: 'cloudflare' | 'captcha' | 'paywall' | 'rate-limit' | 'access-denied' | 'other';
  httpStatus?: number;
};

type WebToolErrorCode =
  | 'BLOCKED_BY_ANTI_BOT'
  | 'PAYWALL'
  | 'RATE_LIMITED'
  | 'TIMEOUT'
  | 'NOT_FOUND'
  | 'INVALID_URL'
  | 'ALL_TIERS_UNAVAILABLE'   // honesty case: no tier could even be tried
  | 'INTERNAL_ERROR';

type WebToolData =
  | { kind: 'page';   url: string; title?: string; content: string;  contentType?: string }
  | { kind: 'search'; query: string; results: { title: string; url: string; snippet?: string }[] };
```

**Backwards-compat alias:** `error.message` includes the literal substring `"BLOCKED:"` whenever `error.code === 'BLOCKED_BY_ANTI_BOT'` or `'ALL_TIERS_UNAVAILABLE'`. Any prompt fragment in the codebase mentioning "BLOCKED" still matches. Nothing existing breaks.

**Where the envelope is enforced:** `src/tools/registry.ts:273–438` (`execute()`). The registry tries to `JSON.parse` the tool's string return; if it parses as `WebToolResult`, the new code path runs. If not, the legacy path runs unchanged. Non-web tools are untouched.

### D2 — LLM surface: umbrella stays the only visible web tool

- The umbrella `web` tool stays the LLM's primary web fetcher.
- `camofox` and `scrapling` remain `deprecated:true` (hidden from `getAllDefinitions()`).
- They're invoked **inside** `smart-fetch.ts` as Tier 2 / Tier 3.
- `live-browser` stays a peer tool (LLM-visible, standalone). It is **not** in the umbrella's escalation chain. It's invoked when the user asks to *see* something, when authentication is needed, or when human-visible interaction (clicking, form-filling) is required.
- Optional `hint?: 'anti-bot'` parameter on the umbrella allows the LLM to pre-select a starting tier. `hint: 'anti-bot'` skips Tier 1 and starts at Tier 2. Skipped tiers still appear in `attemptedTiers[]` with `outcome: 'skipped-by-hint'` for honesty. The hint is a closed enum in Phase A (only `'anti-bot'`); additional values may be added in Phase B if telemetry justifies.
- No `force_tier` parameter in Phase A. (Deferred to Phase B if telemetry justifies.)

### D3 — CamoFox bootstrap: production install path lives in the assistant, not start.sh

- `start.sh:270–333` (the camofox install block) is **deleted**. start.sh becomes pure dev tooling.
- The existing onboarding wizard (`src/cli/onboarding.ts`) gets a new subsection in **Section D — Features**: "Stealth web backends." Multi-select menu listing camofox, scrapling, live-browser; selecting one runs an in-line installer.
- A new `stackowl backends` CLI subcommand provides the same install/repair surface from anywhere:
  - `stackowl backends list` — prints availability map
  - `stackowl backends install` — re-opens the multi-select menu
  - `stackowl backends repair` — re-probes and repairs broken installs
  - `stackowl backends stats` — prints per-tier success rates from `ToolTracker`
- `~/.stackowl/runtime-availability.json` is the single source of truth on what's installed and ready. Schema: `{ camofox: { installed, version, lastProbe, ready }, scrapling: { ... }, live-browser: { ... } }`.
- `src/index.ts:266–273` (`initCamoFox()` site) reduces to a probe-and-record (no install attempt). Boot proceeds regardless of availability.
- `package.json` keeps `camofox-browser` under `optionalDependencies` so `npm install` of the assistant package never hard-fails on environments where the user hasn't run onboarding yet.

### D4 — Scrapling pipe: lazy probe, no auto-install

- First time scrapling is needed in a session, `python3 -c "import scrapling; print(scrapling.__version__)"` runs once. Result cached for the session.
- No auto-install, no interactive prompts mid-conversation.
- If the import fails: envelope `suggestedEscalation` carries `pip install scrapling[all] && patchright install chromium`.
- The LLM surfaces the install command via whatever channel is active. The user runs the command on their host machine.

### D5 — GoalVerifier coupling: keyed off `error.code`, not substrings

- `src/tools/registry.ts:411–413` is rewritten to:
  - Try parsing the tool's string return as `WebToolResult`.
  - If it parses and `success === false`: build a new `<tool_attempt_summary>` XML block from the envelope's `attemptedTiers[]` (one `<tier>` element per attempt) and pass `error.code` to `GoalVerifier`.
  - If it doesn't parse (non-web tools, or older code paths): legacy `<tool_result_warning>` path runs unchanged.
- `src/tools/goal-verifier.ts:41–49` SYSTEM_PROMPT loses the literal `"BLOCKED"` string-match cue. New cue:
  > "If the tool reports `success:false` with `error.code: BLOCKED_BY_ANTI_BOT` or `ALL_TIERS_UNAVAILABLE`, classify as BLOCKED. If `error.code: TIMEOUT`, classify as PARTIAL. If `success:true`, classify based on whether `data` answers the goal."
- `userContent` passed to the verifier is **rebuilt from the parsed envelope** (a humanized summary the verifier LLM can reason about), not the raw JSON string.
- Verdict contract (ADVANCES / PARTIAL / BLOCKED / NEUTRAL) preserved. Existing verifier callers don't change.

### D6 — Anti-Bot Override directive: generic, envelope-driven

- `src/engine/runtime.ts:2367` ("Anti-Bot Override") prompt is rewritten to be **generic, not enumerated**.
- Old: hardcoded "escalate to `scrapling_fetch`, `camofox`" (both deprecated and invisible — actively contradictory).
- New: "*If you see a `<tool_attempt_summary>` showing a tier as `unavailable`, surface the install command in `suggestedEscalation` to the user. If all tiers were tried and blocked, tell the user honestly which tiers failed and why; offer to try `live_browser` if the site might need login.*"
- The envelope is self-describing — no per-boot prompt regeneration, no enumeration of installed backends. Robust to mid-session backend failures.

## 4. The 3-tier escalation chain (`smart-fetch.ts`)

```
Tier 1: http       — fetch + headers, fast path (no JS, ~4s budget)
Tier 2: camofox    — stealth Firefox, primary rendered fetcher (~20s budget)
Tier 3: scrapling  — Python anti-bot via Patchright, alternative evasion (~25s budget)
                     Total envelope budget: 60s (TIMEOUT after that)
```

**Why 3 tiers, not 4 or 5:**
- The legacy `stealth-chromium` tier is dropped. Camoufox renders JS *and* evades anti-bot — unstealth Chromium was a worse subset.
- `live-browser` is excluded from the chain (different mode: interactive/auth/visual).
- Camofox is promoted from Tier 4 → Tier 2 (was the ghost, now the primary).
- Scrapling provides genuine fingerprint diversity (different Patchright build, different evasion).

**Per-tier protocol:** Each tier's runner returns a `TierAttempt` record regardless of outcome (success, blocked, timeout, unavailable, error). The dispatcher (`runEscalationChain`) collects the array. On the first `outcome: 'success'`, it returns `{ success: true, data }` immediately. If all tiers exhaust without success, it returns `{ success: false, error }` with the full attempt list.

**Hint handling:** If `hint === 'anti-bot'`, Tier 1 is recorded as `outcome: 'skipped-by-hint'` and the chain starts at Tier 2. `hint` is a closed enum in Phase A — only `'anti-bot'` is accepted. Unknown values are ignored (treated as no hint).

## 5. Hardcoded-keyword replacement (`BlockingClassifier`)

**NEW FILE 1 of 3:** `src/browser/blocking-classifier.ts` (~120 LOC)

```ts
export type BlockingClassification = {
  blocked: boolean;
  reason?: 'cloudflare' | 'captcha' | 'paywall' | 'rate-limit' | 'access-denied' | 'other';
  confidence: number;  // 0..1
  source: 'cache' | 'router' | 'fallback';
};

export class BlockingClassifier {
  constructor(
    private router: IntelligenceRouter,
    private bus: GatewayEventBus,
  );
  async classify(input: {
    url: string;
    httpStatus: number;
    bodyPreview: string;  // first 2KB of response body
    headers?: Record<string, string>;
  }): Promise<BlockingClassification>;
}
```

**Classifier semantics:**
- Calls `IntelligenceRouter.resolve('classification')` (cheap-tier model).
- Schema-validated structured JSON output. Closed enum for `reason`.
- Latency budget: **<200ms p95**. On overrun: return `{ blocked: false, source: 'fallback', confidence: 0 }` (fail-open).
- LRU cache keyed by `(host + httpStatus + bodyHash)`. TTL 1h, cap 1000 entries. Reduces classifier calls ~80% on real workloads.

**Lazy invocation in `smart-fetch.ts`:**
- Classify only when one of the following triggers is met on a tier's response:
  - HTTP status in `{401, 403, 429, 503, ≥500}`
  - Response body length < 1KB
  - Response is a redirect to a different host
- Clean 200 responses skip classification (trust them, return).
- Worst case: 1–2 classifier calls per fetch. Median: 0.

**Deletion:** `src/browser/smart-fetch.ts:101–147` (the keyword block) is removed entirely. No keyword arrays anywhere. No regex on response bodies.

**Fail-open semantics:** Classifier timeout, invalid JSON, IntelligenceRouter unavailable → response treated as **not blocked** and returned as-is. False-negative-block is cheap (return an unhelpful page); false-positive-block is expensive (slow chain, wasted backend calls).

**Audit trail:** Every classification (cache hit or router call) emits `web:blocking_classified { url, source, latency, blocked, reason }` on `GatewayEventBus`. Fits the same telemetry pipeline as Section 6.

## 6. Telemetry & narration

### 6.1 `ToolTracker` schema extension (additive)

`src/tools/tracker.ts:22–57` extended with optional `attemptMetadata` field:

```ts
type AttemptMetadata = TierAttempt[];  // matches the envelope shape
```

Persisted to existing tracker DB table via additive migration. Existing rows have `attemptMetadata: null` — backwards compatible.

### 6.2 New bus events (fine-grained)

```
web:tier_attempted        { tier, name, url, startedAt }
web:tier_blocked          { tier, name, blockedReason, durationMs }
web:escalating            { fromTier, toTier, reason }
web:blocking_classified   { url, source, latency, blocked, reason }   // from §5
```

Emitted by `smart-fetch.ts` between tier transitions and by `BlockingClassifier`. Subscribed by:
- `narration-formatter` for channel-aware rendering
- `ToolTracker` to assemble the `attemptMetadata` array on `tool:result`

### 6.3 Narration templates (channel parity)

New web-specific templates in `src/narration/narration-formatter.ts`:

| Channel | Pattern |
|---|---|
| **CLI** | Streamed inline as the chain runs: `→ http (403)` `→ camofox (cloudflare)` `→ scrapling (success)`. Visible journey. |
| **Telegram / Slack** | Single typed message after the chain settles: *"Tried 3 ways: HTTP got 403, camofox saw Cloudflare, scrapling worked."* Rate-limit-friendly. |
| **Web UI** | Collapsible per-tier breakdown. Default-collapsed; user expands to see attempt list. |

Same `attemptMetadata` array drives all three. Channel parity = identical *information*, channel-appropriate *form*.

### 6.4 Telemetry as the Phase A → B gating signal

`stackowl backends stats` reads the tracker DB and prints:

```
=== Web fetch stats (last 7 days) ===
Tier 1 (http):       success rate  62%  (n=3,481 / 5,617)
Tier 2 (camofox):    success rate  78%  (n=1,654 / 2,136)  reached after Tier 1 failed
Tier 3 (scrapling):  success rate  41%  (n=198 / 482)      reached after Tier 2 failed
ALL_TIERS_UNAVAILABLE: 12% of fetches  (gating threshold for Phase B: ≥15%)
```

This is the metric that decides whether Phase B (new backends) is justified. Phase B kicks off only if `ALL_TIERS_UNAVAILABLE` rate exceeds 15% over a 7-day window with ≥500 fetches.

## 7. Runtime availability

**NEW FILE 2 of 3:** `src/runtime/availability.ts` (~80 LOC)

```ts
export type BackendStatus = {
  installed: boolean;
  version?: string;
  lastProbe: string;       // ISO 8601
  ready: boolean;          // last probe result
  lastError?: string;
};

export type AvailabilityMap = {
  camofox:     BackendStatus;
  scrapling:   BackendStatus;
  'live-browser': BackendStatus;
};

export class RuntimeAvailability {
  constructor(private path: string = '~/.stackowl/runtime-availability.json');
  async load(): Promise<AvailabilityMap>;
  async update(backend: keyof AvailabilityMap, status: Partial<BackendStatus>): Promise<void>;
  async isReady(backend: keyof AvailabilityMap): Promise<boolean>;
  async probeAll(): Promise<AvailabilityMap>;  // re-probes every backend
}
```

**Read by:** `smart-fetch.ts` (decides whether to attempt Tier 2/3), envelope builder (puts install command in `suggestedEscalation` when a tier is unavailable), `runtime.ts` Anti-Bot Override directive.

**Written by:** Onboarding wizard (after install), `stackowl backends repair`, `index.ts:266–273` boot probe, runtime health checks.

## 8. Envelope types & helpers

**NEW FILE 3 of 3:** `src/browser/envelope.ts` (~100 LOC)

Single source of truth for the envelope types. Contains:
- All TypeScript types from §3 D1
- `serializeWebToolResult(result: WebToolResult): string` — JSON-stringify with backwards-compat alias injection (adds "BLOCKED:" prefix to `error.message` when applicable)
- `parseWebToolResult(s: string): WebToolResult | null` — safe parse + schema validation; returns null on invalid input
- Type guards (`isWebToolResult`, `isWebToolError`)
- `buildAttemptSummaryXml(result: WebToolResult): string` — renders the `<tool_attempt_summary>` XML block consumed by `registry.ts`

Imported by: `web.ts`, `web-unified.ts`, `web-scrapling.ts`, `camofox.ts`, `smart-fetch.ts`, `registry.ts`.

## 9. File touch surface

| File | Change | Δ LOC |
|---|---|---|
| `src/browser/blocking-classifier.ts` | **NEW FILE 1** — IntelligenceRouter classifier | +120 |
| `src/runtime/availability.ts` | **NEW FILE 2** — runtime availability map | +80 |
| `src/browser/envelope.ts` | **NEW FILE 3** — envelope types + helpers | +100 |
| `src/browser/smart-fetch.ts` | Refactor to 3-tier, delete keyword block, use envelope + classifier | -50 / +180 |
| `src/browser/camofox-client.ts` | Add health-check + readiness probe surface | +30 |
| `src/tools/web.ts` | Replace narrative-string returns with envelope | -20 / +30 |
| `src/tools/web-unified.ts` | Add `hint` param, route through envelope | -10 / +25 |
| `src/tools/web-scrapling.ts` | Lazy probe, return envelope, surface install command | -15 / +40 |
| `src/tools/camofox.ts` | Stays `deprecated:true`; consume availability map | +15 |
| `src/tools/registry.ts` | Lines 411–413 rewrite: parse envelope, emit `<tool_attempt_summary>` | -10 / +50 |
| `src/tools/goal-verifier.ts` | Re-target SYSTEM_PROMPT, rebuild userContent from envelope | -10 / +25 |
| `src/tools/tracker.ts` | Additive `attemptMetadata` field | +20 |
| `src/engine/runtime.ts` | Rewrite Anti-Bot Override directive (generic) | -8 / +12 |
| `src/index.ts` | `initCamoFox()` → probe-and-record only | -10 / +15 |
| `src/cli/onboarding.ts` | New "Stealth web backends" subsection in Section D | +180 |
| `src/cli/commands.ts` | `stackowl backends` subcommand (list/install/repair/stats) | +200 |
| `src/narration/narration-formatter.ts` | Web-specific event templates | +120 |
| `start.sh` | **DELETE lines 270–333** (camofox install block) | -64 |
| `package.json` | Confirm `camofox-browser` under `optionalDependencies` | ±0 |

**Total: 3 new files, 13 existing files extended, 1 file shrunk (start.sh), 0 files duplicated.** Within the standing-rule budget.

## 10. Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | Existing prompts mention literal "BLOCKED:" string and break | `error.message` always includes "BLOCKED:" prefix when `error.code` is `BLOCKED_BY_ANTI_BOT` or `ALL_TIERS_UNAVAILABLE`. Regression-tested. |
| R2 | Non-web tool returns happen to JSON-parse as `WebToolResult` shape (false positive) | Schema validation in `parseWebToolResult` requires the closed enum on `error.code` and the array structure on `attemptedTiers`. Probability of accidental match ≈ 0. |
| R3 | `IntelligenceRouter` unavailable at boot or under load | Classifier fail-open path returns `{ blocked: false }`. Chain still completes; we just trust the response. |
| R4 | LRU cache poisons after a one-time site change | TTL 1h bounds staleness. Cache key includes `bodyHash` so same site with new body misses cache and re-classifies. |
| R5 | `stackowl backends install` fails halfway (npm partial install) | `repair` subcommand re-probes and re-installs idempotently. `runtime-availability.json` records `ready: false` so the chain treats the backend as `unavailable`. |
| R6 | Patchright (scrapling's underlying library) breaks on a Chromium update | Scrapling's lazy probe catches it; envelope `suggestedEscalation` says `stackowl backends repair scrapling`. User-recoverable. |
| R7 | Telegram users can't run `stackowl backends install` | Envelope's suggestion is plain text — user runs the command on their host. Channel-agnostic. Documented in `stackowl backends help`. |
| R8 | Tracker DB grows unbounded with `attemptMetadata` | Existing tracker has retention policy (Element 14); `attemptMetadata` participates in the same retention sweep. |
| R9 | Backend version drift between assistant and runtime-availability.json | `lastProbe` timestamp + version field; probe re-runs if `lastProbe > 24h ago`. |
| R10 | Phase B/C decision based on faulty telemetry (e.g., low traffic) | Gating threshold requires ≥500 fetches in the 7-day window before evaluating `ALL_TIERS_UNAVAILABLE` rate. |

## 11. Verification (how we know Phase A worked)

After implementation:

1. **The "I'm blocked" lie is gone.** `grep -r "I'm blocked" src/` returns no narrative-string asserts. Tools return envelopes; the LLM only declares blocked when `error.code` confirms it.
2. **CamoFox actually runs.** `stackowl backends stats` shows `Tier 2 (camofox)` with non-zero attempts after the user installs it.
3. **Honesty under failure.** When the user removes camofox manually (`stackowl backends uninstall camofox`), the next blocked-site fetch returns `error.code: ALL_TIERS_UNAVAILABLE` with attemptedTiers showing camofox `outcome: 'unavailable'`. The user is told to reinstall — no false "blocked" claim.
4. **Channel parity.** Same fetch on CLI / Telegram / Slack / Web shows identical attempt-list information, channel-appropriate form. Verified with end-to-end tests on each channel.
5. **No keyword arrays.** `grep -rn "cloudflare\|captcha\|access denied" src/browser/` returns matches only in test fixtures and the classifier's prompt template — never in runtime code paths.
6. **start.sh is dev-only.** `grep -rn "camofox\|scrapling" start.sh` returns empty. Production install lives in onboarding + `stackowl backends`.
7. **Tier 4 ghost is gone.** No silent-skip path. Every tier attempt produces a `TierAttempt` record.
8. **Telemetry gates Phase B.** `stackowl backends stats` produces the data needed to decide Phase B kickoff.

## 12. What this spec does NOT design (intentional)

- New stealth backends (Obscura, Patchright as Tier replacements) — Phase B.
- LLM-driven tier-override flag (`force_tier`) — Phase B if telemetry justifies.
- Captcha-solving service integration — out of charter.
- Residential proxy rotation — out of charter.
- Web UI live browser embedding — separate element.
- Cross-tier fingerprint coordination — Phase C.

These are explicitly deferred. The brainstorming session considered each and locked them out for Phase A.
