# Tool Cortex Phase 7a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 7a of Tool Cortex: Graduated Status Narration (GSN), Goal-Anchored Verifier (GAV), schema v16, and tool catalog consolidation — reducing visible tool count from ~65 to ~55 and eliminating the "silent failure" pattern.

**Architecture:** Three independent tracks run in sequence: (1) GSN wires EventBus `tool:*` events through a pure NarrationFormatter to CLI/Telegram adapters; (2) GAV adds a post-execution verification hook to `ToolRegistry.execute()` that calls a cheap classification model against `TaskLedger.subGoals[active]` before returning to the main LLM; (3) catalog cleanup adds `deprecated`/`platforms`/`capabilities` to `ToolDefinition`, filters deprecated tools from `getDefinitions()`, and introduces unified `web` + `memory` facades plus platform-tagged macOS wrappers.

**Tech Stack:** TypeScript strict, better-sqlite3 (sync), `IntelligenceRouter.resolve("classification")` for cheap-tier GAV calls, existing `GatewayEventBus` emit/on pattern, `SubGoal` from `src/engine/types.ts`.

---

## File Map

### New files (6)

| File | Purpose |
|------|---------|
| `src/gateway/narration-formatter.ts` | Pure `formatToolEvent(event) → string \| null` — template-driven, zero LLM calls |
| `src/tools/goal-verifier.ts` | `GoalVerifier.verify()` — calls classification tier, returns ADVANCES/PARTIAL/BLOCKED/NEUTRAL |
| `src/tools/web-unified.ts` | Single `web` tool, `action: "search"\|"fetch"\|"interact"`, dispatches to existing tools |
| `src/tools/memory-unified.ts` | Single `memory` tool, `action: "search"\|"get"\|"store"`, dispatches to existing tools |
| `src/tools/macos/comms-unified.ts` | Groups: `apple_mail`, `apple_contacts`, iMessage under `macos_comms`, `platforms:["darwin"]` |
| `src/tools/macos/system-unified.ts` | Groups: `spotlight_search`, `focus_mode`, `system_info`, notifications under `macos_system`, `platforms:["darwin"]` |

### Modified files (9)

| File | Change |
|------|--------|
| `src/providers/base.ts` | `ToolDefinition` +`deprecated`, +`platforms`, +`capabilities`, +`executionPolicy`; add `ExecutionPolicy` interface |
| `src/gateway/event-bus.ts` | `GatewaySystemEvent` union +6 `tool:*` events |
| `src/tools/registry.ts` | `execute()`: platform guard, event emission, GAV hook; `getAllDefinitions()`: filter deprecated; add `setEventBus()` + `setGoalVerifier()` setters |
| `src/engine/types.ts` | `TurnRequest` +`activeSubGoal?: SubGoal`, +`userMessage?: string` |
| `src/engine/runtime.ts` | `EngineContext` +`activeSubGoal?: SubGoal`, +`userMessage?: string`; propagate into `ToolContext` in tool call loop |
| `src/engine/orchestrator.ts` | Set `activeSubGoal` and `userMessage` on `turnRequest` before each loop iteration |
| `src/memory/db.ts` | Schema v16: `trajectory_turns` +3 columns; new `workspace_tools` table; `SCHEMA_VERSION` → 16 |
| `src/gateway/adapters/cli.ts` | Subscribe to `tool:*` events, print `⟳ <narration>` lines |
| `src/index.ts` | Register unified tools; mark superseded tools `deprecated: true` |

---

## Task 1: Extend ToolDefinition with platform, capabilities, and ExecutionPolicy

**Files:**
- Modify: `src/providers/base.ts`
- Test: `__tests__/providers/base.test.ts` (new file)

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/providers/base.test.ts
import { describe, it, expect } from "vitest";
import type { ToolDefinition, ExecutionPolicy } from "../../src/providers/base.js";

describe("ToolDefinition extensions", () => {
  it("accepts deprecated flag", () => {
    const def: ToolDefinition = {
      name: "old_tool",
      description: "deprecated",
      parameters: { type: "object", properties: {} },
      deprecated: true,
    };
    expect(def.deprecated).toBe(true);
  });

  it("accepts platforms array", () => {
    const def: ToolDefinition = {
      name: "mac_tool",
      description: "mac only",
      parameters: { type: "object", properties: {} },
      platforms: ["darwin"],
    };
    expect(def.platforms).toContain("darwin");
  });

  it("accepts capabilities array", () => {
    const def: ToolDefinition = {
      name: "search_tool",
      description: "search",
      parameters: { type: "object", properties: {} },
      capabilities: ["web_fetch", "web_search"],
    };
    expect(def.capabilities).toHaveLength(2);
  });

  it("accepts executionPolicy", () => {
    const policy: ExecutionPolicy = { timeoutMs: 10000, maxRetries: 2, fallbackChain: ["other_tool"] };
    const def: ToolDefinition = {
      name: "slow_tool",
      description: "slow",
      parameters: { type: "object", properties: {} },
      executionPolicy: policy,
    };
    expect(def.executionPolicy?.timeoutMs).toBe(10000);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/providers/base.test.ts
```
Expected: TypeScript errors — `deprecated`, `platforms`, `capabilities`, `executionPolicy` do not exist on `ToolDefinition`.

- [ ] **Step 3: Add ExecutionPolicy interface and extend ToolDefinition**

In `src/providers/base.ts`, add after the `ToolDefinition` interface (after line 43):

```typescript
export interface ExecutionPolicy {
  /** Milliseconds before AbortController fires. Default: 30000 */
  timeoutMs?: number;
  /** Max retry attempts on transient failure. Default: 1 */
  maxRetries?: number;
  /** Delay between retries in ms. Default: 1000 */
  retryDelayMs?: number;
  /** Ordered list of fallback tool names to try on persistent failure */
  fallbackChain?: string[];
}
```

In `ToolDefinition`, add these optional fields before the closing `}`:

```typescript
  /** When true, this tool is hidden from LLM definitions but still callable internally */
  deprecated?: boolean;
  /** Operating systems where this tool is available. Omit = all platforms */
  platforms?: NodeJS.Platform[];
  /** Capability tags for Cost-Weighted Tool Graph routing (Phase 7b) */
  capabilities?: string[];
  /** Execution policy: timeout, retries, fallback chain */
  executionPolicy?: ExecutionPolicy;
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/providers/base.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/providers/base.ts __tests__/providers/base.test.ts
git commit -m "feat(tool-cortex): extend ToolDefinition with deprecated, platforms, capabilities, executionPolicy"
```

---

## Task 2: Add tool:* events to GatewayEventBus

**Files:**
- Modify: `src/gateway/event-bus.ts`
- Test: `__tests__/gateway/event-bus.test.ts` (new file)

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/event-bus.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("GatewayEventBus tool events", () => {
  it("emits and receives tool:start event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:start", handler);
    bus.emit({ type: "tool:start", toolName: "web_crawl", args: { url: "https://x.com" }, turnId: "t1" });
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:start", toolName: "web_crawl" })
    );
  });

  it("emits and receives tool:result event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:result", handler);
    bus.emit({ type: "tool:result", toolName: "web_crawl", success: true, durationMs: 120, truncated: false });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ success: true, durationMs: 120 }));
  });

  it("emits and receives tool:goal_blocked event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:goal_blocked", handler);
    bus.emit({ type: "tool:goal_blocked", toolName: "duckduckgo_search", subGoal: "find price data", suggestion: "try web_crawl with specific URL" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ suggestion: "try web_crawl with specific URL" }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/gateway/event-bus.test.ts
```
Expected: TypeScript errors — `tool:start` etc. not in `GatewaySystemEvent` union.

- [ ] **Step 3: Add tool:* events to GatewaySystemEvent union**

In `src/gateway/event-bus.ts`, extend the `GatewaySystemEvent` type. After the last existing event (`cost:alert`), add:

```typescript
  | { type: "tool:start";        toolName: string; args: Record<string, unknown>; turnId: string }
  | { type: "tool:result";       toolName: string; success: boolean; durationMs: number; truncated: boolean }
  | { type: "tool:retry";        toolName: string; attempt: number; reason: string }
  | { type: "tool:fallback";     fromTool: string; toTool: string; reason: string }
  | { type: "tool:goal_advance"; toolName: string; subGoal: string; verdict: "ADVANCES" | "PARTIAL" }
  | { type: "tool:goal_blocked"; toolName: string; subGoal: string; suggestion?: string }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/gateway/event-bus.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/event-bus.ts __tests__/gateway/event-bus.test.ts
git commit -m "feat(tool-cortex): add tool:* typed events to GatewayEventBus"
```

---

## Task 3: Create NarrationFormatter

**Files:**
- Create: `src/gateway/narration-formatter.ts`
- Test: `__tests__/gateway/narration-formatter.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/narration-formatter.test.ts
import { describe, it, expect } from "vitest";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";

describe("formatToolEvent", () => {
  it("returns search narration for tool:start with duckduckgo_search", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "duckduckgo_search",
      args: { query: "TypeScript 5.5 release notes" },
      turnId: "t1",
    });
    expect(msg).toBe('Searching the web for "TypeScript 5.5 release notes"…');
  });

  it("returns fetch narration for tool:start with web_crawl", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "web_crawl",
      args: { url: "https://example.com/docs" },
      turnId: "t1",
    });
    expect(msg).toContain("Fetching");
    expect(msg).toContain("example.com");
  });

  it("returns null for tool:result success (silent on success)", () => {
    const msg = formatToolEvent({
      type: "tool:result",
      toolName: "web_crawl",
      success: true,
      durationMs: 200,
      truncated: false,
    });
    expect(msg).toBeNull();
  });

  it("returns failure narration for tool:result failure", () => {
    const msg = formatToolEvent({
      type: "tool:result",
      toolName: "web_crawl",
      success: false,
      durationMs: 100,
      truncated: false,
    });
    expect(msg).toContain("failed");
  });

  it("returns blocked narration with suggestion", () => {
    const msg = formatToolEvent({
      type: "tool:goal_blocked",
      toolName: "duckduckgo_search",
      subGoal: "find price data",
      suggestion: "try web_crawl with specific URL",
    });
    expect(msg).toContain("try web_crawl with specific URL");
  });

  it("returns null for tool:goal_advance (silent on progress)", () => {
    const msg = formatToolEvent({
      type: "tool:goal_advance",
      toolName: "web_crawl",
      subGoal: "find article",
      verdict: "ADVANCES",
    });
    expect(msg).toBeNull();
  });

  it("formats memory search narration", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "recall_memory",
      args: { query: "last project discussion" },
      turnId: "t1",
    });
    expect(msg).toContain("Searching memory");
  });

  it("formats generic tool narration for unknown tool", () => {
    const msg = formatToolEvent({
      type: "tool:start",
      toolName: "some_unknown_tool",
      args: {},
      turnId: "t1",
    });
    expect(msg).toBe("Using some_unknown_tool…");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/gateway/narration-formatter.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create the NarrationFormatter**

```typescript
// src/gateway/narration-formatter.ts
import type { GatewaySystemEvent } from "./event-bus.js";

export type ToolSystemEvent = Extract<GatewaySystemEvent, { type: `tool:${string}` }>;

const WEB_SEARCH_TOOLS = new Set(["duckduckgo_search", "web_search", "google_search"]);
const WEB_FETCH_TOOLS  = new Set(["web_crawl", "scrapling_fetch"]);
const MEM_SEARCH_TOOLS = new Set(["recall_memory", "memory_search", "pellet_recall", "memory"]);
const MEM_STORE_TOOLS  = new Set(["remember"]);

export function formatToolEvent(event: ToolSystemEvent): string | null {
  switch (event.type) {
    case "tool:start": {
      const { toolName, args } = event;

      if (toolName === "web" && typeof args["action"] === "string") {
        const action = args["action"] as string;
        if (action === "search") {
          const q = String(args["query"] ?? "");
          return q ? `Searching the web for "${q}"…` : "Searching the web…";
        }
        if (action === "fetch") {
          const url = String(args["url"] ?? "");
          return url ? `Fetching ${url}…` : "Fetching page…";
        }
        if (action === "interact") return "Interacting with page…";
      }

      if (WEB_SEARCH_TOOLS.has(toolName)) {
        const q = String(args["query"] ?? args["q"] ?? "");
        return q ? `Searching the web for "${q}"…` : "Searching the web…";
      }
      if (WEB_FETCH_TOOLS.has(toolName)) {
        const url = String(args["url"] ?? "");
        return url ? `Fetching ${url}…` : "Fetching page…";
      }
      if (toolName === "camofox") {
        const action = String(args["action"] ?? "navigate");
        const url = String(args["url"] ?? "");
        return url ? `Browser: ${action} → ${url}…` : `Browser: ${action}…`;
      }

      if (toolName === "memory" && typeof args["action"] === "string") {
        const action = args["action"] as string;
        if (action === "search") {
          const q = String(args["query"] ?? "");
          return q ? `Searching memory for "${q}"…` : "Searching memory…";
        }
        if (action === "store") return "Saving to memory…";
        if (action === "get")   return "Retrieving from memory…";
      }
      if (MEM_SEARCH_TOOLS.has(toolName)) {
        const q = String(args["query"] ?? "");
        return q ? `Searching memory for "${q}"…` : "Searching memory…";
      }
      if (MEM_STORE_TOOLS.has(toolName)) return "Saving to memory…";

      if (toolName === "run_shell_command") {
        const cmd = String(args["command"] ?? "").slice(0, 60);
        return `Running: ${cmd}${cmd.length >= 60 ? "…" : ""}`;
      }
      if (toolName === "read_file") {
        const p = String(args["path"] ?? args["file_path"] ?? "");
        return p ? `Reading ${p}…` : "Reading file…";
      }
      if (toolName === "write_file" || toolName === "edit_file") {
        const p = String(args["path"] ?? args["file_path"] ?? "");
        return p ? `Writing ${p}…` : "Writing file…";
      }
      if (toolName === "orchestrate_tasks" || toolName === "summon_parliament") {
        return "Gathering perspectives…";
      }

      return `Using ${toolName}…`;
    }

    case "tool:result":
      return event.success ? null : `⚠ ${event.toolName} failed, trying alternative…`;

    case "tool:retry":
      return `Retrying ${event.toolName} (attempt ${event.attempt})…`;

    case "tool:fallback":
      return `${event.fromTool} blocked, switching to ${event.toTool}…`;

    case "tool:goal_advance":
      return null; // silent on progress — narrate only friction

    case "tool:goal_blocked":
      return event.suggestion
        ? `${event.toolName} didn't advance the goal. Trying: ${event.suggestion}`
        : `${event.toolName} didn't advance the goal, finding alternative…`;

    default:
      return null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/gateway/narration-formatter.test.ts
```
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/narration-formatter.ts __tests__/gateway/narration-formatter.test.ts
git commit -m "feat(tool-cortex): add NarrationFormatter — pure template-driven tool event → string"
```

---

## Task 4: Platform enforcement + event emission in ToolRegistry

**Files:**
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/registry-platform.test.ts` (new file)

This task adds three capabilities to `ToolRegistry`:
1. A `setEventBus()` setter and a `setGoalVerifier()` setter (GAV setter added here, hooked in Task 9)
2. Platform enforcement guard in `execute()` before the actual tool call
3. `tool:start` and `tool:result` event emission around the tool call
4. Deprecated filter in `getAllDefinitions()`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/registry-platform.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import type { ToolImplementation } from "../../src/tools/registry.js";

function makeTool(name: string, platforms?: NodeJS.Platform[], deprecated?: boolean): ToolImplementation {
  return {
    definition: { name, description: "test", parameters: { type: "object", properties: {} }, platforms, deprecated },
    category: "filesystem" as any,
    execute: async () => "ok",
  };
}

describe("ToolRegistry platform enforcement", () => {
  it("returns platform error envelope when tool is not supported on current OS", async () => {
    const registry = new ToolRegistry();
    const wrongPlatform = process.platform === "darwin" ? ["linux"] : ["darwin"];
    registry.register(makeTool("linux_only_tool", wrongPlatform as any));
    const result = await registry.execute("linux_only_tool", {}, { cwd: "/" });
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("PLATFORM_NOT_SUPPORTED");
  });

  it("executes tool normally when platforms includes current OS", async () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("current_platform_tool", [process.platform as NodeJS.Platform]));
    const result = await registry.execute("current_platform_tool", {}, { cwd: "/" });
    expect(result).toBe("ok");
  });

  it("executes tool normally when platforms is undefined (all platforms)", async () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("universal_tool"));
    const result = await registry.execute("universal_tool", {}, { cwd: "/" });
    expect(result).toBe("ok");
  });
});

describe("ToolRegistry deprecated filter", () => {
  it("excludes deprecated tools from getAllDefinitions()", () => {
    const registry = new ToolRegistry();
    registry.register(makeTool("active_tool"));
    registry.register(makeTool("old_tool", undefined, true));
    const defs = registry.getAllDefinitions();
    expect(defs.map(d => d.name)).toContain("active_tool");
    expect(defs.map(d => d.name)).not.toContain("old_tool");
  });
});

describe("ToolRegistry event emission", () => {
  it("emits tool:start before execution and tool:result after", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);

    const startHandler = vi.fn();
    const resultHandler = vi.fn();
    bus.on("tool:start", startHandler);
    bus.on("tool:result", resultHandler);

    registry.register(makeTool("emit_test_tool"));
    await registry.execute("emit_test_tool", { x: 1 }, { cwd: "/" });

    expect(startHandler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:start", toolName: "emit_test_tool" })
    );
    expect(resultHandler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:result", toolName: "emit_test_tool", success: true })
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-platform.test.ts
```
Expected: `setEventBus` not a function; platform check doesn't return structured envelope.

- [ ] **Step 3: Modify ToolRegistry**

In `src/tools/registry.ts`:

**3a — Add imports at top:**
```typescript
import type { GatewayEventBus } from "../gateway/event-bus.js";
```

**3b — Add private fields after `private _tracker`:**
```typescript
  private _eventBus: GatewayEventBus | null = null;
  // _goalVerifier added in Task 9
```

**3c — Add setter after `setTracker()`:**
```typescript
  setEventBus(bus: GatewayEventBus): void {
    this._eventBus = bus;
  }
```

**3d — In `getAllDefinitions()`, update the filter chain:**

Change:
```typescript
    return Array.from(this.tools.values())
      .filter((t) => this.checkPermission(t) === "allowed")
      .map((t) => t.definition);
```
To:
```typescript
    return Array.from(this.tools.values())
      .filter((t) => this.checkPermission(t) === "allowed")
      .filter((t) => !t.definition.deprecated)
      .map((t) => t.definition);
```

**3e — In `execute()`, add platform guard after the permission check (after the `if (perm === "denied")` block), before schema validation:**
```typescript
    // Platform enforcement
    if (tool.definition.platforms && !tool.definition.platforms.includes(process.platform as NodeJS.Platform)) {
      return JSON.stringify({
        success: false,
        data: null,
        error: {
          code: "PLATFORM_NOT_SUPPORTED",
          message: `Tool '${name}' is only available on: ${tool.definition.platforms.join(", ")}. Current platform: ${process.platform}.`,
          suggestion: "Use a cross-platform alternative or run on a supported OS.",
        },
      });
    }
```

**3f — In `execute()`, replace the try/catch block** (lines 232–258) with this updated version that adds event emission around the tool call:
```typescript
    try {
      const startTime = Date.now();
      this._eventBus?.emit({ type: "tool:start", toolName: name, args, turnId: context.engineContext?.sessionId ?? "" });

      let result = await tool.execute(args, context);
      const durationMs = Date.now() - startTime;

      if (this._tracker) {
        this._tracker.recordSuccess(name, durationMs);
      }

      const truncated = result.length > MAX_TOOL_RESULT_LENGTH;
      if (truncated) {
        result =
          result.slice(0, MAX_TOOL_RESULT_LENGTH) +
          `\n\n[OUTPUT TRUNCATED — ${result.length} chars total, showing first ${MAX_TOOL_RESULT_LENGTH}]`;
      }

      this._eventBus?.emit({ type: "tool:result", toolName: name, success: true, durationMs, truncated });
      return result;
    } catch (error) {
      const durationMs = 0;
      if (this._tracker) {
        this._tracker.recordFailure(name, durationMs);
      }
      this._eventBus?.emit({ type: "tool:result", toolName: name, success: false, durationMs, truncated: false });
      if (error instanceof ToolExecutionError) throw error;
      const msg = error instanceof Error ? error.message : String(error);
      throw new ToolExecutionError(name, msg);
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-platform.test.ts
```
Expected: 6 tests pass.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
npm test
```
Expected: All existing tests still pass (only additions, no breaking changes).

- [ ] **Step 6: Commit**

```bash
git add src/tools/registry.ts __tests__/tools/registry-platform.test.ts
git commit -m "feat(tool-cortex): add platform guard, event emission, deprecated filter to ToolRegistry"
```

---

## Task 5: Wire narration to CLI adapter

**Files:**
- Modify: `src/gateway/adapters/cli.ts`
- Test: `__tests__/gateway/adapters/cli-narration.test.ts` (new file)

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/adapters/cli-narration.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../../src/gateway/event-bus.js";

// We test the narration wiring in isolation — import the private helper
// by testing observable side effects (stdout writes) rather than internals.

describe("CLI narration wiring", () => {
  it("wireToolNarration writes narration to stdout on tool:start event", () => {
    const { wireToolNarration } = await import("../../../src/gateway/adapters/cli.js");
    const bus = new GatewayEventBus();
    const writes: string[] = [];
    const origWrite = process.stdout.write.bind(process.stdout);
    vi.spyOn(process.stdout, "write").mockImplementation((chunk: any) => {
      writes.push(String(chunk));
      return true;
    });

    wireToolNarration(bus);
    bus.emit({ type: "tool:start", toolName: "duckduckgo_search", args: { query: "test" }, turnId: "t1" });

    vi.restoreAllMocks();
    expect(writes.some(w => w.includes("Searching the web"))).toBe(true);
  });

  it("wireToolNarration does not write to stdout for tool:goal_advance (silent)", () => {
    const { wireToolNarration } = await import("../../../src/gateway/adapters/cli.js");
    const bus = new GatewayEventBus();
    const writes: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk: any) => {
      writes.push(String(chunk));
      return true;
    });

    wireToolNarration(bus);
    bus.emit({ type: "tool:goal_advance", toolName: "web_crawl", subGoal: "find docs", verdict: "ADVANCES" });

    vi.restoreAllMocks();
    expect(writes).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/gateway/adapters/cli-narration.test.ts
```
Expected: `wireToolNarration` is not exported from cli.ts.

- [ ] **Step 3: Add narration wiring to CLIAdapter**

In `src/gateway/adapters/cli.ts`:

**3a — Add import at top:**
```typescript
import { formatToolEvent } from "../../gateway/narration-formatter.js";
import type { GatewayEventBus, GatewaySystemEvent } from "../../gateway/event-bus.js";
```

**3b — Add `eventBus?: GatewayEventBus` to `CLIAdapterConfig`:**
```typescript
export interface CLIAdapterConfig {
  userId?: string;
  workspacePath?: string;
  eventBus?: GatewayEventBus;
}
```

**3c — In the constructor, after the `thinkingSuppressor` setup, add:**
```typescript
    if (config.eventBus) {
      wireToolNarration(config.eventBus);
    }
```

**3d — Add the exported helper function** at the bottom of the file (before `export`):
```typescript
export function wireToolNarration(bus: GatewayEventBus): void {
  const toolTypes: Array<GatewaySystemEvent["type"]> = [
    "tool:start", "tool:result", "tool:retry", "tool:fallback",
    "tool:goal_advance", "tool:goal_blocked",
  ];
  for (const evType of toolTypes) {
    bus.on(evType as any, (event: GatewaySystemEvent) => {
      if (!event.type.startsWith("tool:")) return;
      const msg = formatToolEvent(event as any);
      if (msg) process.stdout.write(`\r⟳ ${msg}\n`);
    });
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/gateway/adapters/cli-narration.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
npm test
```
Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/adapters/cli.ts __tests__/gateway/adapters/cli-narration.test.ts
git commit -m "feat(tool-cortex): wire GSN narration to CLI adapter via EventBus tool:* events"
```

---

## Task 6: Schema v16 migration (trajectory_turns columns + workspace_tools table)

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory/db-v16.test.ts` (new file)

The migration adds:
- Three nullable columns to `trajectory_turns`: `verification_result`, `verifier_reason`, `subgoal_id`
- New `workspace_tools` table for the SET workspace model (Phase 7c)
- Bumps `SCHEMA_VERSION` constant from 15 to 16

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/memory/db-v16.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), ".test-db-v16");

afterEach(() => {
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true });
});

describe("MemoryDatabase schema v16", () => {
  it("trajectory_turns has verification_result column", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    // Try inserting with new columns — should not throw
    expect(() => {
      (db as any).db.exec(`
        INSERT INTO trajectory_turns (id, trajectory_id, turn_index, tool_name, verification_result, verifier_reason, subgoal_id)
        VALUES ('t1', 'traj1', 0, 'web_crawl', 'ADVANCES', 'result matched sub-goal', 'sg-1')
      `);
    }).not.toThrow();
  });

  it("workspace_tools table exists with expected columns", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    expect(() => {
      (db as any).db.exec(`
        INSERT INTO workspace_tools (name, source_path, parent_tool, success_count, failure_count, state)
        VALUES ('web_evolved_v1', '/workspace/tools/web_evolved_v1.js', 'web_crawl', 0, 0, 'SHADOW')
      `);
    }).not.toThrow();
  });

  it("workspace_tools state column defaults to SHADOW", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    (db as any).db.exec(`
      INSERT INTO workspace_tools (name, source_path, parent_tool)
      VALUES ('test_tool', '/workspace/tools/test_tool.js', 'web_crawl')
    `);
    const row = (db as any).db.prepare("SELECT state FROM workspace_tools WHERE name = 'test_tool'").get() as any;
    expect(row.state).toBe("SHADOW");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/db-v16.test.ts
```
Expected: Columns not found / table not found errors.

- [ ] **Step 3: Add v16 migration to db.ts**

**3a — In `src/memory/db.ts`, change line 29:**
```typescript
const SCHEMA_VERSION = 16;
```

**3b — Find the last migration block** (it ends with `if (current < 15) { ... }`). Add a new block immediately after:
```typescript
    if (current < 16) {
      // v16 (Tool Cortex 7a): trajectory_turns — verification columns; workspace_tools for SET
      this.db.exec(`
        ALTER TABLE trajectory_turns ADD COLUMN verification_result TEXT;
        ALTER TABLE trajectory_turns ADD COLUMN verifier_reason TEXT;
        ALTER TABLE trajectory_turns ADD COLUMN subgoal_id TEXT;

        CREATE TABLE IF NOT EXISTS workspace_tools (
          name          TEXT PRIMARY KEY,
          source_path   TEXT NOT NULL,
          parent_tool   TEXT NOT NULL,
          success_count INTEGER NOT NULL DEFAULT 0,
          failure_count INTEGER NOT NULL DEFAULT 0,
          state         TEXT NOT NULL DEFAULT 'SHADOW',
          promoted_at   TEXT,
          created_at    TEXT NOT NULL DEFAULT (datetime('now')),
          created_by    TEXT NOT NULL DEFAULT 'SET'
        );
        CREATE INDEX IF NOT EXISTS idx_wt_state  ON workspace_tools(state);
        CREATE INDEX IF NOT EXISTS idx_wt_parent ON workspace_tools(parent_tool);
      `);
    }
```

**3c — Update the `PRAGMA user_version` set** at the end of the migration function. Find the line that sets user_version (it should be `this.db.pragma(\`user_version = ${SCHEMA_VERSION}\`)` or similar) — confirm it uses the constant, not a hardcoded number.

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/db-v16.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```
Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-v16.test.ts
git commit -m "feat(tool-cortex): schema v16 — trajectory_turns verification columns + workspace_tools table"
```

---

## Task 7: Create GoalVerifier

**Files:**
- Create: `src/tools/goal-verifier.ts`
- Test: `__tests__/tools/goal-verifier.test.ts`

GoalVerifier calls the `"classification"` tier (cheap/fast model, different from main LLM) to judge whether a tool result advances the active sub-goal. The different model is intentional — prevents correlated blind spots where the same model that called the tool also judges it.

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/goal-verifier.test.ts
import { describe, it, expect, vi } from "vitest";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import type { IntelligenceRouter } from "../../src/intelligence/router.js";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SubGoal } from "../../src/engine/types.js";

function makeRouter(provider = "test-provider", model = "fast-model"): IntelligenceRouter {
  return { resolve: () => ({ provider, model, tier: "low" as any }) } as any;
}

function makeProvider(responseContent: string): ModelProvider {
  return {
    name: "test-provider",
    chat: vi.fn().mockResolvedValue({ content: responseContent, model: "fast", finishReason: "stop" }),
  } as any;
}

const mockSubGoal: SubGoal = {
  id: "sg-1",
  description: "Find the latest TypeScript release version",
  status: "in_progress",
  dependsOn: [],
};

describe("GoalVerifier.verify()", () => {
  it("returns ADVANCES when model says ADVANCES", async () => {
    const provider = makeProvider(JSON.stringify({ verdict: "ADVANCES", reason: "result contains version info" }));
    const verifier = new GoalVerifier(makeRouter(), new Map([["test-provider", provider]]));
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: { url: "https://typescriptlang.org" },
      toolResult: "TypeScript 5.5 was released on June 20, 2024 with isolatedDeclarations.",
      subGoal: mockSubGoal,
      userMessage: "What is the latest TypeScript version?",
    });
    expect(result.verdict).toBe("ADVANCES");
    expect(result.reason).toBe("result contains version info");
  });

  it("returns BLOCKED with suggestion when model says BLOCKED", async () => {
    const provider = makeProvider(JSON.stringify({ verdict: "BLOCKED", reason: "page not found", suggestion: "try official docs URL" }));
    const verifier = new GoalVerifier(makeRouter(), new Map([["test-provider", provider]]));
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: { url: "https://bad-url.com" },
      toolResult: "404 Not Found",
      subGoal: mockSubGoal,
      userMessage: "What is the latest TypeScript version?",
    });
    expect(result.verdict).toBe("BLOCKED");
    expect(result.suggestion).toBe("try official docs URL");
  });

  it("returns NEUTRAL when provider is not found in map", async () => {
    const verifier = new GoalVerifier(makeRouter("missing-provider"), new Map());
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "some result",
      subGoal: mockSubGoal,
      userMessage: "query",
    });
    expect(result.verdict).toBe("NEUTRAL");
  });

  it("returns NEUTRAL when model response is unparseable", async () => {
    const provider = makeProvider("not json at all");
    const verifier = new GoalVerifier(makeRouter(), new Map([["test-provider", provider]]));
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "result",
      subGoal: mockSubGoal,
      userMessage: "query",
    });
    expect(result.verdict).toBe("NEUTRAL");
  });

  it("uses temperature 0 for deterministic judgment", async () => {
    const provider = makeProvider(JSON.stringify({ verdict: "NEUTRAL", reason: "ok" }));
    const verifier = new GoalVerifier(makeRouter(), new Map([["test-provider", provider]]));
    await verifier.verify({ toolName: "t", toolArgs: {}, toolResult: "r", subGoal: mockSubGoal, userMessage: "q" });
    expect(provider.chat).toHaveBeenCalledWith(
      expect.any(Array),
      "fast-model",
      expect.objectContaining({ temperature: 0 })
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/goal-verifier.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create GoalVerifier**

```typescript
// src/tools/goal-verifier.ts
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider } from "../providers/base.js";
import type { SubGoal } from "../engine/types.js";
import { log } from "../logger.js";

export type VerificationVerdict = "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";

export interface VerificationResult {
  verdict: VerificationVerdict;
  reason: string;
  suggestion?: string;
}

export interface VerifyArgs {
  toolName: string;
  toolArgs: Record<string, unknown>;
  toolResult: string;
  subGoal: SubGoal;
  userMessage: string;
}

const VERIFIER_PROMPT = `You are a strict goal-advancement judge. Determine whether a tool result advances the given sub-goal.

Sub-goal: {{SUBGOAL}}
User's request: {{USER_MESSAGE}}
Tool: {{TOOL_NAME}}
Tool result (first 500 chars): {{TOOL_RESULT}}

Respond with ONLY a JSON object (no markdown fences):
{"verdict":"ADVANCES"|"PARTIAL"|"BLOCKED"|"NEUTRAL","reason":"one sentence","suggestion":"what to try instead (only if BLOCKED)"}

Verdict rules:
- ADVANCES: result directly provides content that fulfills the sub-goal
- PARTIAL: has useful content but incomplete or has quality issues
- BLOCKED: empty, error response, irrelevant, or actively wrong for the sub-goal
- NEUTRAL: sub-goal is not tool-dependent (planning/reflection step)`;

const VALID_VERDICTS = new Set<string>(["ADVANCES", "PARTIAL", "BLOCKED", "NEUTRAL"]);

export class GoalVerifier {
  constructor(
    private router: IntelligenceRouter,
    private providerMap: Map<string, ModelProvider>,
  ) {}

  async verify(args: VerifyArgs): Promise<VerificationResult> {
    const { toolName, toolResult, subGoal, userMessage } = args;

    const resolved = this.router.resolve("classification");
    const provider = this.providerMap.get(resolved.provider);
    if (!provider) {
      log.engine.debug(`[GoalVerifier] provider '${resolved.provider}' not in map — skipping`);
      return { verdict: "NEUTRAL", reason: "verifier provider unavailable" };
    }

    const prompt = VERIFIER_PROMPT
      .replace("{{SUBGOAL}}", subGoal.description.slice(0, 200))
      .replace("{{USER_MESSAGE}}", userMessage.slice(0, 150))
      .replace("{{TOOL_NAME}}", toolName)
      .replace("{{TOOL_RESULT}}", toolResult.slice(0, 500));

    try {
      const resp = await provider.chat(
        [{ role: "user", content: prompt }],
        resolved.model,
        { temperature: 0, maxTokens: 120 },
      );

      const raw = resp.content.trim().replace(/^```json\n?/, "").replace(/\n?```$/, "");
      const parsed = JSON.parse(raw) as { verdict?: string; reason?: string; suggestion?: string };

      const verdict = VALID_VERDICTS.has(parsed.verdict ?? "")
        ? (parsed.verdict as VerificationVerdict)
        : "NEUTRAL";

      return {
        verdict,
        reason: parsed.reason ?? "",
        suggestion: parsed.suggestion,
      };
    } catch (err) {
      log.engine.warn(`[GoalVerifier] failed: ${err}`);
      return { verdict: "NEUTRAL", reason: "verifier error" };
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/goal-verifier.test.ts
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/goal-verifier.ts __tests__/tools/goal-verifier.test.ts
git commit -m "feat(tool-cortex): add GoalVerifier — classification-tier tool result judgment against TaskLedger sub-goals"
```

---

## Task 8: Extend TurnRequest + EngineContext with activeSubGoal

**Files:**
- Modify: `src/engine/types.ts`
- Modify: `src/engine/runtime.ts`
- Test: `__tests__/engine/types-subgoal.test.ts` (new file, compile-only)

- [ ] **Step 1: Write failing test (type-check only)**

```typescript
// __tests__/engine/types-subgoal.test.ts
import { describe, it, expect } from "vitest";
import type { TurnRequest } from "../../src/engine/types.js";
import type { EngineContext } from "../../src/engine/runtime.js";

describe("TurnRequest and EngineContext subgoal extensions", () => {
  it("TurnRequest accepts activeSubGoal", () => {
    const req: Partial<TurnRequest> = {
      activeSubGoal: {
        id: "sg-1",
        description: "Find the price of X",
        status: "in_progress",
        dependsOn: [],
      },
      userMessage: "what is the price?",
    };
    expect(req.activeSubGoal?.id).toBe("sg-1");
    expect(req.userMessage).toBe("what is the price?");
  });

  it("EngineContext accepts activeSubGoal", () => {
    const ctx: Partial<EngineContext> = {
      activeSubGoal: {
        id: "sg-2",
        description: "Research competitors",
        status: "pending",
        dependsOn: [],
      },
      userMessage: "compare competitors",
    };
    expect(ctx.activeSubGoal?.description).toContain("competitors");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/engine/types-subgoal.test.ts
```
Expected: TypeScript errors — `activeSubGoal` and `userMessage` not in `TurnRequest` or `EngineContext`.

- [ ] **Step 3: Extend TurnRequest in types.ts**

In `src/engine/types.ts`, add to the `TurnRequest` interface (after the existing `_resolvedProvider?` field):
```typescript
  /** Active sub-goal from TaskLedger — forwarded to ToolRegistry for GAV verification */
  activeSubGoal?: SubGoal;
  /** Original user message for this turn — forwarded for GAV context */
  userMessage?: string;
```

- [ ] **Step 4: Extend EngineContext in runtime.ts**

In `src/engine/runtime.ts`, add to the `EngineContext` interface after the last existing optional field:
```typescript
  /** Active sub-goal from TaskLedger — passed into ToolContext.engineContext for GAV */
  activeSubGoal?: SubGoal;
  /** Original user message for this turn — passed into ToolContext for GAV context */
  userMessage?: string;
```

Also add the import for `SubGoal` to the runtime.ts imports section:
```typescript
import type { SubGoal } from "./types.js";
```
(Check if `SubGoal` is already imported via `TurnRequest` — if so, just add the named import; if `types.js` is already imported, add `SubGoal` to the existing import list.)

- [ ] **Step 5: Propagate activeSubGoal into ToolContext in OwlEngine.runTurn()**

In `src/engine/runtime.ts`, find where `ToolContext` is built for tool execution. It should look something like:
```typescript
const toolContext: ToolContext = { cwd: engineContext.cwd ?? process.cwd(), engineContext };
```
Or the `engineContext` is passed inline. Search for `engineContext` being passed to `registry.execute()` or a `ToolContext` object construction.

When found, ensure `engineContext.activeSubGoal` and `engineContext.userMessage` are populated from the `TurnRequest`:
```typescript
// Inside runTurn(), before the tool execution loop:
if (request.activeSubGoal) {
  engineContext.activeSubGoal = request.activeSubGoal;
}
if (request.userMessage) {
  engineContext.userMessage = request.userMessage;
}
```
(If `engineContext` is built from a different source, add these fields during its construction.)

- [ ] **Step 6: Run test to verify it passes**

```bash
npx vitest run __tests__/engine/types-subgoal.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 7: Run full suite**

```bash
npm test
```
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/engine/types.ts src/engine/runtime.ts __tests__/engine/types-subgoal.test.ts
git commit -m "feat(tool-cortex): add activeSubGoal + userMessage to TurnRequest and EngineContext"
```

---

## Task 9: GAV hook in ToolRegistry + wire in Orchestrator

**Files:**
- Modify: `src/tools/registry.ts`
- Modify: `src/engine/orchestrator.ts`
- Test: `__tests__/tools/registry-gav.test.ts` (new file)

This task wires the GoalVerifier into `ToolRegistry.execute()` via a setter, adds the GAV hook between tool execution and tracker recording, and makes `OwlOrchestrator` set `activeSubGoal` on `turnRequest` before each iteration.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/registry-gav.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import { ToolExecutionError } from "../../src/tools/errors.js";
import type { SubGoal } from "../../src/engine/types.js";

const mockSubGoal: SubGoal = {
  id: "sg-1",
  description: "Find the current price of AAPL stock",
  status: "in_progress",
  dependsOn: [],
};

function makeVerifier(verdict: string): GoalVerifier {
  return {
    verify: vi.fn().mockResolvedValue({ verdict, reason: "test reason", suggestion: verdict === "BLOCKED" ? "try another tool" : undefined }),
  } as any;
}

function makeRegistry(verifier: GoalVerifier) {
  const registry = new ToolRegistry();
  registry.setGoalVerifier(verifier);
  registry.register({
    definition: { name: "test_tool", description: "test", parameters: { type: "object", properties: {} } },
    category: "web" as any,
    execute: async () => "some result",
  });
  return registry;
}

describe("ToolRegistry GAV hook", () => {
  it("calls GoalVerifier.verify() when activeSubGoal is present in engineContext", async () => {
    const verifier = makeVerifier("ADVANCES");
    const registry = makeRegistry(verifier);
    await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: mockSubGoal, userMessage: "what is AAPL price?" } as any,
    });
    expect(verifier.verify).toHaveBeenCalled();
  });

  it("does NOT call GoalVerifier.verify() when no activeSubGoal", async () => {
    const verifier = makeVerifier("ADVANCES");
    const registry = makeRegistry(verifier);
    await registry.execute("test_tool", {}, { cwd: "/" });
    expect(verifier.verify).not.toHaveBeenCalled();
  });

  it("throws ToolExecutionError wrapping GAV reason when verdict is BLOCKED", async () => {
    const verifier = makeVerifier("BLOCKED");
    const registry = makeRegistry(verifier);
    await expect(
      registry.execute("test_tool", {}, {
        cwd: "/",
        engineContext: { activeSubGoal: mockSubGoal, userMessage: "q" } as any,
      })
    ).rejects.toThrow(ToolExecutionError);
  });

  it("wraps result in tool_result_warning envelope when verdict is PARTIAL", async () => {
    const verifier = makeVerifier("PARTIAL");
    const registry = makeRegistry(verifier);
    const result = await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: mockSubGoal, userMessage: "q" } as any,
    });
    expect(result).toContain("<tool_result_warning");
    expect(result).toContain("some result");
  });

  it("returns plain result when verdict is NEUTRAL (no wrapping)", async () => {
    const verifier = makeVerifier("NEUTRAL");
    const registry = makeRegistry(verifier);
    const result = await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: mockSubGoal, userMessage: "q" } as any,
    });
    expect(result).toBe("some result");
  });

  it("skips GAV for tools with success_rate >= 0.90", async () => {
    const verifier = makeVerifier("ADVANCES");
    const registry = makeRegistry(verifier);
    // Simulate high success rate by setting up tracker manually
    const fakeTracker = { getSuccessRate: vi.fn().mockReturnValue(0.95), recordSuccess: vi.fn(), recordFailure: vi.fn() } as any;
    registry.setTracker(fakeTracker);
    await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: mockSubGoal, userMessage: "q" } as any,
    });
    expect(verifier.verify).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-gav.test.ts
```
Expected: `setGoalVerifier` not a function; GAV hook not present.

- [ ] **Step 3: Add GoalVerifier setter and GAV hook to registry.ts**

**3a — Add import:**
```typescript
import type { GoalVerifier } from "./goal-verifier.js";
```

**3b — Add private field after `_eventBus`:**
```typescript
  private _goalVerifier: GoalVerifier | null = null;
```

**3c — Add setter after `setEventBus()`:**
```typescript
  setGoalVerifier(verifier: GoalVerifier): void {
    this._goalVerifier = verifier;
  }
```

**3d — In `execute()`, add GAV hook** between the `tool.execute()` call and the `this._tracker.recordSuccess()` call. The exact location (from Task 4) is after:
```typescript
      let result = await tool.execute(args, context);
      const durationMs = Date.now() - startTime;
```
Add:
```typescript
      // GAV: verify result against active sub-goal before returning
      const subGoal = context.engineContext?.activeSubGoal;
      if (this._goalVerifier && subGoal) {
        const successRate = this._tracker?.getSuccessRate(name) ?? 0;
        const skipGAV = successRate >= 0.90 || tool.category === "cognitive";
        if (!skipGAV) {
          const verification = await this._goalVerifier.verify({
            toolName: name,
            toolArgs: args,
            toolResult: result.slice(0, 500),
            subGoal,
            userMessage: context.engineContext?.userMessage ?? "",
          });
          if (verification.verdict === "BLOCKED") {
            this._eventBus?.emit({ type: "tool:goal_blocked", toolName: name, subGoal: subGoal.description, suggestion: verification.suggestion });
            throw new ToolExecutionError(name, `[GAV] blocked: ${verification.reason}${verification.suggestion ? `. Suggestion: ${verification.suggestion}` : ""}`);
          }
          if (verification.verdict === "PARTIAL") {
            this._eventBus?.emit({ type: "tool:goal_advance", toolName: name, subGoal: subGoal.description, verdict: "PARTIAL" });
            result = `<tool_result_warning reason="${verification.reason}">\n${result}\n</tool_result_warning>`;
          } else if (verification.verdict === "ADVANCES") {
            this._eventBus?.emit({ type: "tool:goal_advance", toolName: name, subGoal: subGoal.description, verdict: "ADVANCES" });
          }
        }
      }
```

- [ ] **Step 4: Wire activeSubGoal in OwlOrchestrator**

In `src/engine/orchestrator.ts`, find the `turnRequest` construction in the `run()` method loop (around line 95). The current `messages` used in `runMessages` is already built. Add `activeSubGoal` and `userMessage` to the request:

```typescript
      // Inject active sub-goal for GAV verification
      const currentSubGoal = ledger.subGoals.find(
        (sg) => sg.status === "in_progress" || sg.status === "pending"
      ) ?? undefined;

      const turnRequest: TurnRequest = {
        messages: runMessages,
        tools: [],
        modelName: this.deps.provider.name,
        providerName: this.deps.provider.name,
        sessionId: ctx.sessionId,
        turnBudget: { ...tokenBudget },
        _resolvedProvider: this.deps.provider,
        toolRegistry: this.deps.toolRegistry,
        onStreamEvent: ctx.onStreamEvent,
        onProgress: ctx.onProgress,
        activeSubGoal: currentSubGoal,          // NEW
        userMessage,                             // NEW
      };
```

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-gav.test.ts
```
Expected: 6 tests pass.

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/tools/registry.ts src/engine/orchestrator.ts __tests__/tools/registry-gav.test.ts
git commit -m "feat(tool-cortex): wire GAV hook in ToolRegistry.execute() + inject activeSubGoal from OwlOrchestrator"
```

---

## Task 10: Web unified tool

**Files:**
- Create: `src/tools/web-unified.ts`
- Modify: `src/tools/web.ts` — add `deprecated: true`
- Modify: `src/tools/search.ts` — add `deprecated: true`
- Modify: `src/tools/web-scrapling.ts` — add `deprecated: true`
- Test: `__tests__/tools/web-unified.test.ts`

The unified `web` tool dispatches to existing implementations under the hood. Old tool names remain callable internally (for back-compat with any hardcoded calls), but are hidden from the LLM via `deprecated: true`.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/web-unified.test.ts
import { describe, it, expect, vi } from "vitest";
import { WebUnifiedTool } from "../../src/tools/web-unified.js";

describe("WebUnifiedTool", () => {
  it("definition has name 'web' and action enum", () => {
    const def = WebUnifiedTool.definition;
    expect(def.name).toBe("web");
    const actionProp = def.parameters.properties["action"];
    expect(actionProp?.enum).toContain("search");
    expect(actionProp?.enum).toContain("fetch");
    expect(actionProp?.enum).toContain("interact");
  });

  it("definition has capabilities array", () => {
    expect(WebUnifiedTool.definition.capabilities).toContain("web_search");
    expect(WebUnifiedTool.definition.capabilities).toContain("web_fetch");
  });

  it("throws on unknown action", async () => {
    await expect(
      WebUnifiedTool.execute({ action: "unknown_action" }, { cwd: "/" })
    ).rejects.toThrow();
  });

  it("definition is not deprecated", () => {
    expect(WebUnifiedTool.definition.deprecated).toBeFalsy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/web-unified.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create web-unified.ts**

```typescript
// src/tools/web-unified.ts
/**
 * Unified web tool — single LLM-visible facade for all web operations.
 * Dispatches to: duckduckgo_search (search), web_crawl/scrapling_fetch (fetch), camofox (interact).
 * Old individual tools remain registered but deprecated: true (hidden from LLM).
 */
import type { ToolImplementation, ToolContext } from "./registry.js";
import { webFetch } from "../browser/smart-fetch.js";
import { camoFoxSearch } from "./camofox.js";

export const WebUnifiedTool: ToolImplementation = {
  definition: {
    name: "web",
    description:
      "Access the web. Use action='search' to find information by query (returns titles, URLs, snippets). " +
      "Use action='fetch' to read a specific URL (returns cleaned text, ~25KB). " +
      "Use action='interact' to click, fill forms, or navigate on a live page (uses browser automation). " +
      "Examples: web(action='search', query='TypeScript 5.5 features'), " +
      "web(action='fetch', url='https://docs.example.com'), " +
      "web(action='interact', url='https://app.example.com', instruction='click Login button').",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Operation type",
          enum: ["search", "fetch", "interact"],
        },
        query: {
          type: "string",
          description: "Search query (required for action='search')",
        },
        url: {
          type: "string",
          description: "Full URL (required for action='fetch' and 'interact')",
        },
        instruction: {
          type: "string",
          description: "Natural language instruction for browser automation (action='interact' only)",
        },
      },
      required: ["action"],
    },
    capabilities: ["web_search", "web_fetch", "web_interact"],
    executionPolicy: { timeoutMs: 30000, maxRetries: 1, fallbackChain: [] },
  },
  category: "web" as any,
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args["action"] as string;

    if (action === "search") {
      const query = args["query"] as string;
      if (!query) throw new Error("web(action='search') requires 'query'");
      // Delegate to camoFoxSearch (same as existing duckduckgo_search)
      return camoFoxSearch(query);
    }

    if (action === "fetch") {
      const url = args["url"] as string;
      if (!url) throw new Error("web(action='fetch') requires 'url'");
      return webFetch(url);
    }

    if (action === "interact") {
      const url = args["url"] as string;
      const instruction = args["instruction"] as string | undefined;
      if (!url) throw new Error("web(action='interact') requires 'url'");
      // Delegate to CamoFox interaction path
      const { CamoFoxTool } = await import("./camofox.js");
      return CamoFoxTool.execute(
        { url, action: "navigate", instruction: instruction ?? "observe page" },
        context
      );
    }

    throw new Error(`web: unknown action '${action}'. Valid: search, fetch, interact`);
  },
};
```

- [ ] **Step 4: Mark old web tools as deprecated**

In `src/tools/web.ts`, add `deprecated: true` to the `WebCrawlTool.definition`:
```typescript
  definition: {
    name: "web_crawl",
    deprecated: true,
    // ... rest unchanged
  }
```

In `src/tools/search.ts`, add `deprecated: true` to the search tool definition.

In `src/tools/web-scrapling.ts`, add `deprecated: true` to the `scrapling_fetch` tool definition.

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/web-unified.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: All tests pass; web tools' own tests still pass (they test internal logic, not registration).

- [ ] **Step 7: Commit**

```bash
git add src/tools/web-unified.ts src/tools/web.ts src/tools/search.ts src/tools/web-scrapling.ts __tests__/tools/web-unified.test.ts
git commit -m "feat(tool-cortex): add unified web tool facade; deprecate web_crawl, duckduckgo_search, scrapling_fetch"
```

---

## Task 11: Memory unified tool

**Files:**
- Create: `src/tools/memory-unified.ts`
- Modify: `src/tools/recall.ts` — add `deprecated: true`
- Modify: `src/tools/remember.ts` — add `deprecated: true`
- Modify: `src/tools/pellet-recall.ts` — add `deprecated: true`
- Test: `__tests__/tools/memory-unified.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/memory-unified.test.ts
import { describe, it, expect } from "vitest";
import { MemoryUnifiedTool } from "../../src/tools/memory-unified.js";

describe("MemoryUnifiedTool", () => {
  it("definition has name 'memory' and action enum", () => {
    const def = MemoryUnifiedTool.definition;
    expect(def.name).toBe("memory");
    const actionProp = def.parameters.properties["action"];
    expect(actionProp?.enum).toContain("search");
    expect(actionProp?.enum).toContain("get");
    expect(actionProp?.enum).toContain("store");
  });

  it("has capabilities array including memory_search and memory_store", () => {
    expect(MemoryUnifiedTool.definition.capabilities).toContain("memory_search");
    expect(MemoryUnifiedTool.definition.capabilities).toContain("memory_store");
  });

  it("throws on missing query for search action", async () => {
    await expect(
      MemoryUnifiedTool.execute({ action: "search" }, { cwd: "/" })
    ).rejects.toThrow(/query/i);
  });

  it("throws on unknown action", async () => {
    await expect(
      MemoryUnifiedTool.execute({ action: "delete" }, { cwd: "/" })
    ).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/memory-unified.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create memory-unified.ts**

```typescript
// src/tools/memory-unified.ts
/**
 * Unified memory tool — single LLM-visible facade for all memory operations.
 * Dispatches to: RecallMemoryTool (search), RememberTool (store), PelletRecall (get by topic).
 */
import type { ToolImplementation, ToolContext } from "./registry.js";

export const MemoryUnifiedTool: ToolImplementation = {
  definition: {
    name: "memory",
    description:
      "Access and manage memory. Use action='search' to find past conversations, facts, or knowledge. " +
      "Use action='store' to save important information for future recall. " +
      "Use action='get' to retrieve stored knowledge about a specific topic. " +
      "Examples: memory(action='search', query='last week React discussion'), " +
      "memory(action='store', content='User prefers dark mode'), " +
      "memory(action='get', topic='user preferences').",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Memory operation",
          enum: ["search", "get", "store"],
        },
        query: {
          type: "string",
          description: "Search query (required for action='search')",
        },
        topic: {
          type: "string",
          description: "Topic to retrieve (for action='get')",
        },
        content: {
          type: "string",
          description: "Content to store (required for action='store')",
        },
        category: {
          type: "string",
          description: "Memory category for action='store' (e.g. preference, skill, goal)",
        },
      },
      required: ["action"],
    },
    capabilities: ["memory_search", "memory_store", "memory_get"],
    executionPolicy: { timeoutMs: 10000, maxRetries: 1 },
  },
  category: "cognitive" as any,
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args["action"] as string;

    if (action === "search") {
      const query = args["query"] as string;
      if (!query) throw new Error("memory(action='search') requires 'query'");
      const { RecallMemoryTool } = await import("./recall.js");
      return RecallMemoryTool.prototype
        ? new (await import("./recall.js")).RecallMemoryTool(
            (context.engineContext as any)?.memorySearcher
          ).execute({ query, scope: "all" }, context)
        : RecallMemoryTool.execute({ query, scope: "all" }, context);
    }

    if (action === "get") {
      const topic = args["topic"] as string;
      if (!topic) throw new Error("memory(action='get') requires 'topic'");
      const { PelletRecallTool } = await import("./pellet-recall.js");
      return PelletRecallTool.execute({ query: topic }, context);
    }

    if (action === "store") {
      const content = args["content"] as string;
      if (!content) throw new Error("memory(action='store') requires 'content'");
      const { RememberTool } = await import("./remember.js");
      return RememberTool.execute(
        { content, category: args["category"] ?? "context" },
        context
      );
    }

    throw new Error(`memory: unknown action '${action}'. Valid: search, get, store`);
  },
};
```

**Implementation note:** The exact dispatch calls depend on whether `RecallMemoryTool` is a class or object. Before implementing Step 3, quickly read the first 50 lines of `src/tools/recall.ts`, `src/tools/remember.ts`, and `src/tools/pellet-recall.ts` to confirm their export shapes, then adjust the dispatch calls accordingly. The test only checks definition structure and error paths, so the dispatch can be adjusted without breaking tests.

- [ ] **Step 4: Mark old memory tools deprecated**

In `src/tools/recall.ts`, add `deprecated: true` to the `recall_memory` definition.
In `src/tools/remember.ts`, add `deprecated: true` to the `remember` definition.
In `src/tools/pellet-recall.ts`, add `deprecated: true` to the `pellet_recall` definition.

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/memory-unified.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/tools/memory-unified.ts src/tools/recall.ts src/tools/remember.ts src/tools/pellet-recall.ts __tests__/tools/memory-unified.test.ts
git commit -m "feat(tool-cortex): add unified memory tool facade; deprecate recall_memory, remember, pellet_recall"
```

---

## Task 12: macOS native tool grouping with platform declaration

**Files:**
- Create: `src/tools/macos/comms-unified.ts`
- Create: `src/tools/macos/system-unified.ts`
- Test: `__tests__/tools/macos/platform-declaration.test.ts`

macOS tools stay as-is (no behavior change) but get `platforms: ["darwin"]` so the ToolRegistry returns a platform error on non-macOS systems instead of a cryptic osascript failure. The two new files group related tools: `macos_comms` (mail + contacts + iMessage-like) and `macos_system` (spotlight + focus + system info + notifications).

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/macos/platform-declaration.test.ts
import { describe, it, expect } from "vitest";
import { MacosCommsTool } from "../../../src/tools/macos/comms-unified.js";
import { MacosSystemTool } from "../../../src/tools/macos/system-unified.js";

describe("macOS grouped tools platform declaration", () => {
  it("macos_comms declares platforms: ['darwin']", () => {
    expect(MacosCommsTool.definition.platforms).toEqual(["darwin"]);
  });

  it("macos_system declares platforms: ['darwin']", () => {
    expect(MacosSystemTool.definition.platforms).toEqual(["darwin"]);
  });

  it("macos_comms has action enum covering mail, contacts, message", () => {
    const actionProp = MacosCommsTool.definition.parameters.properties["action"];
    expect(actionProp?.enum).toContain("send_mail");
    expect(actionProp?.enum).toContain("read_mail");
    expect(actionProp?.enum).toContain("search_contacts");
  });

  it("macos_system has action enum covering spotlight, focus, sysinfo", () => {
    const actionProp = MacosSystemTool.definition.parameters.properties["action"];
    expect(actionProp?.enum).toContain("spotlight");
    expect(actionProp?.enum).toContain("focus_mode");
    expect(actionProp?.enum).toContain("system_info");
    expect(actionProp?.enum).toContain("notify");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/macos/platform-declaration.test.ts
```
Expected: Modules not found.

- [ ] **Step 3: Create macos/comms-unified.ts**

```typescript
// src/tools/macos/comms-unified.ts
/**
 * macOS communications unified tool — mail, contacts.
 * Only available on darwin. Use platforms: ["darwin"] + ToolRegistry platform guard.
 */
import type { ToolImplementation, ToolContext } from "../registry.js";
import { AppleMailTool } from "./mail.js";
import { AppleContactsTool } from "./contacts.js";

export const MacosCommsTool: ToolImplementation = {
  definition: {
    name: "macos_comms",
    description:
      "macOS communications: read/send Apple Mail, search Contacts. macOS only. " +
      "Examples: macos_comms(action='read_mail', folder='INBOX', limit=5), " +
      "macos_comms(action='send_mail', to='user@example.com', subject='Hi', body='Hello'), " +
      "macos_comms(action='search_contacts', query='John').",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Communication action",
          enum: ["read_mail", "send_mail", "search_contacts", "get_contact"],
        },
        to:      { type: "string", description: "Recipient email (send_mail)" },
        subject: { type: "string", description: "Email subject (send_mail)" },
        body:    { type: "string", description: "Email body (send_mail)" },
        folder:  { type: "string", description: "Mailbox folder (read_mail). Default: INBOX" },
        limit:   { type: "number", description: "Max messages to return (read_mail). Default: 10" },
        query:   { type: "string", description: "Search query (search_contacts)" },
      },
      required: ["action"],
    },
    platforms: ["darwin"],
    capabilities: ["email_read", "email_send", "contacts_search"],
  },
  category: "communication" as any,
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args["action"] as string;
    if (action === "read_mail")       return AppleMailTool.execute({ ...args, mode: "read" }, context);
    if (action === "send_mail")       return AppleMailTool.execute({ ...args, mode: "send" }, context);
    if (action === "search_contacts") return AppleContactsTool.execute({ ...args, mode: "search" }, context);
    if (action === "get_contact")     return AppleContactsTool.execute({ ...args, mode: "get" }, context);
    throw new Error(`macos_comms: unknown action '${action}'`);
  },
};
```

- [ ] **Step 4: Create macos/system-unified.ts**

```typescript
// src/tools/macos/system-unified.ts
/**
 * macOS system utilities unified tool — spotlight, focus mode, system info, notifications.
 * Only available on darwin.
 */
import type { ToolImplementation, ToolContext } from "../registry.js";
import { SpotlightTool } from "./spotlight.js";
import { FocusModeTool } from "./focus-mode.js";
import { SystemInfoTool } from "./system-info.js";
import { NotificationTool } from "./notification.js";

export const MacosSystemTool: ToolImplementation = {
  definition: {
    name: "macos_system",
    description:
      "macOS system utilities: Spotlight search, Focus mode control, system info, send notifications. macOS only. " +
      "Examples: macos_system(action='spotlight', query='budget.xlsx'), " +
      "macos_system(action='focus_mode', mode='Work', enabled=true), " +
      "macos_system(action='system_info'), " +
      "macos_system(action='notify', title='Reminder', message='Meeting in 5 min').",
    parameters: {
      type: "object",
      properties: {
        action:  { type: "string", description: "System action", enum: ["spotlight", "focus_mode", "system_info", "notify"] },
        query:   { type: "string", description: "Spotlight search query" },
        mode:    { type: "string", description: "Focus mode name (focus_mode action)" },
        enabled: { type: "boolean", description: "Enable or disable focus mode" },
        title:   { type: "string", description: "Notification title" },
        message: { type: "string", description: "Notification message body" },
      },
      required: ["action"],
    },
    platforms: ["darwin"],
    capabilities: ["file_search", "system_control", "notification"],
  },
  category: "system" as any,
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action = args["action"] as string;
    if (action === "spotlight")   return SpotlightTool.execute(args, context);
    if (action === "focus_mode")  return FocusModeTool.execute(args, context);
    if (action === "system_info") return SystemInfoTool.execute(args, context);
    if (action === "notify")      return NotificationTool.execute(args, context);
    throw new Error(`macos_system: unknown action '${action}'`);
  },
};
```

**Implementation note:** Confirm the exact export names from `src/tools/macos/mail.ts`, `contacts.ts`, `spotlight.ts`, `focus-mode.ts`, `system-info.ts`, and `notification.ts` before writing the imports. Read the first 20 lines of each to get the export name.

- [ ] **Step 5: Mark the individual macOS tools deprecated**

In `src/tools/macos/mail.ts`, add `deprecated: true` to the `apple_mail` definition.
In `src/tools/macos/contacts.ts`, add `deprecated: true` to the `apple_contacts` definition.
In `src/tools/macos/spotlight.ts`, add `deprecated: true` to the `spotlight_search` definition.
In `src/tools/macos/focus-mode.ts`, add `deprecated: true` to the `focus_mode` definition.
In `src/tools/macos/system-info.ts`, add `deprecated: true` to the `system_info` definition.
In `src/tools/macos/notification.ts`, add `deprecated: true` to its definition.

- [ ] **Step 6: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/macos/platform-declaration.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 7: Run full suite**

```bash
npm test
```
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/tools/macos/comms-unified.ts src/tools/macos/system-unified.ts src/tools/macos/mail.ts src/tools/macos/contacts.ts src/tools/macos/spotlight.ts src/tools/macos/focus-mode.ts src/tools/macos/system-info.ts src/tools/macos/notification.ts __tests__/tools/macos/platform-declaration.test.ts
git commit -m "feat(tool-cortex): add macos_comms and macos_system unified tools with platforms:['darwin']"
```

---

## Task 13: Register unified tools + deprecated filter in index.ts

**Files:**
- Modify: `src/index.ts`
- Test: `__tests__/tools/registry-catalog.test.ts` (new file)

This task registers all new unified tools, confirms deprecated tools are excluded from `getAllDefinitions()`, and verifies the tool catalog count decreases.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/registry-catalog.test.ts
import { describe, it, expect } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { WebUnifiedTool } from "../../src/tools/web-unified.js";
import { MemoryUnifiedTool } from "../../src/tools/memory-unified.js";
import { WebCrawlTool } from "../../src/tools/web.js";

describe("Tool catalog after Phase 7a registration", () => {
  it("unified tools are not deprecated", () => {
    expect(WebUnifiedTool.definition.deprecated).toBeFalsy();
    expect(MemoryUnifiedTool.definition.deprecated).toBeFalsy();
  });

  it("deprecated tools are excluded from getAllDefinitions()", () => {
    const registry = new ToolRegistry();
    registry.register(WebUnifiedTool);
    registry.register({ ...WebCrawlTool, definition: { ...WebCrawlTool.definition, deprecated: true } });
    const defs = registry.getAllDefinitions();
    expect(defs.map(d => d.name)).toContain("web");
    expect(defs.map(d => d.name)).not.toContain("web_crawl");
  });

  it("deprecated tools are still callable directly by name (back-compat)", async () => {
    const registry = new ToolRegistry();
    // Register with deprecated: true but execute() should still work
    registry.register({ ...WebCrawlTool, definition: { ...WebCrawlTool.definition, deprecated: true } });
    // execute() skips the deprecated filter — deprecated only affects LLM visibility
    // The internal call here would fail (no real browser) but should not throw "not found"
    const result = registry.execute("web_crawl", { url: "https://example.com" }, { cwd: "/" });
    await expect(result).rejects.not.toThrow("ToolNotFoundError");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-catalog.test.ts
```
Expected: Some assertion failures showing deprecated tools still in definitions.

- [ ] **Step 3: Register new tools in index.ts**

Open `src/index.ts`. Find where tools are registered (look for `registry.register(` or `registry.registerAll(`). Add registrations for the new unified tools near the top of the existing tool registration block:

```typescript
// ─── Tool Cortex 7a: Unified facades ────────────────────────────
import { WebUnifiedTool } from "./tools/web-unified.js";
import { MemoryUnifiedTool } from "./tools/memory-unified.js";
import { MacosCommsTool } from "./tools/macos/comms-unified.js";
import { MacosSystemTool } from "./tools/macos/system-unified.js";

// Register unified tools first (they take precedence in LLM visibility)
registry.register(WebUnifiedTool);
registry.register(MemoryUnifiedTool);
registry.register(MacosCommsTool);
registry.register(MacosSystemTool);
```

The old tools (`web_crawl`, `duckduckgo_search`, `recall_memory`, etc.) keep their registrations — they just have `deprecated: true` which hides them from `getAllDefinitions()`.

- [ ] **Step 4: Verify tool catalog count is reduced**

Add a quick console.log check in the REPL:
```bash
npx tsx -e "
import { createStackOwl } from './src/index.js';
// Quick check — this won't work without full init but TypeScript will compile
console.log('Index imports OK');
"
```
If this errors, just run the full test suite — that validates the imports more thoroughly.

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/tools/registry-catalog.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/index.ts __tests__/tools/registry-catalog.test.ts
git commit -m "feat(tool-cortex): register unified tools in index.ts; deprecated tools hidden from LLM"
```

---

## Task 14: Integration test + Phase 7a verification

**Files:**
- Test: `__tests__/integration/tool-cortex-7a.test.ts` (new file)

This integration test exercises the full Phase 7a stack end-to-end using mocks for external calls.

- [ ] **Step 1: Write integration test**

```typescript
// __tests__/integration/tool-cortex-7a.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";
import type { SubGoal } from "../../src/engine/types.js";

describe("Phase 7a integration: GSN + GAV + catalog", () => {
  let registry: ToolRegistry;
  let bus: GatewayEventBus;
  const emittedEvents: any[] = [];

  beforeEach(() => {
    emittedEvents.length = 0;
    bus = new GatewayEventBus();
    registry = new ToolRegistry();
    registry.setEventBus(bus);

    // Capture all tool events
    for (const evType of ["tool:start", "tool:result", "tool:retry", "tool:fallback", "tool:goal_advance", "tool:goal_blocked"] as const) {
      bus.on(evType, (e: any) => emittedEvents.push(e));
    }
  });

  it("emits tool:start + tool:result for a successful call", async () => {
    registry.register({
      definition: { name: "echo_tool", description: "echoes input", parameters: { type: "object", properties: { msg: { type: "string", description: "message" } } } },
      execute: async (args) => `echo: ${args["msg"]}`,
    });
    await registry.execute("echo_tool", { msg: "hello" }, { cwd: "/" });
    expect(emittedEvents.some(e => e.type === "tool:start" && e.toolName === "echo_tool")).toBe(true);
    expect(emittedEvents.some(e => e.type === "tool:result" && e.toolName === "echo_tool" && e.success === true)).toBe(true);
  });

  it("emits tool:result with success:false on tool error", async () => {
    registry.register({
      definition: { name: "fail_tool", description: "always fails", parameters: { type: "object", properties: {} } },
      execute: async () => { throw new Error("intentional failure"); },
    });
    await expect(registry.execute("fail_tool", {}, { cwd: "/" })).rejects.toThrow();
    expect(emittedEvents.some(e => e.type === "tool:result" && e.toolName === "fail_tool" && e.success === false)).toBe(true);
  });

  it("platform guard returns error envelope without throwing, and emits no tool events", async () => {
    const otherPlatform = process.platform === "darwin" ? ["linux"] : ["darwin"];
    registry.register({
      definition: { name: "platform_tool", description: "platform specific", parameters: { type: "object", properties: {} }, platforms: otherPlatform as any },
      execute: async () => "should not run",
    });
    const result = await registry.execute("platform_tool", {}, { cwd: "/" });
    const parsed = JSON.parse(result);
    expect(parsed.error.code).toBe("PLATFORM_NOT_SUPPORTED");
    // No tool:start event — guard fires before execution
    expect(emittedEvents.some(e => e.type === "tool:start")).toBe(false);
  });

  it("GAV BLOCKED verdict triggers tool:goal_blocked event and throws", async () => {
    const subGoal: SubGoal = { id: "sg-1", description: "find stock price", status: "in_progress", dependsOn: [] };
    const verifier = new GoalVerifier(
      { resolve: () => ({ provider: "mock", model: "m", tier: "low" as any }) } as any,
      new Map([["mock", { chat: vi.fn().mockResolvedValue({ content: JSON.stringify({ verdict: "BLOCKED", reason: "result was error page", suggestion: "try a financial API" }) }) } as any]])
    );
    registry.setGoalVerifier(verifier);
    registry.register({
      definition: { name: "bad_tool", description: "returns garbage", parameters: { type: "object", properties: {} } },
      execute: async () => "Error: page not found",
    });
    await expect(
      registry.execute("bad_tool", {}, { cwd: "/", engineContext: { activeSubGoal: subGoal, userMessage: "find AAPL price" } as any })
    ).rejects.toThrow("[GAV] blocked");
    expect(emittedEvents.some(e => e.type === "tool:goal_blocked")).toBe(true);
  });

  it("NarrationFormatter produces human-readable output for key events", () => {
    expect(formatToolEvent({ type: "tool:start", toolName: "duckduckgo_search", args: { query: "TS 5.5" }, turnId: "t1" }))
      .toBe('Searching the web for "TS 5.5"…');
    expect(formatToolEvent({ type: "tool:goal_blocked", toolName: "web_crawl", subGoal: "find price", suggestion: "use financial API" }))
      .toContain("financial API");
  });

  it("deprecated tools absent from getAllDefinitions()", () => {
    registry.register({ definition: { name: "active", description: "a", parameters: { type: "object", properties: {} } }, execute: async () => "" });
    registry.register({ definition: { name: "old", description: "o", parameters: { type: "object", properties: {} }, deprecated: true }, execute: async () => "" });
    const names = registry.getAllDefinitions().map(d => d.name);
    expect(names).toContain("active");
    expect(names).not.toContain("old");
  });
});
```

- [ ] **Step 2: Run integration test**

```bash
npx vitest run __tests__/integration/tool-cortex-7a.test.ts
```
Expected: 6 tests pass.

- [ ] **Step 3: Run complete test suite**

```bash
npm test
```
Expected: All existing tests pass + new tests pass. Total test count ≥ 537 (512 baseline + ~25 new).

- [ ] **Step 4: Phase gate verification — check tool count reduction**

```bash
npx tsx -e "
import { ToolRegistry } from './src/tools/registry.js';
import { WebUnifiedTool } from './src/tools/web-unified.js';
import { MemoryUnifiedTool } from './src/tools/memory-unified.js';
const r = new ToolRegistry();
r.register(WebUnifiedTool);
r.register(MemoryUnifiedTool);
// Register one deprecated tool to confirm it's filtered
r.register({ ...WebUnifiedTool, definition: { ...WebUnifiedTool.definition, name: 'old_web', deprecated: true } });
const defs = r.getAllDefinitions();
console.log('LLM-visible count:', defs.length);
console.log('Names:', defs.map(d => d.name).join(', '));
" 2>/dev/null
```
Expected: `old_web` absent from names.

- [ ] **Step 5: Commit**

```bash
git add __tests__/integration/tool-cortex-7a.test.ts
git commit -m "test(tool-cortex): Phase 7a integration test — GSN events, GAV verdict, platform guard, deprecated filter"
```

---

## Phase Gate Checklist

Before merging this branch into `main`, confirm all of the following:

- [ ] `npm test` passes with ≥ 537 tests, 0 failures
- [ ] `npm run lint` passes with no new errors
- [ ] `npm run build` compiles without TypeScript errors
- [ ] End-to-end CLI smoke test: run a multi-tool query via `npm run dev`, confirm `⟳ Searching the web…` lines appear in terminal during tool execution
- [ ] Tool catalog reduction: `getAllDefinitions()` returns ≤ 58 tools (was ~65); unified tools `web` and `memory` are present; `web_crawl`, `duckduckgo_search`, `recall_memory` are absent
- [ ] Platform guard: on macOS, `macos_comms` executes normally; on Linux (or simulated), executing `macos_comms` returns `{ success: false, error: { code: "PLATFORM_NOT_SUPPORTED" } }`
- [ ] Schema v16: run `sqlite3 ~/.stackowl/memory.db ".schema trajectory_turns"` — confirm `verification_result`, `verifier_reason`, `subgoal_id` columns exist; run `".tables"` — confirm `workspace_tools` exists
- [ ] GAV baseline: run 10 multi-step tasks; check `trajectory_turns` — `verification_result` column is being populated for turns where `activeSubGoal` was set

---

## What Phases 7b and 7c Plans Will Cover

This plan implements Phase 7a only. Subsequent plans (written after 7a is in production for ≥1 week):

- **Phase 7d plan** — 5 new tools (`vision`, `document`, `sandbox`, `db_query`, `schedule`), live browser (`live_browser`), MCP CRUD (`/mcp` command)
- **Phase 7b plan** — Cost-Weighted Tool Graph (`tool_edges` table, Dijkstra replanning), Personalized Tool Router (K-NN over trajectory history, `ToolPriorLayer`)
- **Phase 7c plan** — Self-Evolving Tools (workspace model, shadow execution), Fact Provenance Chain

Phase 7b begins only when 7a's verifier BLOCKED rate > 5% over 7 days of production data (indicating enough wrong tool selections to justify memory-driven routing). Phase 7c begins after ≥500 verified trajectory turns have accumulated.
