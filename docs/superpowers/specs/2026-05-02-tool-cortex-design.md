# Element 7 — Tool Cortex Design Spec

**Status:** Draft — approved in chunks  
**Authors:** Winston (System Architect), John (Product Manager)  
**Date:** 2026-05-02  
**Element:** 7 of 18 in the platform audit  

---

## 1. Context & Problem Statement

StackOwl has ~65 tools. Despite their breadth, users experience three recurring pain points:

1. **The owl goes silent, then gives up.** A tool fails; the assistant reports failure or fabricates a success message rather than trying an alternative. There is no post-execution critique — the LLM is the only thing checking whether a tool result actually answered the question, and it fails this check often.

2. **Multiple tools for the same work.** Five tools can "get web content" (web_crawl, duckduckgo_search, camofox, scrapling, browser). Five tools can "recall memory" (MemorySearch, MemoryGet, RecallMemory, Remember, PelletRecall). LLMs are sensitive to catalog size and overlap — more choices with similar descriptions leads to worse selection.

3. **Tools are hard to improve and hard to add.** No standard interface for timeouts, retries, or error reporting. No scaffolding to create new tools. No mechanism for tools to improve from failure data. Quality compounds downward as the catalog grows.

**Platform constraint (locked):** StackOwl runs on Windows, macOS, and Linux. Every tool must declare `platforms: NodeJS.Platform[]`. The runtime enforces this — platform-mismatched tool calls return structured errors, never silent failures. macOS-only tools (Calendar, Mail, etc.) are valid; they just declare `platforms: ["darwin"]`.

### What is already in place (from Elements 1–6)

| Primitive | Location | Relevance |
|-----------|----------|-----------|
| `TaskLedger.subGoals[]` | `src/engine/types.ts` | Goal state — what the owl is trying to do |
| `OwlOrchestrator` 7-phase loop | `src/engine/orchestrator.ts` | Where tool execution happens |
| `trajectory_turns` SQLite table | `src/memory/db.ts:712` | Per-tool args/result/success/duration — mineable |
| `IntelligenceRouter.resolve()` | `src/intelligence/router.ts` | Cheap-tier LLM access via `"classification"` task type |
| `PatchTool` / `ToolSmith` | `src/tools/toolsmith.ts` | Already rewrites tool source on demand |
| `EventBus` | `src/gateway/event-bus.ts` | Typed pub/sub — used for cache invalidation today |
| `fastembed` semantic search | `src/session/user-memory-store.ts` | Cosine similarity — reusable for PTR |
| `MCPManager` | `src/tools/mcp/manager.ts` | Auto-registers MCP tools into ToolRegistry |
| `FallbackSequencer` | `src/tools/fallback-sequencer.ts` | Static fallback map — learning evaporates on restart |

---

## 2. Architecture Overview

Element 7 is delivered in four phases. Phases 7a and 7d can be developed in parallel on separate branches. Phases 7b and 7c are gated — they require production data from 7a before they produce value.

```
Phase 7a — Verification & Narration        (ships Week 1–2)
  GSN  Graduated Status Narration          tool execution becomes visible to user
  GAV  Goal-Anchored Verifier              tool results verified against active sub-goal
  TC   Tool Catalog Cleanup                web 5→1, memory 5→1, native 15→4

Phase 7d — Quality & Coverage              (parallel with 7a, ships Week 3–5)
  LBC  Live Browser Control               cross-platform Safari/Chrome on user's screen
  MCP  MCP Full Lifecycle + Marketplace   /mcp CRUD in Telegram and CLI
  TQP  Tool Quality Pass                  30 tools get timeout/retry/error envelope
  NT   5 New Tools                        vision, document, sandbox, db_query, schedule
  TSF  Tool Scaffolder                    npm run tool:create generates boilerplate

Phase 7b — Memory-Driven Routing           (gated on 7a — starts Month 2)
  CWTG Cost-Weighted Tool Graph           LLM-free recovery via Dijkstra
  PTR  Personalized Tool Routing          inject owl's own success history as prior

Phase 7c — Self-Evolution & Provenance     (gated on 7b — starts Month 3)
  SET  Self-Evolving Tools (workspace)    failing tools rewrite themselves
  FPC  Fact Provenance Chain              retract bad facts before they cascade
```

### Phase gate rules

| Gate | Condition to proceed |
|------|---------------------|
| 7a → 7b | After 1 week in production: verifier BLOCKED rate > 5% confirms routing data is worth mining |
| 7b → 7c | After 2 weeks: ≥500 verified trajectory_turns with `verification_result` populated |
| Either gate | If condition not met, defer — don't ship routing that has no signal to route on |

---

## 3. Phase 7a — Graduated Status Narration (GSN)

### Problem
Tool execution is invisible. The user sends a message, waits, and either gets an answer or a failure. When a 4-tool turn takes 12 seconds, there is no signal that anything is happening. When a tool fails, there is no narration — the LLM silently absorbs the error and decides what to tell the user, often fabricating confidence.

### Design

**EventBus extension.** Six new typed events added to `src/gateway/event-bus.ts`:

```typescript
"tool:start"       { toolName: string; args: Record<string, unknown>; turnId: string }
"tool:result"      { toolName: string; success: boolean; durationMs: number; truncated: boolean }
"tool:retry"       { toolName: string; attempt: number; reason: string }
"tool:fallback"    { fromTool: string; toTool: string; reason: string }
"tool:goal_advance"  { toolName: string; subGoal: string; verdict: "ADVANCES"|"PARTIAL" }
"tool:goal_blocked"  { toolName: string; subGoal: string; suggestion?: string }
```

**ToolRegistry emission points** (`src/tools/registry.ts`):
- Before `tool.execute()`: emit `tool:start`
- After success: emit `tool:result { success: true }`
- After failure: emit `tool:result { success: false }`
- On retry (from ExecutionPolicy): emit `tool:retry`
- On fallback: emit `tool:fallback`
- After GAV verdict (Phase 7a.2): emit `tool:goal_advance` or `tool:goal_blocked`

**NarrationFormatter** (`src/gateway/narration-formatter.ts`):

Pure function. No LLM calls. Template-driven for speed.

```typescript
format(event: ToolEvent): string | null
```

Examples:
```
tool:start { toolName:"web", args:{action:"search",query:"TypeScript 5.5"} }
  → "Searching the web for TypeScript 5.5..."

tool:fallback { fromTool:"web_crawl", toTool:"web_interact" }
  → "Page blocked, switching approach..."

tool:goal_blocked { toolName:"web", suggestion:"try web(action:'interact')" }
  → "Ran into a wall here, trying another way..."

tool:result { success:false, durationMs:30000 }
  → "That timed out, looking for alternatives..."
```

Returns `null` for events that don't warrant user-visible narration (e.g. `tool:result { success:true }` on a fast tool — no need to clutter).

**Channel adapter wiring:**
- `src/gateway/adapters/cli.ts` — print narration lines prefixed with `⟳ ` before the final response
- `src/gateway/adapters/telegram.ts` — edit a single "working..." message in place (grammY `ctx.reply` → `ctx.api.editMessageText`) to avoid message spam
- `src/gateway/adapters/slack.ts` — update ephemeral status message

**Config flag:** `narration.enabled: boolean` (default `true`). When false, events still fire (GAV depends on them) but NarrationFormatter output is suppressed at the adapter layer.

### Files
| Action | Path |
|--------|------|
| Modify | `src/gateway/event-bus.ts` |
| Modify | `src/tools/registry.ts` |
| Modify | `src/gateway/adapters/cli.ts` |
| Modify | `src/gateway/adapters/telegram.ts` |
| Modify | `src/gateway/adapters/slack.ts` |
| Create | `src/gateway/narration-formatter.ts` |

---

## 4. Phase 7a — Goal-Anchored Verifier (GAV)

### Problem
Tool results today are either passed directly to the LLM (if execution succeeded) or thrown as errors (if execution failed). Neither path checks whether the result actually advanced the user's goal. The LLM infers this from prose — and is wrong often enough to drive the "giving up" behaviour users report.

### Design

**Core concept.** After every tool execution, a cheap-tier LLM call checks: *"Given sub-goal X, does this tool output advance it?"* The verifier runs BEFORE returning the result to the main LLM. If the result is `BLOCKED`, the registry triggers a fallback rather than passing bad data forward.

**Critical constraint: different model from main LLM.** Research (2026 Galileo production analysis) shows that verifier + generator using the same model share correlated blindspots — the verifier approves what the generator would have produced. The cheap tier (`IntelligenceRouter.resolve("classification")`) must be a different model family from the conversation tier.

**GoalVerifier class** (`src/tools/goal-verifier.ts`):

```typescript
interface VerifierArgs {
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string
  subGoal: string          // LedgerWithMeta.subGoals[i].description
  userMessage: string
}

type VerifierVerdict = "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL"

interface VerifierResult {
  verdict: VerifierVerdict
  reason: string           // one sentence
  suggestion?: string      // what to try next on BLOCKED
}

class GoalVerifier {
  async verify(args: VerifierArgs): Promise<VerifierResult>
}
```

Prompt is fixed and short (~150 tokens input). Model call via `IntelligenceRouter.resolve("classification")` → `provider.chat()` (same pattern as `src/evolution/detector.ts:132`). Target p95 latency: 400ms.

**Skip rules** (applied in order, first match wins):

| Rule | Condition | Action |
|------|-----------|--------|
| No sub-goal | `engineContext.activeSubGoal` is null | Skip — return NEUTRAL |
| High-confidence tool | Tool's historical `success_rate >= 0.90` on this `subgoal_id` | Skip — return NEUTRAL |
| Cognitive category | `tool.category === "cognitive"` | Skip — self-reflective tools |
| Batch mode | N parallel tools fired in same turn | Verify batch in ONE call, not N calls |
| NEUTRAL result | Tool returned empty string | Skip — verifier has nothing to check |

**Registry hook** (`src/tools/registry.ts:232–237`):

```typescript
// after tool.execute() succeeds, before returning result
if (context.engineContext?.activeSubGoal && !shouldSkipGAV(tool, context)) {
  const verdict = await goalVerifier.verify({
    toolName: name,
    toolArgs: args,
    toolResult: result,
    subGoal: context.engineContext.activeSubGoal.description,
    userMessage: context.engineContext.userMessage ?? ""
  })
  recordVerification(name, verdict)          // writes to trajectory_turns
  emitVerifierEvent(verdict)                 // fires tool:goal_advance or tool:goal_blocked
  if (verdict.verdict === "BLOCKED") {
    result = triggerFallback(name, result, verdict.suggestion)
  } else if (verdict.verdict === "PARTIAL") {
    result = wrapPartial(result, verdict.reason)  // <tool_result_warning> envelope
  }
}
```

**Fallback on BLOCKED (Phase 7a):** Uses existing static `TOOL_FALLBACKS` map from `fallback-sequencer.ts`. Phase 7b replaces this with CWTG Dijkstra. The interface is the same — `triggerFallback(toolName, failedResult, suggestion)` — so 7a and 7b are drop-in compatible.

**Schema v17** (`src/memory/db.ts`):
```sql
ALTER TABLE trajectory_turns ADD COLUMN verification_result TEXT;
ALTER TABLE trajectory_turns ADD COLUMN verifier_reason TEXT;
ALTER TABLE trajectory_turns ADD COLUMN subgoal_id TEXT;
```

**Orchestrator change** (`src/engine/orchestrator.ts`): Before each tool-call turn, set `ctx.engineContext.activeSubGoal` to the current open sub-goal from `LedgerWithMeta.subGoals`. Clear it when no ledger is active (simple conversations).

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/goal-verifier.ts` |
| Modify | `src/tools/registry.ts` |
| Modify | `src/engine/orchestrator.ts` |
| Modify | `src/memory/db.ts` (schema v17) |

---

## 5. Phase 7a — Tool Catalog Cleanup (TC)

### Problem
The LLM sees ~65 tool schemas every turn. Five tools overlap on "get web content." Five overlap on "recall memory." Fifteen separate macOS tools each contribute a full schema to the context window. This drives two failure modes: wrong tool selection and inflated context cost (~3KB wasted on tool schemas per turn).

### Design

**Three consolidations. One deprecation pattern.**

Deprecated tools remain registered and callable internally (back-compat, MCP tools may reference them). They are excluded from `getDefinitions()` via a `deprecated: true` flag on `ToolImplementation`. The LLM never sees them.

**Consolidation 1 — Web (5 → 1)**

New: `src/tools/web-unified.ts` — `web(action: "search" | "fetch" | "interact", ...)`

| Action | Internal dispatch |
|--------|-----------------|
| `search` | DuckDuckGo → BraveSearch fallback |
| `fetch` | smart-fetch layer (HTTP → stealth Chromium → CamoFox) |
| `interact` | CamoFox for interactive pages, forms, SPAs |

Deprecated: `duckduckgo_search`, `web_crawl`, `camofox`, `web_scrapling`, `browser_tool`

**Consolidation 2 — Memory (5 → 1)**

New: `src/tools/memory-unified.ts` — `memory(action: "search" | "get" | "store", ...)`

| Action | Internal dispatch |
|--------|-----------------|
| `search` | fastembed cosine search over `facts` + `pellets` |
| `get` | direct lookup by id or key |
| `store` | writes to `facts` table via `UserMemoryStore` |

Deprecated: `memory_search`, `memory_get`, `recall_memory`, `remember`, `pellet_recall`

**Consolidation 3 — macOS Native (15 → 4)**

Four themed tools, each declaring `platforms: ["darwin"]`. Non-macOS calls return a structured error: `{ success: false, error: { code: "PLATFORM_UNSUPPORTED", message: "This action requires macOS." } }`.

| New tool | Actions | Replaces |
|----------|---------|---------|
| `comms` | `mail_read\|mail_send\|mail_search\|imessage\|airdrop` | Mail, iMessage, AirDrop |
| `organize` | `calendar\|reminders\|notes\|contacts` | Calendar, Reminders, Notes, Contacts |
| `system` | `focus\|notification\|spotlight\|process\|volume\|brightness\|info` | FocusMode, Notification, Spotlight, ProcessManager, SystemControls, SystemInfo |
| `media` | `tts\|music\|clipboard\|clipboard_history` | TTS, Music, Clipboard |

**Platform declaration pattern** (added to `src/providers/base.ts`):
```typescript
interface ToolDefinition {
  // ... existing fields ...
  platforms?: NodeJS.Platform[]   // omit = all platforms
  deprecated?: boolean            // omit = false
}
```

`ToolRegistry.execute()` checks `process.platform` against `tool.definition.platforms` before dispatch. `getDefinitions()` filters out `deprecated: true` tools.

**Result after cleanup:** ~65 tools → ~48 LLM-visible tools. ~3KB context budget reclaimed per turn.

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/web-unified.ts` |
| Create | `src/tools/memory-unified.ts` |
| Create | `src/tools/native/comms.ts` |
| Create | `src/tools/native/organize.ts` |
| Create | `src/tools/native/system.ts` |
| Create | `src/tools/native/media.ts` |
| Modify | `src/providers/base.ts` |
| Modify | `src/tools/registry.ts` |
| Modify | `src/index.ts` |

---

## 6. Phase 7d — Live Browser Control (LBC)

### Problem
Controlling the user's actual Safari or Chrome is "horrible" (user's words). Today: Chrome only works if pre-launched with `--remote-debugging-port=9222`; otherwise `browser_launch` opens a separate Puppeteer window — not the user's real session. Safari has no driver. macOS-specific JXA/AppleScript approaches are dead ends — StackOwl targets Windows, macOS, and Linux.

### Design

**Driver: Playwright over CDP.** Playwright (replacing existing Puppeteer BrowserBridge in `computer-use/`) supports Chromium-based browsers on all platforms via CDP, and Firefox via its own CDP-compatible protocol. Same tool, same actions, same behaviour on Windows 11, Ubuntu 24, macOS 15.

**`live_browser` tool** — `src/tools/live-browser/index.ts`

```typescript
live_browser(
  action: "tabs" | "active_url" | "active_text" | "navigate" |
          "click" | "fill" | "screenshot" | "switch_tab" |
          "new_tab" | "close_tab" | "scroll" | "back" | "forward" | "eval",
  // action-specific params:
  url?: string          // for navigate
  text?: string         // for click (match by visible text)
  selector?: string     // for click/fill (CSS selector)
  value?: string        // for fill
  index?: number        // for switch_tab
  script?: string       // for eval
  direction?: "up"|"down"  // for scroll
  amount?: number       // for scroll
)
```

`platforms: ["win32", "linux", "darwin"]` — all platforms.

**Three sub-modules:**

`src/tools/live-browser/chrome-driver.ts`
- `connect()`: try `playwright.chromium.connectOverCDP("http://localhost:9222")`
- If fails → `bootstrap()` → relaunch Chrome with debug flag → reconnect
- `list_tabs()`: `Target.getTargets` via CDP
- `switch_tab(index)`: `Target.activateTarget`
- All actions via Playwright Page API

`src/tools/live-browser/firefox-driver.ts`
- `connect()`: `playwright.firefox.launch({ headless: false })` + attach to existing if possible
- Same action surface as chrome-driver
- Secondary path — used when Chrome unavailable

`src/tools/live-browser/bootstrap.ts`
- Cross-platform Chrome relaunch with `--remote-debugging-port=9222`:
  ```
  Windows: Start-Process chrome --ArgumentList "--remote-debugging-port=9222 --restore-last-session"
  macOS:   open -a "Google Chrome" --args --remote-debugging-port=9222 --restore-last-session
  Linux:   google-chrome --remote-debugging-port=9222 --restore-last-session &
  ```
- Detects Chrome executable via OS-appropriate path lookup (registry on Windows, `which` on Linux/macOS)
- Fires narration: *"I need to relaunch Chrome with debug mode to control your tabs — your session will be restored. OK?"* → waits for HitlChannel approval (one-time per session)
- After approval: stores `debugPortApproved: true` in session — never prompts again in same session

`src/tools/live-browser/frontmost.ts`
- Detects active browser cross-platform:
  - Windows: `Get-Process | Where MainWindowTitle` via PowerShell
  - Linux: `xdotool getactivewindow getwindowname`
  - macOS: `osascript -e 'tell application "System Events" to get name of first process whose frontmost is true'`
- Routes to chrome-driver or firefox-driver based on result

**Per-tab screenshot:** `action:"screenshot"` captures the active tab via Playwright `page.screenshot()` — not the full screen. Returns base64 PNG path written to `workspace/screenshots/`.

**Playwright migration:** Existing `src/tools/computer-use/browser/cdp.ts` (Puppeteer BrowserBridge) is kept but marked internal-only. New code uses Playwright exclusively. The `computer_use browser_*` actions in `src/tools/computer-use/index.ts` are marked `deprecated: true` in their description — still callable, but LLM is directed to `live_browser`.

**Permissions:** Tool definition describes clearly that this controls the user's real browser. A `browser.trustedDomains: string[]` config allows restricting which URLs the owl can interact with (default: all). Actions on domains not in the list prompt via HitlChannel.

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/live-browser/index.ts` |
| Create | `src/tools/live-browser/chrome-driver.ts` |
| Create | `src/tools/live-browser/firefox-driver.ts` |
| Create | `src/tools/live-browser/bootstrap.ts` |
| Create | `src/tools/live-browser/frontmost.ts` |
| Modify | `src/tools/computer-use/index.ts` (deprecate browser_* actions) |
| Modify | `src/tools/screenshot.ts` (add active_tab mode) |
| Modify | `src/index.ts` (register live_browser) |

---

## 7. Phase 7d — MCP Full Lifecycle + Marketplace

### Problem
`MCPManager` auto-registers tools correctly but changes evaporate on restart. Telegram `/mcp` only supports `connect <npm-pkg>`, `reconnect`, `disconnect`, `status` — no `add/edit/remove`. CLI has zero `/mcp` command. Env vars (API keys) stored as plaintext in `stackowl.config.json`. No way to discover available MCP servers without external research.

### Design

**McpCommandRouter** (`src/gateway/commands/mcp-router.ts`)

Channel-agnostic dispatcher used by both Telegram and CLI. Both channels call `router.dispatch(verb, args, ctx)` and render the returned string. One implementation, two surfaces.

```typescript
type McpVerb = "list" | "add" | "install" | "edit" | "remove" |
               "enable" | "disable" | "reconnect" | "status" |
               "tools" | "logs" | "marketplace"

class McpCommandRouter {
  async dispatch(verb: McpVerb, args: string[], ctx: GatewayContext): Promise<string>
}
```

**Persistence write-back** (`src/tools/mcp/manager.ts`):

New methods added to `MCPManager`:
```typescript
async addServer(config: McpServerConfig): Promise<{ connected: boolean; tools: string[] }>
async removeServer(name: string): Promise<void>
async updateServer(name: string, patch: Partial<McpServerConfig>): Promise<void>
async setEnabled(name: string, enabled: boolean): Promise<void>
```

Each method mutates in-memory state, calls existing `connect()`/`disconnect()`, then calls `saveConfig()`. Config schema extended:

```typescript
interface McpServerConfig {
  name: string
  transport: "stdio" | "sse"
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>     // references like "$SECRET:GITHUB_TOKEN"
  enabled: boolean                 // default true
  description?: string
  installedAt?: string
}
```

**Secret handling.** `addServer` accepts `--secret KEY=value`. Value stored in `Credentials` vault (`src/tools/credentials.ts`). Config stores reference `"$SECRET:KEY"`. At connect time, `MCPClient` resolves references before spawning the process. Plaintext secrets in config are flagged with a warning on startup.

**Command surface** (identical in Telegram and CLI):

| Command | Description |
|---------|-------------|
| `/mcp` | Show all servers: name, status, tool count |
| `/mcp add <name> stdio "<cmd> [args]"` | Add stdio server, connect, persist |
| `/mcp add <name> sse <url>` | Add SSE server, connect, persist |
| `/mcp install <npm-package>` | Shortcut: npx install, auto-name, persist |
| `/mcp remove <name>` | Disconnect, delete from config |
| `/mcp edit <name>` | Telegram: paste JSON block; CLI: `$EDITOR` |
| `/mcp enable/disable <name>` | Toggle without removing |
| `/mcp tools <name>` | List tools this server exposes |
| `/mcp status [name]` | Connection health, last error |
| `/mcp logs <name>` | Tail recent stderr output |
| `/mcp marketplace` | Browse + install curated servers |
| `/mcp marketplace search <query>` | Filter catalog |
| `/mcp marketplace install <id>` | Install by catalog ID |

**MCP Marketplace catalog** (`src/gateway/commands/mcp-catalog.json`):

Static JSON array, ~40 entries, shipped with the package. Example entry:
```json
{
  "id": "github",
  "name": "GitHub MCP Server",
  "description": "Manage repos, PRs, issues, search code",
  "package": "@modelcontextprotocol/server-github",
  "transport": "stdio",
  "requiredSecrets": ["GITHUB_TOKEN"],
  "tags": ["code", "git", "productivity"]
}
```

`/mcp marketplace install github` → prompts for `GITHUB_TOKEN` via narration → stores in Credentials vault → runs `addServer()` → persists. One-command setup.

Remote manifest override: `marketplace.catalogUrl` in `stackowl.config.json` — if set, fetches and merges remote catalog on startup (cached 24h). Works offline if fetch fails (falls back to bundled catalog).

**MCP tool lifecycle fix:** MCP tool executions now flow through `ToolRegistry.execute()` giving them the same lifecycle as builtins: schema validation, tracker recording, GAV verification, narration events, structured error envelopes.

**Background health monitor:** Every 60s, ping each connected MCP server. On failure, set status `error`, attempt reconnect with exponential backoff (1s, 5s, 30s). After 3 failed reconnects, surface via HitlChannel: *"MCP server 'github' has been unreachable for 90s. Reconnect now?"*

### Files
| Action | Path |
|--------|------|
| Create | `src/gateway/commands/mcp-router.ts` |
| Create | `src/gateway/commands/mcp-catalog.json` |
| Modify | `src/tools/mcp/manager.ts` |
| Modify | `src/config/loader.ts` (schema extension) |
| Modify | `src/gateway/adapters/telegram.ts` |
| Modify | `src/cli/commands.ts` |

---

## 8. Phase 7d — Tool Quality Pass + Scaffolder

### Tool Quality Framework

Every tool (existing and new) must satisfy this checklist. Applied as a sweep over the top 30 most-used tools (by `selectionCount` in `tools-stats.json`).

**1. Platform declaration**
```typescript
platforms?: NodeJS.Platform[]   // omit = all platforms
```

**2. ExecutionPolicy** (new field on `ToolImplementation`)
```typescript
interface ExecutionPolicy {
  timeoutMs: number        // enforced via AbortController in registry.execute()
  maxRetries: number       // 0 = no retry
  retryDelayMs: number
  fallbackChain?: string[] // tool names tried in order on failure
}

// Category defaults applied when tool omits executionPolicy:
const CATEGORY_DEFAULTS: Record<ToolCategory, ExecutionPolicy> = {
  filesystem: { timeoutMs: 5_000,  maxRetries: 1, retryDelayMs: 500 },
  shell:      { timeoutMs: 30_000, maxRetries: 0, retryDelayMs: 0   },
  network:    { timeoutMs: 30_000, maxRetries: 2, retryDelayMs: 1000 },
  cognitive:  { timeoutMs: 60_000, maxRetries: 1, retryDelayMs: 2000 },
  system:     { timeoutMs: 10_000, maxRetries: 1, retryDelayMs: 500  },
  mcp:        { timeoutMs: 30_000, maxRetries: 2, retryDelayMs: 1000 },
}
```

**3. Structured error envelope**
Tools must never `throw` raw errors. On failure, return:
```
ERROR[code]: message
SUGGESTION: what to try next
```
`ToolRegistry.execute()` catches raw throws and wraps them in this format. Tools that already return structured errors bypass the wrapper.

**4. Capability tags** (new field on `ToolImplementation`)
```typescript
capabilities?: string[]   // ["web_search", "file_read", "code_exec"]
```
Used by CWTG (Phase 7b) and PTR for routing. Tools without capabilities are unroutable by CWTG — they still work, they just can't participate in intelligent fallback.

**5. Description with example**
Every tool description must include one concrete usage example in its `description` string. Format: `"... Example: web({action:'search', query:'TypeScript 5.5 release notes'})"`.

**Tool Scaffolder** (`scripts/create-tool.ts`)

```bash
npm run tool:create <name> <category> [--workspace]
```

Generates:
- `src/tools/<name>.ts` (or `workspace/tools/<name>.js` with `--workspace`) — full template with ExecutionPolicy, platforms, capabilities, structured-error helper, example in description
- `__tests__/tools/<name>.test.ts` — three test stubs: success path, error path, timeout path
- Appends registration line to `src/index.ts` (idempotent — skips if already registered)

`package.json` addition: `"tool:create": "tsx scripts/create-tool.ts"`

### Files
| Action | Path |
|--------|------|
| Modify | `src/providers/base.ts` (ExecutionPolicy, capabilities, platforms) |
| Modify | `src/tools/registry.ts` (timeout enforcement, retry loop, structured error wrap) |
| Modify | `src/tools/categories.ts` (CATEGORY_DEFAULTS) |
| Create | `scripts/create-tool.ts` |
| Modify | `package.json` |
| Modify | 30 individual tool files (quality checklist sweep) |

---

## 9. Phase 7d — Five New Tools

### 9.1 `vision` — Image Understanding

```typescript
vision(
  source: string | string[],   // file path, URL, or array for batch
  action: "describe" | "extract_text" | "extract_data" | "detect" | "compare",
  question?: string,           // for describe/detect
  schema?: Record<string, string>  // for extract_data: { field: "description" }
): string
```

Routes via `IntelligenceRouter.resolve("high")` to a vision-capable provider. Fallback: if no vision provider configured, `extract_text` falls back to OCR path. Batch: `source[]` → `Promise.allSettled()`. Result cache: SHA256 hash of `source+action+question` → SQLite `vision_cache` table, TTL 24h. Deprecates standalone `OCR` tool (kept registered, hidden from LLM).

`platforms: ["win32", "linux", "darwin"]`

### 9.2 `document` — Unified Document Parser

```typescript
document(
  source: string,              // file path or URL
  action: "text" | "tables" | "metadata" | "chunks" | "images",
  page_range?: [number, number],
  chunk_size?: number          // tokens per chunk for action:"chunks"
): string | object
```

Format support: PDF (`pdf-parse`), DOCX/PPTX (`mammoth`, `officegen`), XLSX (`xlsx`), EPUB (`epub`), HTML (`cheerio`), Markdown, CSV, JSON, plain text. Tables returned as JSON arrays. Images extracted and routed inline to `vision`. Page number annotations in output. Deprecates standalone `PDF` tool.

`platforms: ["win32", "linux", "darwin"]`

### 9.3 `sandbox` — Full Isolated Code Execution

```typescript
sandbox(
  runtime: "python" | "node" | "bash",
  code: string,
  packages?: string[],         // pip install / npm install before execution
  files?: Record<string, string>,  // filename → content, injected into workspace
  timeout_ms?: number,         // default 60_000
  network?: boolean,           // default false
  persist?: boolean            // keep workspace across calls in this session
): { stdout: string; stderr: string; return_value?: unknown; files_written: string[]; duration_ms: number; tier: "docker" | "process" }
```

**Tier 1 — Docker** (when `docker` is available on PATH): proper container isolation, no host filesystem access, `--network none` by default, CPU+memory limits via Docker flags. pip/npm install inside container, no host mutation.

**Tier 2 — Process** (fallback): `child_process.spawn` + `ulimit` (Linux/macOS) or Job Objects (Windows). pip/npm install runs on host in a tmp virtualenv/node_modules. AbortController timeout enforced.

Package install: session-level cache keyed by `(runtime, sorted packages)`. Install once per session, reuse on subsequent calls. Docker: layer cached per package set.

Auto-detect tier at startup: `execSync("docker info")` — if exit 0, Tier 1 available.

`platforms: ["win32", "linux", "darwin"]`

### 9.4 `db_query` — Multi-Database Client

```typescript
db_query(
  connection: string,          // named connection from Credentials vault
  action: "query" | "schema" | "list_tables" | "describe_table" | "test",
  query?: string,              // SQL or MongoDB query JSON string
  params?: unknown[],          // parameterized — injection impossible
  read_only?: boolean,         // default true
  limit?: number               // default 100, max 10_000
): string
```

Supported databases: SQLite (`better-sqlite3` — already in project), PostgreSQL (`pg`), MySQL (`mysql2`), MongoDB (`mongodb`), Redis (`ioredis`).

Connection management: `/db` CLI/Telegram command (same pattern as `/mcp`):
```
/db add <name> <type> <connection-string>
/db list
/db remove <name>
/db test <name>
```

Connection strings stored in Credentials vault. Config holds reference `"$SECRET:DB_MY_POSTGRES"`.

Safety non-negotiables:
- Parameterized queries only — the API does not accept raw string interpolation
- `read_only: true` default — write requires explicit `read_only: false` + narration confirmation
- Row cap: 100 default, 10,000 maximum — never unbounded result sets
- Schema introspection before query: `action:"schema"` recommended as first call

`platforms: ["win32", "linux", "darwin"]`

### 9.5 `schedule` — Natural Language Scheduling

```typescript
schedule(
  action: "once" | "repeat" | "list" | "cancel",
  when?: string,               // "in 2 hours", "every Monday at 9am", "2026-05-10T14:00"
  message?: string,
  channel?: "telegram" | "cli" | "all"  // default: channel where scheduled
): string
```

Natural language parsing via `chrono-node` (cross-platform, no native dependencies). Wraps existing `Cron` tool and `Heartbeat` proactive system. `action:"list"` shows active schedules with cancel IDs. `action:"cancel"` by ID or fuzzy name match.

`platforms: ["win32", "linux", "darwin"]`

---

## 10. Phase 7b — Cost-Weighted Tool Graph (CWTG)

### Problem
When GAV returns `BLOCKED`, Phase 7a uses the static `TOOL_FALLBACKS` map from `fallback-sequencer.ts`. That map never learns and evaporates on restart. If `web_crawl` fails 500 times and `web_interact` succeeds 450 of those, the system has no memory of this pattern after a restart.

### Design

**Tool graph persisted in SQLite.** Nodes are tool names. Directed edges represent "when tool A fails on capability X, tool B is a good alternative." Edge weights encode historical success rate and speed. Dijkstra finds the optimal recovery path in sub-50ms without any LLM call.

**Schema v18** (`src/memory/db.ts`):
```sql
CREATE TABLE tool_edges (
  from_tool      TEXT NOT NULL,
  to_tool        TEXT NOT NULL,
  capability_tag TEXT NOT NULL,
  success_rate   REAL DEFAULT 0.5,
  avg_duration_ms INTEGER DEFAULT 5000,
  sample_count   INTEGER DEFAULT 0,
  updated_at     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (from_tool, to_tool, capability_tag)
);
CREATE INDEX idx_tool_edges_cap ON tool_edges(capability_tag, from_tool);
```

**ToolGraph class** (`src/tools/cortex/tool-graph.ts`):

```typescript
class ToolGraph {
  // Called after every tool execution — updates edge from previous tool
  async recordExecution(toolName: string, capabilityTag: string,
                        success: boolean, durationMs: number): Promise<void>

  // Called by registry when GAV returns BLOCKED
  // Returns next best tool for this capability, or null if no path
  async replan(failedTool: string, capabilityTag: string): Promise<string | null>

  // Dijkstra weight: lower is better
  // weight = (1 - success_rate) * avg_duration_ms
  private dijkstra(start: string, capabilityTag: string): string | null
}
```

**Cold start:** On first boot, seeded from existing `TOOL_FALLBACKS` map in `fallback-sequencer.ts`. Each static fallback entry becomes an edge with `success_rate: 0.5, sample_count: 0` as a prior. Graph is never empty.

**FallbackSequencer replacement:** `src/tools/fallback-sequencer.ts` reads from `tool_edges` table instead of in-memory map. Existing interface preserved — callers unchanged. Phase 7a's `triggerFallback()` calls into `FallbackSequencer` which now delegates to `ToolGraph.replan()`.

**ToolTracker migration:** `src/tools/tracker.ts` migrates from `workspace/tools-stats.json` to a new `tool_executions` SQLite table. Error reasons (currently discarded) are now captured:

```sql
CREATE TABLE tool_executions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_name   TEXT NOT NULL,
  session_id  TEXT,
  success     INTEGER NOT NULL,
  duration_ms INTEGER,
  error_code  TEXT,
  error_msg   TEXT,
  created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX idx_tool_exec_name ON tool_executions(tool_name);
```

**`/tools graph` debug command:** Renders current graph as a Mermaid diagram for debugging. Shows top 10 capability tags by edge count.

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/cortex/tool-graph.ts` |
| Modify | `src/memory/db.ts` (schema v18: tool_edges + tool_executions) |
| Modify | `src/tools/fallback-sequencer.ts` (DB-backed) |
| Modify | `src/tools/tracker.ts` (JSON → SQLite migration) |

---

## 11. Phase 7b — Personalized Tool Routing (PTR)

### Problem
Tool selection today is pure LLM inference over tool descriptions. The owl has no memory of what worked last time a similar request came in. After 3 months of use, a user who asks "summarize my emails" every morning should have the owl pick the right tool sequence immediately — not rediscover it every time.

### Design

**PersonalizedRouter class** (`src/tools/cortex/personalized-router.ts`):

At the PLAN phase of `OwlOrchestrator`, before the LLM generates a tool sequence:
1. Embed the user's current message using `fastembed` (already in `UserMemoryStore`)
2. K-NN search over embedded `userMessage` from successful `trajectory_turns` (last 30 days, `verification_result = "ADVANCES"` or NULL)
3. For top-3 matches, extract tool sequences from their `trajectory_turns` ordered by `turn_index`
4. Inject as `ToolPriorLayer` in ContextPipeline at priority 8

```typescript
class PersonalizedRouter {
  async buildPrior(userMessage: string, userId: string): Promise<ToolPrior | null>
}

interface ToolPrior {
  sequences: Array<{
    toolSequence: string[]    // ["web", "memory"]
    similarity: number        // cosine similarity 0-1
    outcomeLabel: string      // "web search + fact storage"
  }>
}
```

**ToolPriorLayer** injected into ContextPipeline prompt:
```
Based on similar past requests, you've solved this type of task with:
1. web(action:'search') → web(action:'fetch') → memory(action:'store')
2. sandbox(runtime:'python') → document(action:'text')
Consider this as a starting point, not a constraint.
```

**Cold-start handling:** If `< 50` verified successes exist for this user, `buildPrior()` returns `null` — no prior injected, LLM picks freely. No errors, no placeholders.

**Staleness filter:** Prior sequences that reference deprecated tools are filtered before injection. Dead tool names are stripped silently from the sequence. If the entire sequence references only deprecated tools, that sequence is excluded.

**TTL:** Prior is computed fresh each turn (embedding computation is fast, <50ms with fastembed cached model). No caching of the prior itself — user patterns change.

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/cortex/personalized-router.ts` |
| Modify | `src/engine/orchestrator.ts` (call PersonalizedRouter at PLAN phase) |
| Modify | `src/context/layers/` (add ToolPriorLayer) |

---

## 12. Phase 7c — Self-Evolving Tools (SET) — Workspace Model

### Core Decision
SET **never** modifies system tools in `src/tools/`. Evolved tools land in `workspace/tools/` as JavaScript files. The system source directory is read-only from the runtime's perspective. This eliminates regression risk to core functionality and gives users full ownership of evolved tools.

### Workspace Tool Routing

Three-state lifecycle per tool:

```
State 1 — SHADOW (success_count < 40)
  Both versions execute in parallel.
  System result returned to LLM.
  Workspace result compared and score tracked.

State 2 — PROMOTED (success_count >= 40)
  Workspace tool executes only.
  System version bypassed entirely.

State 3 — ABSENT (no workspace version)
  System tool executes (normal path).
```

**`workspace_tools` table** (schema v17 addition):
```sql
CREATE TABLE workspace_tools (
  name          TEXT PRIMARY KEY,
  source_path   TEXT NOT NULL,       -- workspace/tools/<name>.js
  parent_tool   TEXT,                -- system tool it evolved from (NULL if new)
  success_count INTEGER DEFAULT 0,
  failure_count INTEGER DEFAULT 0,
  promoted_at   TEXT,
  created_by    TEXT NOT NULL,       -- "SET" | "patch_tool" | "user"
  created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
```

Workspace tools are JavaScript (not TypeScript) — no compilation step, immediate hot-reload on file change. `ToolRegistry.loadWorkspaceTools(workspacePath)` scans `workspace/tools/*.js` at startup and on `fs.watch` event.

### SET Weekly Job

`ImprovementScheduler` gains `runToolEvolution()`:

```
1. Query tool_executions: tool with lowest success_rate, >= 20 executions, last 7 days
   Skip if tool is on critical exclusion list (see below)

2. Pull last 50 failure samples from tool_executions:
   { error_code, error_msg, args_snapshot } — from trajectory_turns join

3. Dispatch PatchTool with prompt:
   "Current source: <src>. These 50 calls failed: <samples>.
    Rewrite the implementation to handle these cases.
    Do not change the tool name, parameters, or return type."

4. PatchTool writes result to workspace/tools/<name>.js

5. Shadow execution begins automatically (workspace_tools entry created, state: SHADOW)

6. After 24h: compare workspace success_rate vs system success_rate
   workspace >= system + 5pp → auto-promote (promoted_at set)
   workspace <  system - 5pp → discard (delete workspace file + table row)
   within 5pp              → surface via HitlChannel:
     "I rewrote <tool>. Results are comparable. Want to switch? [Yes/No/View diff]"
```

**Critical tool exclusion list** (never selected by SET):
```typescript
const SET_EXCLUDED = new Set([
  "remember", "recall", "memory",       // user data persistence
  "write_file", "edit_file",            // filesystem writes
  "shell", "sandbox",                   // code execution
  "db_query",                           // database writes
  "patch_tool",                         // the tool that does the rewriting
  // any tool with capabilities including "data_write" or "system_exec"
])
```

**Hard limits:**
- Maximum 1 active rewrite at a time
- Maximum 1 rewrite per week per tool
- Rewrite only changes implementation — never the JSON schema (interface frozen)

### User Commands

```
/tools workspace          — list workspace tools with scores and state
/tools promote <name>     — force promote (skip 40-success threshold)
/tools demote <name>      — return to SHADOW state (reset score to 0)
/tools reset <name>       — delete workspace version, restore system tool
/tools evolution status   — current SET candidate, shadow metrics, time remaining
```

### User-Authored Tools
Files dropped manually into `workspace/tools/<name>.js` are auto-detected and registered at `success_count: 0` (SHADOW state). They start accumulating their own score independently. `npm run tool:create <name> <category> --workspace` generates the template directly into the workspace directory.

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/cortex/self-evolver.ts` |
| Modify | `src/engine/improvement-scheduler.ts` |
| Modify | `src/tools/registry.ts` (workspace routing, loadWorkspaceTools) |
| Modify | `src/memory/db.ts` (workspace_tools table) |

---

## 13. Phase 7c — Fact Provenance Chain (FPC)

### Problem
Multi-step tasks can cascade bad data. A tool returns wrong information at step 2. The LLM treats it as fact. Steps 3, 4, and 5 build on it. By the time the user sees an answer, four tool calls have compounded a single bad fact into a confidently-wrong response — the "phantom SKU" failure pattern documented in 2026 production analysis.

### Design

Every tool result becomes a `FactEnvelope` tagged with its origin. When GAV marks a result `BLOCKED`, the envelope is retracted — stripped from future context turns and flagged so downstream tools cannot build on it.

**FactEnvelope type** (`src/tools/cortex/fact-envelope.ts`):

```typescript
interface FactEnvelope {
  id: string                      // uuid
  content: string                 // the actual tool output
  provenance: {
    toolName: string
    args: Record<string, unknown>
    durationMs: number
    verifiedBy?: "ADVANCES" | "PARTIAL"   // from GAV
    confidence: number            // 0-1; ADVANCES=0.9, PARTIAL=0.6, NEUTRAL=0.5
  }
  retracted: boolean
  sessionId: string
  turnIndex: number
}
```

**Critical constraint:** Provenance metadata is never rendered into the LLM prompt. It lives in the working-memory store only. The LLM sees `fact.content` only. Adding provenance to the prompt would cost ~200 bytes × 20 facts = 4KB context tax per turn.

**Working-memory store:** In-memory `Map<string, FactEnvelope>` keyed by `factId`, scoped per session. Periodically flushed to SQLite for session replay (optional, Phase 7c+).

**Retraction cascade:**

```
GAV returns BLOCKED on turn 4, toolName: "web"
  → find FactEnvelope created in turn 2 by "web" (same session, earlier turn)
  → set retracted: true
  → emit EventBus: "fact:retracted" { factId, toolName, reason, turnIndex }

ContextPipeline on next build:
  → filter step: exclude retracted FactEnvelopes from context assembly
  → LLM next turn no longer has the bad fact

EventBus subscriber in registry:
  → any tool whose args_snapshot references a retracted factId gets flagged
  → on next execution attempt: narration fires "Re-verifying earlier result..."
```

**`fact:retracted` event** added to `src/gateway/event-bus.ts`:
```typescript
"fact:retracted" { factId: string; toolName: string; reason: string; turnIndex: number }
```

**ContextPipeline integration** (`src/context/pipeline.ts`): Add `ProvenanceFilter` step that runs before final assembly. Any signal block that was sourced from a retracted `FactEnvelope` is excluded. Provenance metadata injected into the pipeline as a side-channel (not into the rendered prompt).

### Files
| Action | Path |
|--------|------|
| Create | `src/tools/cortex/fact-envelope.ts` |
| Modify | `src/gateway/event-bus.ts` (fact:retracted event) |
| Modify | `src/context/pipeline.ts` (ProvenanceFilter step) |
| Modify | `src/tools/registry.ts` (wrap results in FactEnvelope after GAV) |

---

## 14. Schema Migrations Summary

| Version | Phase | Changes |
|---------|-------|---------|
| v17 | 7a + 7c | `trajectory_turns`: add `verification_result`, `verifier_reason`, `subgoal_id`. Add `workspace_tools` table. |
| v18 | 7b | Add `tool_edges` table + index. Add `tool_executions` table (replaces JSON tracker). |
| v19 | 7c (optional) | Add `vision_cache` table for vision result caching. |

All migrations are additive — no existing columns altered or dropped. Existing data unaffected.

---

## 15. Complete File Map

### Phase 7a
| Action | File |
|--------|------|
| Create | `src/gateway/narration-formatter.ts` |
| Create | `src/tools/goal-verifier.ts` |
| Create | `src/tools/web-unified.ts` |
| Create | `src/tools/memory-unified.ts` |
| Create | `src/tools/native/comms.ts` |
| Create | `src/tools/native/organize.ts` |
| Create | `src/tools/native/system.ts` |
| Create | `src/tools/native/media.ts` |
| Modify | `src/gateway/event-bus.ts` |
| Modify | `src/tools/registry.ts` |
| Modify | `src/providers/base.ts` |
| Modify | `src/engine/orchestrator.ts` |
| Modify | `src/memory/db.ts` (v17) |
| Modify | `src/gateway/adapters/cli.ts` |
| Modify | `src/gateway/adapters/telegram.ts` |
| Modify | `src/gateway/adapters/slack.ts` |
| Modify | `src/index.ts` |

### Phase 7d
| Action | File |
|--------|------|
| Create | `src/tools/live-browser/index.ts` |
| Create | `src/tools/live-browser/chrome-driver.ts` |
| Create | `src/tools/live-browser/firefox-driver.ts` |
| Create | `src/tools/live-browser/bootstrap.ts` |
| Create | `src/tools/live-browser/frontmost.ts` |
| Create | `src/gateway/commands/mcp-router.ts` |
| Create | `src/gateway/commands/mcp-catalog.json` |
| Create | `src/tools/new/vision.ts` |
| Create | `src/tools/new/document.ts` |
| Create | `src/tools/new/sandbox.ts` |
| Create | `src/tools/new/db-query.ts` |
| Create | `src/tools/new/schedule.ts` |
| Create | `src/gateway/commands/db-router.ts` |
| Create | `scripts/create-tool.ts` |
| Modify | `src/tools/mcp/manager.ts` |
| Modify | `src/config/loader.ts` |
| Modify | `src/tools/computer-use/index.ts` |
| Modify | `src/tools/screenshot.ts` |
| Modify | `src/tools/categories.ts` |
| Modify | `src/cli/commands.ts` |
| Modify | `package.json` |

### Phase 7b
| Action | File |
|--------|------|
| Create | `src/tools/cortex/tool-graph.ts` |
| Create | `src/tools/cortex/personalized-router.ts` |
| Create | `src/context/layers/tool-prior-layer.ts` |
| Modify | `src/memory/db.ts` (v18) |
| Modify | `src/tools/fallback-sequencer.ts` |
| Modify | `src/tools/tracker.ts` |
| Modify | `src/engine/orchestrator.ts` |

### Phase 7c
| Action | File |
|--------|------|
| Create | `src/tools/cortex/self-evolver.ts` |
| Create | `src/tools/cortex/fact-envelope.ts` |
| Modify | `src/engine/improvement-scheduler.ts` |
| Modify | `src/tools/registry.ts` (workspace routing) |
| Modify | `src/memory/db.ts` (workspace_tools) |
| Modify | `src/gateway/event-bus.ts` (fact:retracted) |
| Modify | `src/context/pipeline.ts` (ProvenanceFilter) |

---

## 16. Competitive Differentiation

| What competitors do | What this spec adds |
|--------------------|---------------------|
| Static fallback maps (LangChain, AutoGPT) | **CWTG**: learned, persistent, Dijkstra-optimal |
| LLM-only tool selection | **PTR**: few-shot from own history — no other OSS personal assistant does this |
| Manual tool improvement | **SET**: tools rewrite themselves from failure traces — workspace model prevents regression |
| No output verification | **GAV**: goal-anchored — verifier sees the sub-goal, not just the result |
| Cascading hallucinations | **FPC**: retroactive fact retraction before next turn |
| MCP as config-only | **MCP Marketplace**: one-command install + auto-register + persists |
| macOS-only browser control | **live_browser**: cross-platform Playwright CDP, all OS |

---

*Spec complete. Implementation plans for each phase to follow in `docs/superpowers/plans/`.*
