# CamoFox Integration Specification

## Overview

**Goal:** Replace the entire browser stack (Puppeteer/Chromium) with CamoFox (Firefox-based anti-detection browser) as the primary web browsing solution in StackOwl.

**Scope:**

- Create `CamoFoxTool` with full REST API integration
- Replace `BrowserPool` + `smart-fetch.ts` with CamoFox-backed fetch
- Keep ScraplingTool as fallback
- Keep Computer Use Tool (separate CDP-based automation)
- Maintain `browser` tool name as alias for backward compatibility

---

## Background

### What is CamoFox?

- **Type:** Anti-detection browser automation REST API server
- **Engine:** Camoufox — Firefox fork with C++-level fingerprint spoofing (not JavaScript patches)
- **Differentiator:** Passes Google/Cloudflare bot detection that breaks Chromium-based solutions
- **Memory:** ~40MB idle (vs 100-200MB for Chromium)
- **API:** REST over HTTP — works with any language, any AI framework

### CamoFox vs Old BrowserStack (Chromium/Puppeteer)

| Aspect              | Old: BrowserPool (Chromium) | New: CamoFox                                                |
| ------------------- | --------------------------- | ----------------------------------------------------------- |
| Engine              | Chromium + Puppeteer        | Firefox fork (Camoufox)                                     |
| Anti-detection      | JS patches (fragile)        | C++ engine-level (robust)                                   |
| Element refs        | CSS selectors               | `e1, e2, e3` stable accessibility refs                      |
| Search macros       | None                        | `@google_search`, `@youtube_search`, `@amazon_search` + 10+ |
| YouTube transcripts | No                          | Yes (via yt-dlp)                                            |
| Memory per instance | ~100-200MB                  | ~40MB                                                       |
| API model           | Direct SDK (Node.js)        | REST API                                                    |
| Proxy/GeoIP         | Manual                      | Built-in                                                    |

---

## Architecture

### New Web Fetch Escalation

```
webFetch(url)
    │
    ├─► Tier 1: fetch() with Chrome headers
    │       └─► Blocked? → next
    │
    ├─► Tier 2: CamoFox REST API  ← PRIMARY
    │       └─► Unavailable? → next
    │
    └─► Tier 3: ScraplingTool  ← KEEP (fallback)
```

### New Search Escalation

```
searchTool(query)
    │
    ├─► DuckDuckGo HTML  ← KEEP (primary)
    │       └─► CAPTCHA? → next
    │
    └─► CamoFox @google_search  ← NEW fallback
```

### Tool Alias

```
LLM calls "browser" tool
    └─► camofox tool (alias)  ← backward compatibility
```

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     ToolRegistry                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ CamoFoxTool (NEW) — "camofox" + alias "browser"     │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ScraplingTool (KEEP) — Tier 3 fallback              │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ComputerUseTool (KEEP) — separate CDP automation    │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Smart-Fetch Layer                        │
│  Tier 1: fetch() — fast, plain HTTP                       │
│  Tier 2: CamoFox REST API  ← PRIMARY                      │
│  Tier 3: ScraplingTool  ← FALLBACK                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   CamoFox Server                            │
│  http://localhost:9377 (default)                          │
│  (can run on separate machine, Docker, etc.)               │
└─────────────────────────────────────────────────────────────┘
```

---

## Phases

### Phase 1: Core Tool — `src/tools/camofox.ts` (NEW)

**CamoFoxTool** wraps the CamoFox REST API with all supported actions.

**Actions supported:**

| Action       | Description                                                     |
| ------------ | --------------------------------------------------------------- |
| `start`      | Create a new CamoFox session (tab) with optional initial URL    |
| `navigate`   | Navigate to URL or use search macro (`@google_search query`)    |
| `snapshot`   | Get accessibility tree (eN refs + text, ~90% smaller than HTML) |
| `click`      | Click element by `eN` reference                                 |
| `type`       | Type text into element by `eN` reference                        |
| `screenshot` | Take screenshot (returns base64)                                |
| `stop`       | Close the session                                               |

**Tool definition:**

```typescript
{
  name: "camofox",
  description: `Anti-detection browser automation using CamoFox (Firefox-based).
  Use when other browsers get blocked by Cloudflare, Google, or bot detection.
  Supports: navigate, snapshot, click, type, screenshot.
  Search macros: @google_search, @youtube_search, @amazon_search, @reddit_subreddit, @wikipedia_search`,
  parameters: {
    type: "object",
    properties: {
      action: { type: "string", enum: ["start", "navigate", "snapshot", "click", "type", "screenshot", "stop"] },
      url: { type: "string", description: "URL or search macro (e.g. @google_search coffee shops)" },
      ref: { type: "string", description: "Element reference from snapshot (e.g. e5)" },
      text: { type: "string", description: "Text to type" },
      userId: { type: "string", description: "Session profile ID (default: 'stackowl')" }
    },
    required: ["action"]
  }
}
```

### Phase 2: Rewrite Smart-Fetch — `src/browser/smart-fetch.ts`

Replace Chromium tiers with CamoFox REST API:

```
Tier 1: fetch() with Chrome headers
    ↓ (blocked)
Tier 2: CamoFox REST API
    ↓ (unavailable/error)
Tier 3: ScraplingTool fallback
```

**Changes:**

- Remove `BrowserPool` dependency
- Remove all Puppeteer imports
- Add CamoFox REST client
- Add ScraplingTool as Tier 3

### Phase 3: DuckDuckGo Fallback — `src/tools/search.ts`

When DuckDuckGo HTML returns CAPTCHA:

1. Detect block (existing logic)
2. Fall back to CamoFox with `@google_search` macro
3. Parse results from CamoFox snapshot

### Phase 4: Remove Old Browser Files

**DELETE:**

- `src/compat/tools/browser.ts` (1331 lines)
- `src/browser/pool.ts` (380 lines)
- `src/browser/chrome.ts` (55 lines)

**REWRITE:**

- `src/browser/index.ts` — Re-export only `webFetch` and `initSmartFetch` (no pool)

### Phase 5: Config & Registration

**`src/config/loader.ts`:**

- Remove `browser:` config section
- Add `camofox:` config section:

```typescript
interface CamoFoxConfig {
  enabled: boolean;
  baseUrl: string; // default: "http://localhost:9377"
  apiKey: string | null;
  defaultUserId: string; // default: "stackowl"
  defaultTimeout: number; // default: 30000
}
```

**`src/index.ts`:**

- Remove `BrowserPool`, `BrowserTool` imports and registration
- Add `CamoFoxTool` import and registration

### Phase 6: Backward Compatibility — `browser` Alias

**`src/trust/chain.ts`:**

- Keep `browser` → `web_fetch` mapping
- Add `camofox` → `web_fetch` mapping

**`src/skills/executor.ts`:**

- Update `BrowserTool: "camofox"`

**Tool registration:**

- Register as `camofox` AND as `browser` (alias)

### Phase 7: Cleanup

**`src/compat/index.ts`:**

- Remove `BrowserTool` export

**`src/compat/profiles.ts`:**

- Update `ui` group: `browser` → `camofox`

**`package.json`:**

- Remove `puppeteer` if only used by browser tools
- Add `camofox-browser` as optional dependency

---

## File Changes Summary

### DELETE

| File                          | Lines | Reason                                          |
| ----------------------------- | ----- | ----------------------------------------------- |
| `src/compat/tools/browser.ts` | 1331  | Puppeteer BrowserTool — replaced by CamoFoxTool |
| `src/browser/pool.ts`         | 380   | BrowserPool Chromium — replaced by CamoFox REST |
| `src/browser/chrome.ts`       | 55    | Chrome discovery — no longer needed             |

### CREATE

| File                   | Purpose                                        |
| ---------------------- | ---------------------------------------------- |
| `src/tools/camofox.ts` | CamoFoxTool — all browser actions via REST API |

### REWRITE

| File                         | Change                                                            |
| ---------------------------- | ----------------------------------------------------------------- |
| `src/browser/smart-fetch.ts` | Replace Chromium tiers with CamoFox REST API + Scrapling fallback |
| `src/browser/index.ts`       | Re-export only webFetch/initSmartFetch                            |

### MODIFY

| File                     | Change                                                  |
| ------------------------ | ------------------------------------------------------- |
| `src/index.ts`           | Remove BrowserPool/BrowserTool, add CamoFoxTool         |
| `src/config/loader.ts`   | Replace `browser:` with `camofox:` config               |
| `src/tools/search.ts`    | Add CamoFox `@google_search` fallback                   |
| `src/compat/index.ts`    | Remove BrowserTool export                               |
| `src/compat/profiles.ts` | Update ui group                                         |
| `src/trust/chain.ts`     | Add camofox mapping, keep browser alias                 |
| `src/skills/executor.ts` | BrowserTool → camofox                                   |
| `package.json`           | Remove puppeteer (if browser-only), add camofox-browser |

### KEEP (UNCHANGED)

- `computer-use` tool — separate CDP-based automation
- `ScraplingTool` — Tier 3 fallback
- `WebCrawlTool` — uses webFetch
- Screen reader, human motion, planner, recipes — not browser-specific

---

## Acceptance Criteria

1. `camofox` tool is registered and callable by the LLM
2. `browser` tool name works as alias to `camofox` (backward compatibility)
3. `webFetch()` uses CamoFox as primary browser fetch (Tier 2)
4. ScraplingTool remains as Tier 3 fallback when CamoFox unavailable
5. DuckDuckGo search falls back to CamoFox `@google_search` on CAPTCHA
6. All old Puppeteer-based browser code removed
7. Config uses `camofox:` section (no `browser:`)
8. Build passes with no TypeScript errors
9. Tests updated and passing

---

## Decisions

| Question               | Decision                                         |
| ---------------------- | ------------------------------------------------ |
| Computer Use Tool      | Keep as-is — separate CDP-based macOS automation |
| ScraplingTool          | Keep — Tier 3 fallback when CamoFox unavailable  |
| Backward compatibility | `browser` alias → `camofox`                      |
| Config                 | Replace `browser:` with `camofox:`               |

---

## References

- Main Repo: https://github.com/jo-inc/camofox-browser
- TypeScript Fork: https://github.com/redf0x1/camofox-browser
- MCP Server: https://github.com/redf0x1/camofox-mcp
- Engine: https://github.com/daijro/camoufox
- Docs: https://camoufox.com
