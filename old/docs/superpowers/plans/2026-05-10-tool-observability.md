# Tool Observability — Full Trace Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument all 111 tools with full structured traces — every tool invocation logs its inputs, internal decisions, intermediate steps, and output/error so the AI can query its own tool history.

**Architecture:** The registry wraps every tool's `execute()` in `withSpan("tool.exec")` and emits structured `toolCall`/`toolResult` records (Task 1). Each individual tool then adds `log.tool.debug()` calls at four named points inside its own `execute()`: entry, decisions, steps, and exit. All records carry `traceId`/`spanId` from AsyncLocalStorage — no signature changes anywhere.

**Tech Stack:** TypeScript, `src/infra/observability/context.ts` (`withSpan`), `src/logger.ts` (`log.tool.*`), `src/tools/registry.ts`, Vitest.

---

## Logging Pattern Reference (copy this into every tool)

```ts
import { log } from "../logger.js";  // adjust path depth per file

// inside execute():
log.tool.debug("<toolname>.execute: entry", { ...relevantArgs });
log.tool.debug("<toolname>.execute: <decision>", { chosen, reason });
log.tool.debug("<toolname>.execute: <step>", { ...stepContext });
log.tool.debug("<toolname>.execute: exit", { success: true, resultLen: result.length });
// on error inside execute():
log.tool.error("<toolname>.execute: <step> failed", err, { ...context });
```

Rule: `log.tool.debug` for normal flow; `log.tool.warn` for recoverable issues; `log.tool.error` for failures. All points inside the same `execute()` share the span context automatically.

---

## File Map

**Modified:**
- `src/tools/registry.ts` — add `withSpan` import + wrap `execute()` body
- `src/tools/shell.ts` — 4-point logging
- `src/tools/web.ts` — 4-point logging
- `src/tools/search.ts` — 4-point logging
- `src/tools/files.ts` — 4-point logging
- `src/tools/web-utils/rss-feed.ts`, `web-monitor.ts`, `youtube-search.ts`, `bookmark-manager.ts`, `link-preview.ts`, `web-reader.ts`, `web-scraper.ts` — 4-point logging
- `src/tools/macos/*.ts` (17 tools excluding index.ts) — 4-point logging each
- `src/tools/dev/git.ts`, `docker.ts`, `api-tester.ts`, `cron-job.ts`, `network-scan.ts` — 4-point logging
- `src/tools/code-sandbox.ts`, `src/tools/sandbox.ts` — fill gaps + 4-point logging
- `src/tools/cortex/*.ts` (6 tools excluding index.ts) — 4-point logging each
- `src/tools/computer-use/*.ts` (index excluded) — fill gaps + 4-point logging
- `src/tools/live-browser/*.ts` (index excluded) — fill gaps + 4-point logging
- `src/tools/data/*.ts` (8 tools) — fill gaps + 4-point logging
- `src/tools/creative/*.ts` (5 tools) — 4-point logging
- `src/tools/mcp/client.ts` — 4-point logging
- `src/tools/utils/*.ts` (14 tools excluding index.ts) — 4-point logging each
- `src/tools/read-logs.ts`, `src/tools/vision.ts`, `src/tools/document.ts`, `src/tools/db-query.ts`, `src/tools/intent-router.ts`, `src/tools/goal-verifier.ts`, `src/tools/invoke-skill.ts`, `src/tools/send_file.ts` — 4-point logging

**New:**
- `__tests__/observability/tool-registry-span.test.ts` — verify registry emits toolCall/toolResult with traceId

---

## Task 1: Registry — wrap execute() in withSpan + emit structured records

**Files:**
- Modify: `src/tools/registry.ts:1-20` (imports), `src/tools/registry.ts:296-494` (execute method)
- Create: `__tests__/observability/tool-registry-span.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// __tests__/observability/tool-registry-span.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { installTestSink, capturedLogs, clearTestSink } from "../../src/infra/observability/sinks/test-sink.js";
import { runWithContext } from "../../src/infra/observability/context.js";
import { randomTraceId } from "../../src/infra/observability/ids.js";

describe("ToolRegistry span instrumentation", () => {
  let registry: ToolRegistry;

  beforeEach(() => {
    installTestSink();
    registry = new ToolRegistry();
    registry.register({
      definition: {
        name: "echo",
        description: "test tool",
        parameters: { type: "object", properties: { msg: { type: "string" } }, required: ["msg"] },
      },
      execute: async (args) => `echo: ${args.msg}`,
    });
  });

  afterEach(() => clearTestSink());

  it("emits toolCall record with tool name and args", async () => {
    const traceId = randomTraceId();
    await runWithContext({ traceId }, () =>
      registry.execute("echo", { msg: "hello" }, {})
    );
    const calls = capturedLogs().filter(r => r.msg?.includes("tool.call"));
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[0].fields?.tool).toBe("echo");
    expect(calls[0].traceId).toBe(traceId);
  });

  it("emits toolResult record with success flag", async () => {
    await runWithContext({}, () =>
      registry.execute("echo", { msg: "hi" }, {})
    );
    const results = capturedLogs().filter(r => r.msg?.includes("tool.result"));
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].fields?.success).toBe(true);
  });

  it("emits error record when tool throws", async () => {
    registry.register({
      definition: { name: "boom", description: "fails", parameters: { type: "object", properties: {} } },
      execute: async () => { throw new Error("tool exploded"); },
    });
    await expect(
      runWithContext({}, () => registry.execute("boom", {}, {}))
    ).rejects.toThrow();
    const errors = capturedLogs().filter(r => r.level === "error");
    expect(errors.length).toBeGreaterThan(0);
    expect(errors[0].err?.message).toContain("tool exploded");
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/observability/tool-registry-span.test.ts 2>&1 | tail -20
```
Expected: FAIL — no toolCall/toolResult records emitted yet.

- [ ] **Step 3: Add withSpan import to registry.ts**

In `src/tools/registry.ts`, add to the import block (after the existing imports, around line 20):

```ts
import { withSpan } from "../infra/observability/context.js";
```

- [ ] **Step 4: Add toolCall/toolResult/error log calls in execute()**

In `src/tools/registry.ts`, locate the `execute()` method. Find the block starting at `const startTime = Date.now();` (around line 347). Replace from that line through the closing `}` of the try/catch (line ~493) with:

```ts
    const startTime = Date.now();
    this._eventBus?.emit({ type: "tool:start", toolName: name, args, turnId: context.engineContext?.sessionId ?? "" });

    // Sanitize args before logging — mask sensitive keys
    const sanitizedArgs: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(args)) {
      const lk = k.toLowerCase();
      sanitizedArgs[k] = (lk.includes("token") || lk.includes("key") || lk.includes("password") || lk.includes("secret") || lk.includes("apikey"))
        ? "[REDACTED]"
        : v;
    }

    return withSpan("tool.exec", async () => {
      log.tool.toolCall(name, sanitizedArgs);

      try {
        let result = await tool.execute(args, context);
        const durationMs = Date.now() - startTime;

        if (this._tracker) {
          this._tracker.recordSuccess(name, durationMs, {
            sessionId: context.engineContext?.sessionId,
          });
        }

        // Truncate long results to prevent context bloat
        let truncated = false;
        if (result.length > MAX_TOOL_RESULT_LENGTH) {
          result =
            result.slice(0, MAX_TOOL_RESULT_LENGTH) +
            `\n\n[OUTPUT TRUNCATED — ${result.length} chars total, showing first ${MAX_TOOL_RESULT_LENGTH}]`;
          truncated = true;
        }

        this._eventBus?.emit({ type: "tool:result", toolName: name, success: true, durationMs, truncated });
        log.tool.toolResult(name, result.slice(0, 500), true);

        // Envelope passthrough — emit <tool_attempt_summary> regardless of GAV
        try {
          const { parseWebToolResult, buildAttemptSummaryXml } = await import("../browser/envelope.js");
          const env = parseWebToolResult(result);
          if (env && !env.success && !result.includes("<tool_attempt_summary")) {
            result = result + "\n\n" + buildAttemptSummaryXml(env);
          }
        } catch { /* envelope parse is best-effort */ }

        // GAV: verify result against active sub-goal (skip if no sub-goal or no verifier)
        if (this._goalVerifier && context.engineContext?.activeSubGoal) {
          const subGoal = context.engineContext.activeSubGoal;
          const userMessage = context.engineContext.userMessage ?? "";
          try {
            const verification = await this._goalVerifier.verify({
              toolName: name,
              toolArgs: args,
              toolResult: result,
              subGoal,
              userMessage,
            });

            if (verification.verdict === "ADVANCES" || verification.verdict === "PARTIAL") {
              this._eventBus?.emit({
                type: "tool:goal_advance",
                toolName: name,
                subGoal: subGoal.description,
                verdict: verification.verdict,
              });
            }

            if (verification.verdict === "BLOCKED") {
              this._eventBus?.emit({
                type: "tool:goal_blocked",
                toolName: name,
                subGoal: subGoal.description,
                suggestion: verification.suggestion,
              });

              const capability = tool.definition.capabilities?.[0];
              if (this._toolGraph && capability && _replanDepth === 0) {
                const urlHost = (() => {
                  try {
                    return args.url ? new URL(args.url as string).hostname : "";
                  } catch { return ""; }
                })();
                const alt = this._toolGraph.getAlternative(name, capability, urlHost);
                if (alt) {
                  this._eventBus?.emit({ type: "tool:fallback", from: name, to: alt });
                  log.tool.info(`registry: replanning ${name} → ${alt}`, { capability, urlHost });
                  return this.execute(alt, args, context, 1, _verdictSink);
                }
              }
            }

            if (_verdictSink && verification.verdict) {
              _verdictSink.verdict = verification.verdict;
              _verdictSink.reason = verification.reason;
            }

            const { parseWebToolResult } = await import("../browser/envelope.js");
            const envelope = parseWebToolResult(result);
            if (!envelope && (verification.verdict === "BLOCKED" || verification.verdict === "PARTIAL")) {
              result = result + `\n\n<tool_result_warning verdict="${verification.verdict}">${verification.reason}${verification.suggestion ? ` Suggestion: ${verification.suggestion}` : ""}</tool_result_warning>`;
            }
          } catch (err) {
            log.tool.warn("registry: tool verifier failed (non-fatal)", err);
          }
        }

        return result;
      } catch (error) {
        const durationMs = Date.now() - startTime;
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorCode =
          error instanceof ToolExecutionError
            ? "EXEC_FAILED"
            : error instanceof Error
              ? error.constructor.name
              : "UNKNOWN";
        if (this._tracker) {
          this._tracker.recordFailure(name, durationMs, {
            errorCode,
            errorMessage,
            sessionId: context.engineContext?.sessionId,
          });
        }
        this._eventBus?.emit({ type: "tool:result", toolName: name, success: false, durationMs, truncated: false });
        log.tool.error("tool.exec failed", error, { tool: name, args: sanitizedArgs, durationMs });
        if (error instanceof ToolExecutionError) throw error;
        throw new ToolExecutionError(name, errorMessage);
      }
    }, { tool: name });
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
npx vitest run __tests__/observability/tool-registry-span.test.ts 2>&1 | tail -15
```
Expected: 3 tests PASS.

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
npm test 2>&1 | tail -10
```
Expected: same pass count as before (3014+), no new failures.

- [ ] **Step 7: Commit**

```bash
git add src/tools/registry.ts __tests__/observability/tool-registry-span.test.ts
git commit -m "obs(tools-B1): registry wraps execute() in withSpan; emit toolCall/toolResult/error"
```

---

## Task 2: Shell + Web + Search + Files + web-utils (B2)

**Files:** `src/tools/shell.ts`, `src/tools/web.ts`, `src/tools/search.ts`, `src/tools/files.ts`, `src/tools/web-utils/rss-feed.ts`, `web-utils/web-monitor.ts`, `web-utils/youtube-search.ts`, `web-utils/bookmark-manager.ts`, `web-utils/link-preview.ts`

For each file, apply the 4-point pattern. These are the highest-traffic tools — every log point matters.

- [ ] **Step 1: Instrument shell.ts**

Read `src/tools/shell.ts` execute() method. It spawns a Docker container to run commands. Add:

```ts
// At top of execute():
log.tool.debug("shell.execute: entry", {
  command: args.command,
  workdir: args.workdir,
  timeout: args.timeout ?? 30000,
  language: args.language,
});

// Before container spawn:
log.tool.debug("shell.execute: spawning container", { image, command: args.command });

// After container exits:
log.tool.debug("shell.execute: container exited", { exitCode, stdoutLen: stdout.length, stderrLen: stderr.length });

// On healing/retry decision:
log.tool.debug("shell.execute: healing attempt", { attempt, reason: healReason });

// At exit (before return):
log.tool.debug("shell.execute: exit", { success: exitCode === 0, exitCode, resultLen: result.length });
```

- [ ] **Step 2: Instrument web.ts**

Read `src/tools/web.ts`. It fetches URLs via a smart fetch layer. Add:

```ts
// entry
log.tool.debug("web.execute: entry", { url: args.url, format: args.format });

// decision — which fetch strategy
log.tool.debug("web.execute: fetch strategy selected", { strategy: "scrapling" | "camofox", url: args.url });

// step — response received
log.tool.debug("web.execute: response", { status: res.status, url: args.url, contentLen: body.length });

// exit
log.tool.debug("web.execute: exit", { success: true, resultLen: result.length });

// error
log.tool.error("web.execute: fetch failed", err, { url: args.url });
```

- [ ] **Step 3: Instrument search.ts**

Read `src/tools/search.ts`. It queries DDG HTML backend. Add:

```ts
log.tool.debug("search.execute: entry", { query: args.query, maxResults: args.maxResults });
log.tool.debug("search.execute: http request sent", { url: searchUrl });
log.tool.debug("search.execute: results parsed", { count: results.length });
log.tool.debug("search.execute: exit", { success: true, resultCount: results.length });
log.tool.error("search.execute: request failed", err, { query: args.query });
```

- [ ] **Step 4: Instrument files.ts**

Read `src/tools/files.ts`. It handles read/write/list operations. Add:

```ts
log.tool.debug("files.execute: entry", { operation: args.operation, path: args.path });
// decision — which operation branch
log.tool.debug("files.execute: operation", { op: args.operation, path: args.path });
// step — fs operation result
log.tool.debug("files.execute: fs result", { op: args.operation, success: true, bytesOrCount });
log.tool.debug("files.execute: exit", { op: args.operation, resultLen: result.length });
log.tool.error("files.execute: fs operation failed", err, { op: args.operation, path: args.path });
```

- [ ] **Step 5: Instrument web-utils/ tools**

For each file in `src/tools/web-utils/` (rss-feed.ts, web-monitor.ts, youtube-search.ts, bookmark-manager.ts, link-preview.ts): read the file, then add entry/decision/step/exit log calls using the pattern from Step 2 above, replacing "web" with the tool name (e.g. "rss-feed.execute: entry").

- [ ] **Step 6: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "shell|web\.ts|search|files|web-utils" | head -20
```
Expected: no errors from these files.

- [ ] **Step 7: Commit**

```bash
git add src/tools/shell.ts src/tools/web.ts src/tools/search.ts src/tools/files.ts src/tools/web-utils/
git commit -m "obs(tools-B2): 4-point logging in shell, web, search, files, web-utils"
```

---

## Task 3: macOS system tools (B3)

**Files:** All 17 non-index files in `src/tools/macos/`

`airdrop.ts`, `calendar.ts`, `clipboard.ts`, `comms-unified.ts`, `contacts.ts`, `focus-mode.ts`, `imessage.ts`, `mail.ts`, `music-control.ts`, `notes.ts`, `notification.ts`, `reminders.ts`, `spotlight.ts`, `system-controls.ts`, `system-info.ts`, `system-unified.ts`, `text-to-speech.ts`

- [ ] **Step 1: Add log import to all macos tools that lack it**

For each file, check if `import { log } from "../../logger.js"` (two levels up from `tools/macos/`) is present. If not, add it after the last import.

- [ ] **Step 2: Instrument each file with 4-point pattern**

For each tool, read its execute() method and add:
```ts
log.tool.debug("<toolname>.execute: entry", { ...keyArgs });
// decisions: e.g. calendar.execute: creating vs updating event
log.tool.debug("<toolname>.execute: <decision>", { chosen, reason });
// steps: e.g. appleScript spawned, API call made
log.tool.debug("<toolname>.execute: <step>", { ...stepContext });
log.tool.debug("<toolname>.execute: exit", { success: true, resultLen: result.length });
log.tool.error("<toolname>.execute: failed", err, { ...context });
```

Key decision points per tool:
- **calendar.ts**: creating vs updating event; all-day vs timed
- **mail.ts**: sending vs reading; account selection
- **spotlight.ts**: query type (files vs apps vs contacts)
- **imessage.ts**: sending to individual vs group
- **system-controls.ts**: which control was toggled (wifi, bluetooth, etc.)
- **focus-mode.ts**: enabling vs disabling; which mode
- **notification.ts**: alert vs banner type
- All others: primary operation + any fallback path

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "tools/macos" | head -20
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/tools/macos/
git commit -m "obs(tools-B3): 4-point logging in all 17 macOS system tools"
```

---

## Task 4: Dev tools — git, docker, api-tester, cron-job, network-scan + code-sandbox (B4)

**Files:** `src/tools/dev/git.ts`, `dev/docker.ts`, `dev/api-tester.ts`, `dev/cron-job.ts`, `dev/network-scan.ts`, `src/tools/code-sandbox.ts`, `src/tools/sandbox.ts`

These tools execute external processes — every decision point is critical for debugging.

- [ ] **Step 1: Instrument git.ts**

Read `src/tools/dev/git.ts`. Add:
```ts
log.tool.debug("git.execute: entry", { operation: args.operation, repo: args.repo, branch: args.branch });
log.tool.debug("git.execute: running command", { cmd: builtCmd, cwd: args.repo });
log.tool.debug("git.execute: command result", { exitCode, stdoutLen: stdout.length });
log.tool.debug("git.execute: exit", { operation: args.operation, success: true });
log.tool.error("git.execute: command failed", err, { cmd: builtCmd, exitCode });
```

- [ ] **Step 2: Instrument docker.ts**

Read `src/tools/dev/docker.ts`. Add:
```ts
log.tool.debug("docker.execute: entry", { operation: args.operation, image: args.image, container: args.container });
log.tool.debug("docker.execute: running command", { cmd: builtCmd });
log.tool.debug("docker.execute: command result", { exitCode, stdoutLen: stdout.length });
log.tool.debug("docker.execute: exit", { operation: args.operation, success: exitCode === 0 });
log.tool.error("docker.execute: command failed", err, { operation: args.operation });
```

- [ ] **Step 3: Instrument api-tester.ts, cron-job.ts, network-scan.ts**

For each: read the file, apply entry/step/exit/error pattern with tool-specific context fields.

- [ ] **Step 4: Fill gaps in code-sandbox.ts and sandbox.ts**

Read both files. They already have some logging — add any missing 4-point calls (especially exit log with `resultLen` and decisions about sandbox type/fallback).

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "tools/dev|code-sandbox|sandbox\.ts" | head -20
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/dev/ src/tools/code-sandbox.ts src/tools/sandbox.ts
git commit -m "obs(tools-B4): 4-point logging in dev tools, code-sandbox, sandbox"
```

---

## Task 5: Cortex tools — zero logging today (B5)

**Files:** `src/tools/cortex/edge-accumulator.ts`, `fact-envelope.ts`, `fact-retractor.ts`, `personalized-router.ts`, `self-evolver.ts`, `shadow-runner.ts`, `tool-graph.ts`

These have no logging at all. They reason about tool selection, graph traversal, and self-evolution — the most important decisions to trace.

- [ ] **Step 1: Add log import to all cortex tools**

Each file needs `import { log } from "../../logger.js";` (two levels up from `tools/cortex/`).

- [ ] **Step 2: Instrument each cortex tool**

For each file, read the execute() and add the 4-point pattern. Key decision points:

- **tool-graph.ts**: which tool alternative was selected and why; graph traversal path
- **personalized-router.ts**: which provider/model was chosen; routing rule matched
- **self-evolver.ts**: which self-improvement action was queued; what triggered it
- **shadow-runner.ts**: which shadow execution path was taken; comparison result
- **edge-accumulator.ts**: which edges were added/removed; threshold crossed
- **fact-envelope.ts**: fact accepted vs rejected; confidence threshold
- **fact-retractor.ts**: which facts were retracted; why (contradiction vs expiry)

Example for tool-graph.ts:
```ts
log.tool.debug("tool-graph.execute: entry", { capability: args.capability, excludeTools: args.exclude });
log.tool.debug("tool-graph.execute: traversing graph", { nodes: graph.size, seeking: args.capability });
log.tool.debug("tool-graph.execute: alternative found", { tool: alt, score, capability });
log.tool.debug("tool-graph.execute: exit", { found: !!alt, alternative: alt });
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "tools/cortex" | head -20
```

- [ ] **Step 4: Commit**

```bash
git add src/tools/cortex/
git commit -m "obs(tools-B5): 4-point logging in all 7 cortex tools (zero → full coverage)"
```

---

## Task 6: Computer-use + Live-browser (B6)

**Files:** `src/tools/computer-use/index.ts` (skip), `browser/cdp.ts`, `driver/linux.ts`, `driver/macos.ts`, `screen-reader.ts`, `src/tools/live-browser/bootstrap.ts`, `frontmost.ts`, `safari-driver.ts`, `chrome-driver.ts`

- [ ] **Step 1: Fill gaps in computer-use tools**

Read each file. Most already import log. Add missing entry/decision/step/exit points:

- **cdp.ts**: log each Chrome DevTools Protocol command sent + response
- **driver/linux.ts**: log each xdotool/xclip command + result
- **driver/macos.ts**: log each AppleScript execution + result  
- **screen-reader.ts**: log capture dimensions, OCR call, confidence score

Example for cdp.ts:
```ts
log.tool.debug("cdp.execute: entry", { action: args.action, selector: args.selector });
log.tool.debug("cdp.execute: sending cdp command", { method, params });
log.tool.debug("cdp.execute: cdp response", { result: JSON.stringify(res).slice(0, 200) });
log.tool.debug("cdp.execute: exit", { action: args.action, success: true });
```

- [ ] **Step 2: Fill gaps in live-browser tools**

For `bootstrap.ts`, `frontmost.ts`, `safari-driver.ts`, `chrome-driver.ts` — read each, add missing 4-point calls:
```ts
log.tool.debug("safari-driver.execute: entry", { url: args.url, action: args.action });
log.tool.debug("safari-driver.execute: applescript sent", { scriptLen: script.length });
log.tool.debug("safari-driver.execute: applescript result", { exitCode, output: output.slice(0, 200) });
log.tool.debug("safari-driver.execute: exit", { action: args.action, success: true });
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "computer-use|live-browser" | head -20
```

- [ ] **Step 4: Commit**

```bash
git add src/tools/computer-use/ src/tools/live-browser/
git commit -m "obs(tools-B6): 4-point logging in computer-use and live-browser tools"
```

---

## Task 7: Data + Creative + MCP (B7)

**Files:** `src/tools/data/` (8 files), `src/tools/creative/` (5 files), `src/tools/mcp/client.ts`

These are already partially instrumented — fill remaining gaps.

- [ ] **Step 1: Fill gaps in data tools**

For each of `pdf-reader.ts`, `spreadsheet.ts`, `ocr.ts`, `data-viz.ts`, `file-encrypt.ts`, `file-organize.ts`, `json-transform.ts` (check `ls src/tools/data/`): read file, add any missing points from the 4-point pattern. Focus on: what format/operation was chosen, what the parse/transform produced, empty result detection.

```ts
// Example for pdf-reader.ts — add where missing:
log.tool.debug("pdf-reader.execute: entry", { path: args.path, pages: args.pages });
log.tool.debug("pdf-reader.execute: parsed", { pageCount, textLen });
log.tool.debug("pdf-reader.execute: exit", { success: true, resultLen: result.length });
```

- [ ] **Step 2: Fill gaps in creative tools**

For each of `mermaid-diagram.ts`, `speech-to-text.ts`, `image-generation.ts`, `video-summarizer.ts`, `music-composer.ts` (check `ls src/tools/creative/`): add entry/decision/step/exit pattern.

```ts
// Example for image-generation.ts:
log.tool.debug("image-generation.execute: entry", { prompt: args.prompt?.slice(0, 100), size: args.size, model: args.model });
log.tool.debug("image-generation.execute: api request sent", { model: args.model });
log.tool.debug("image-generation.execute: api response", { imageCount: images.length });
log.tool.debug("image-generation.execute: exit", { success: true, imageCount: images.length });
```

- [ ] **Step 3: Instrument mcp/client.ts**

Read `src/tools/mcp/client.ts`. The MCP client proxies calls to external MCP servers — every hop should be traced:
```ts
log.tool.debug("mcp.execute: entry", { server: args.server, tool: args.tool, argsKeys: Object.keys(args.args ?? {}) });
log.tool.debug("mcp.execute: connecting to server", { server: args.server, transport });
log.tool.debug("mcp.execute: tool call sent", { tool: args.tool });
log.tool.debug("mcp.execute: response received", { resultLen: result.length });
log.tool.debug("mcp.execute: exit", { success: true, server: args.server, tool: args.tool });
log.tool.error("mcp.execute: server call failed", err, { server: args.server, tool: args.tool });
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "tools/data|tools/creative|mcp/client" | head -20
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/data/ src/tools/creative/ src/tools/mcp/
git commit -m "obs(tools-B7): fill 4-point logging gaps in data, creative, mcp tools"
```

---

## Task 8: Utils + remaining root-level tools (B8)

**Files:**
- `src/tools/utils/` (14 non-index files: calculator.ts, currency.ts, daily-briefing.ts, expense-tracker.ts, habit-tracker.ts, json-transform.ts, password.ts, process-manager.ts, qr-code.ts, timer.ts, translator.ts, unit-converter.ts, weather.ts, + any others)
- Root-level: `read-logs.ts`, `vision.ts`, `document.ts`, `db-query.ts`, `intent-router.ts`, `goal-verifier.ts`, `invoke-skill.ts`, `send_file.ts`, `camofox.ts`, `schedule.ts`

- [ ] **Step 1: Instrument utils/ tools**

For each utils tool, apply entry/exit/error (these are simpler — fewer internal decisions, but entry args + exit result are important):
```ts
// Example for translator.ts:
log.tool.debug("translator.execute: entry", { text: args.text?.slice(0, 100), from: args.from, to: args.to });
log.tool.debug("translator.execute: api call sent", { provider: "deepl", targetLang: args.to });
log.tool.debug("translator.execute: exit", { success: true, resultLen: result.length });
log.tool.error("translator.execute: translation failed", err, { from: args.from, to: args.to });
```

For process-manager.ts (executes system commands):
```ts
log.tool.debug("process-manager.execute: entry", { action: args.action, pid: args.pid, name: args.name });
log.tool.debug("process-manager.execute: system call", { cmd: builtCmd });
log.tool.debug("process-manager.execute: exit", { action: args.action, success: true });
```

- [ ] **Step 2: Instrument root-level remaining tools**

For each of `vision.ts`, `document.ts`, `db-query.ts`, `intent-router.ts`, `goal-verifier.ts`, `invoke-skill.ts`, `send_file.ts`, `camofox.ts`, `schedule.ts`: read the file, add any missing 4-point calls. Most of these already have partial coverage — focus on filling entry + exit gaps.

For `read-logs.ts` specifically (our new tool):
```ts
log.tool.debug("read-logs.execute: entry", { traceId: args.traceId, module: args.module, sinceMinutes: args.sinceMinutes, limit: args.limit });
log.tool.debug("read-logs.execute: reading logs", { logsDir, candidateFiles: files.length });
log.tool.debug("read-logs.execute: exit", { recordsReturned: records.length });
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "tools/utils|read-logs|vision|document|db-query|intent-router|goal-verifier|invoke-skill|send_file|camofox|schedule" | head -30
```

- [ ] **Step 4: Commit**

```bash
git add src/tools/utils/ src/tools/read-logs.ts src/tools/vision.ts src/tools/document.ts src/tools/db-query.ts src/tools/intent-router.ts src/tools/goal-verifier.ts src/tools/invoke-skill.ts src/tools/send_file.ts src/tools/camofox.ts src/tools/schedule.ts
git commit -m "obs(tools-B8): 4-point logging in utils and remaining root-level tools"
```

---

## Task 9: Final verification + merge

- [ ] **Step 1: Full TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "^src/tools" | head -30
```
Expected: only pre-existing errors (document.ts, memory-unified.ts, registry.ts type issues that predate this work). Zero new errors.

- [ ] **Step 2: Full test suite**

```bash
npm test 2>&1 | tail -15
```
Expected: all tests pass (plus the 3 new registry span tests). Same pre-existing ambient.test.ts failure is acceptable.

- [ ] **Step 3: Smoke test — verify a tool trace**

Start the app briefly or run:
```bash
node -e "
import('./dist/tools/registry.js').then(async ({ ToolRegistry }) => {
  const { installTestSink, capturedLogs, clearTestSink } = await import('./dist/infra/observability/sinks/test-sink.js');
  installTestSink();
  const reg = new ToolRegistry();
  console.log(capturedLogs().filter(r => r.fields?.tool).map(r => JSON.stringify({msg: r.msg, tool: r.fields.tool})));
});
"
```
Or send a message via CLI that triggers a tool, then:
```bash
cat ~/.stackowl/*/logs/stackowl-$(date +%F).log | jq 'select(.fields.tool) | {ts, msg, tool: .fields.tool, success: .fields.success, durationMs}' | head -20
```
Expected: see toolCall + toolResult records with matching traceId.

- [ ] **Step 4: Verify traceId links tool to request**

```bash
cat ~/.stackowl/*/logs/stackowl-$(date +%F).log | jq -s 'group_by(.traceId) | .[] | {traceId: .[0].traceId, modules: [.[].module] | unique, count: length}' | head -30
```
Expected: groups where `modules` contains both `"engine.runtime"` and `"tool"` — proving the tool trace is linked to the same request trace.

- [ ] **Step 5: Push to remote**

```bash
git push
```

---

## Verification Checklist

After all tasks complete:

- `grep -rn "log.tool.debug\|log.tool.error\|log.tool.warn" src/tools/ | wc -l` — should be 300+
- Every tool file (non-index) has at least one `log.tool.debug` call
- `read_logs({ module: "tool", contains: "shell.execute" })` returns entry + exit records for shell
- `read_logs({ traceId: "<id>", module: "tool" })` returns all tool records for a given request
- `read_logs({ errorOnly: true, module: "tool", sinceMinutes: 60 })` returns tool failures with `err.stack`
