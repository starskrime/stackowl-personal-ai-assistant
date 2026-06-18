# Tool Observability — Full Trace Instrumentation

**Date:** 2026-05-10  
**Status:** Approved  
**Scope:** All 111 tools in `src/tools/`

---

## Problem

111 tools across 16 categories handle shell execution, web fetches, macOS system calls, file I/O, AI reasoning (cortex), and more. When a tool fails or behaves unexpectedly, there is no way to answer:

- What input did it receive?
- What decision did it make and why?
- Which step failed?
- What did it return?
- How long did it take?

36% of tools have partial `log.tool.*` calls; 64% have none. The observability infrastructure (AsyncLocalStorage, withSpan, JSONL sinks, read_logs tool) is already in place — it just isn't wired into the tools.

---

## Goal

Every tool invocation produces a complete structured trace: entry (inputs), decisions (branching), steps (I/O operations), and exit (result/error). All records carry `traceId`/`spanId` so they link to the originating user message. The AI can query its own tool history via `read_logs`.

---

## Approach: Registry wraps all + manual per-tool deep logging

**Why this approach:** No structural refactoring (no base class), the `log.tool` singleton is already established, and tools are independent so parallel agents can instrument them in batches.

---

## Section 1: Registry Enhancement (`src/tools/registry.ts`)

One change to `execute()`:

```ts
// Wrap in withSpan for automatic duration + traceId on every tool
return withSpan("tool.exec", async () => {
  log.tool.toolCall(name, sanitizedArgs);           // entry record
  try {
    const result = await tool.execute(args, ctx);
    log.tool.toolResult(name, result.slice(0, 500), true);  // exit record
    return result;
  } catch (err) {
    log.tool.error("tool.exec failed", err, { tool: name, args: sanitizedArgs });
    throw err;
  }
}, { tool: name });
```

`sanitizedArgs` passes through `redactRecord` — keys named `token`, `key`, `password`, `secret`, `apiKey` are masked before logging. This gives all 111 tools entry/exit/error/duration traces with zero per-tool changes.

---

## Section 2: Per-Tool Logging Pattern (4-Point Standard)

Every tool's `execute()` adds log calls at exactly four named points:

```ts
// 1. ENTRY — inputs received, intent
log.tool.debug("<toolname>.execute: entry", { ...relevantArgs });

// 2. DECISION — whenever the tool chooses between paths
log.tool.debug("<toolname>.execute: <decision description>", { chosen, reason });

// 3. STEP — significant I/O or subprocess operations
log.tool.debug("<toolname>.execute: <operation>", { ...stepContext });

// 4. EXIT — what was produced
log.tool.debug("<toolname>.execute: exit", { ...resultSummary, durationMs });
```

Errors always: `log.tool.error("<toolname>.execute: <step> failed", err, { ...context })`

Naming convention `"<toolname>.execute: <point>"` means `read_logs({ contains: "shell.execute" })` isolates exactly that tool's full trace.

---

## Section 3: Implementation Batches

All tool batches run in parallel after B1 lands. Each batch commits independently.

| Batch | Scope | Files | Priority |
|---|---|---|---|
| **B1** | Registry foundation | `registry.ts` | First, sequential |
| **B2** | Shell + Web + Search + Files + web-utils | `shell.ts`, `web.ts`, `search.ts`, `files.ts`, `web-utils/*` (7) | Critical — high traffic |
| **B3** | macOS system tools | `tools/macos/*` (18) | Critical — side effects |
| **B4** | Dev tools | `tools/dev/*`, `code-sandbox.ts`, `sandbox.ts` (8) | Critical — command execution |
| **B5** | Cortex | `tools/cortex/*` (7) | High — zero logging today |
| **B6** | Computer-use + Live-browser | `computer-use/*` (6), `live-browser/*` (6) | High — OS/browser automation |
| **B7** | Data + Creative + MCP | `data/*` (8), `creative/*` (5), `mcp/*` (2) | Medium — fill gaps |
| **B8** | Utils + Compat + Misc | `utils/*` (15), `compat/*`, root misc | Low — bulk simple tools |

---

## Section 4: Log Fields and Debuggability Contract

Every tool trace record carries:

| Field | Source | Purpose |
|---|---|---|
| `traceId` | ALS context | Links tool call to originating user message |
| `spanId` | `withSpan("tool.exec")` | Unique per invocation |
| `parentSpanId` | ALS context | Links to `engine.iteration` span |
| `tool` | entry field | Tool name |
| `args` | entry field | Sanitized inputs |
| `durationMs` | span end | Total wall time |
| `success` | toolResult record | Did it produce a usable result |
| `err.*` | error records | Full error on failure |

### Queries enabled after implementation

```ts
// What did shell receive and return?
read_logs({ module: "tool", contains: "shell.execute" })

// Why did this request fail?
read_logs({ traceId: "abc...", errorOnly: true })

// Which tools are slowest?
read_logs({ contains: "exit", sinceMinutes: 60 })  // sort by durationMs

// What happened in a specific session?
read_logs({ sessionId: "...", module: "tool" })

// Did any tool silently return empty?
read_logs({ contains: "exit", module: "tool" })  // check fields.resultLen === 0
```

---

## What is NOT in scope

- Changing tool method signatures
- Adding a base class or abstract parent
- Instrumenting providers or the engine (already done in the observability PR)
- Changing the `read_logs` tool (already implemented)
- Adding new log levels or sinks

---

## Verification

After all batches land:

1. `npx tsc --noEmit` — zero new errors
2. `npm test` — all existing tests pass
3. Smoke: send a message that triggers a tool call → `cat logs/stackowl-$(date +%F).log | jq 'select(.fields.tool) | {tool: .fields.tool, args: .fields.args, success: .fields.success, durationMs}'`
4. Verify `traceId` on tool records matches the `traceId` on the engine.iteration record for the same request
5. Ask the AI: "What tools ran in the last 10 minutes?" — it should call `read_logs` and answer correctly
