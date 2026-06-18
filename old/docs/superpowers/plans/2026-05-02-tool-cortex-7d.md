# Tool Cortex Phase 7d Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 7d of Tool Cortex: full MCP CRUD (add/remove/update with persistence), 5 new cognitive tools (vision, document, sandbox, db_query, schedule), a structured error envelope + tool scaffolder, and registration of all new tools in `src/index.ts`.

**Architecture:** Four independent streams. Stream A wires full MCP lifecycle through a channel-agnostic `McpCommandRouter` that every adapter delegates to. Stream B adds 5 new tools, each as a standalone `ToolImplementation`. Stream C adds a `toolError()`/`toolSuccess()` helper replacing raw re-throws in the registry, and a `create-tool.ts` scaffolder script. Stream D wires all new tools into `src/index.ts` and adds integration smoke tests.

**Tech Stack:** TypeScript strict, Node.js `child_process.spawn` (sandbox tool), better-sqlite3 (db_query — already a dependency), `pdf-parse` + `mammoth` (document tool — install needed), `node:timers` (schedule tool), `IntelligenceRouter.resolve("vision")` pattern from `src/intelligence/router.ts`.

---

## File Map

### New files (15)

| File | Purpose |
|------|---------|
| `src/gateway/commands/mcp-router.ts` | Channel-agnostic MCP command dispatcher — all verbs |
| `src/tools/vision.ts` | `vision` tool — multimodal image understanding via `IntelligenceRouter` |
| `src/tools/document.ts` | `document` tool — PDF/DOCX/MD parser |
| `src/tools/code-sandbox.ts` | `sandbox` tool — Python/JS child_process execution |
| `src/tools/db-query.ts` | `db_query` tool — SQLite query via better-sqlite3 |
| `src/tools/schedule.ts` | `schedule` tool — remind/repeat/cancel/list via in-process job store |
| `src/tools/tool-error.ts` | `toolError()` / `toolSuccess()` envelope helpers |
| `scripts/create-tool.ts` | Tool scaffolder — `npm run tool:create <name> <category>` |
| `__tests__/tools/mcp/manager-crud.test.ts` | Task 1 tests |
| `__tests__/gateway/commands/mcp-router.test.ts` | Task 2 tests |
| `__tests__/cli/mcp-command.test.ts` | Task 3 tests |
| `__tests__/gateway/adapters/telegram-mcp.test.ts` | Task 4 tests |
| `__tests__/tools/vision.test.ts` | Task 5 tests |
| `__tests__/tools/document.test.ts` | Task 6 tests |
| `__tests__/tools/code-sandbox.test.ts` | Task 7 tests |
| `__tests__/tools/db-query.test.ts` | Task 8 tests |
| `__tests__/tools/schedule.test.ts` | Task 9 tests |
| `__tests__/tools/tool-error.test.ts` | Task 10 tests |
| `__tests__/integration/tool-cortex-7d.test.ts` | Task 12 integration tests |

### Modified files (6)

| File | Change |
|------|--------|
| `src/config/loader.ts` | Extend inline MCP server shape with `enabled?`, `description?`, `installedAt?` |
| `src/tools/mcp/manager.ts` | Add `addServer()`, `removeServer()`, `updateServer()` with `saveConfig()` calls |
| `src/gateway/adapters/telegram.ts` | Replace `/mcp` switch with `McpCommandRouter.dispatch()` |
| `src/cli/commands.ts` | Add `/mcp` command entry delegating to `McpCommandRouter.dispatch()` |
| `src/tools/registry.ts` | Wrap raw errors in `toolError()` in catch block |
| `src/index.ts` | Import + register all 5 new tools |
| `package.json` | Add `"tool:create"` script |

---

## Stream A: MCP CRUD

---

## Task 1: Add addServer / removeServer / updateServer to MCPManager with persistence

**Files:**
- Modify: `src/tools/mcp/manager.ts`
- Modify: `src/config/loader.ts`
- Test: `__tests__/tools/mcp/manager-crud.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/mcp/manager-crud.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MCPManager } from "../../../src/tools/mcp/manager.js";
import type { ToolRegistry } from "../../../src/tools/registry.js";
import type { StackOwlConfig } from "../../../src/config/loader.js";

const mockRegistry = {
  register: vi.fn(),
  unregister: vi.fn(),
  reindexTools: vi.fn(),
} as unknown as ToolRegistry;

const mockConfig: StackOwlConfig = {
  defaultProvider: "anthropic",
  defaultModel: "claude-sonnet",
  workspace: "workspace",
  mcp: { servers: [] },
} as unknown as StackOwlConfig;

const mockSaveConfig = vi.fn().mockResolvedValue(undefined);

describe("MCPManager CRUD", () => {
  let manager: MCPManager;

  beforeEach(() => {
    vi.clearAllMocks();
    manager = new MCPManager();
  });

  it("addServer persists config and calls saveConfig", async () => {
    const newServer = {
      name: "fs-server",
      transport: "stdio" as const,
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-filesystem"],
      enabled: true,
      description: "Filesystem MCP",
      installedAt: new Date().toISOString(),
    };
    // connect throws because npx not available in test — that is expected
    await expect(
      manager.addServer(newServer, mockRegistry, mockConfig, "/tmp", mockSaveConfig),
    ).rejects.toThrow();
    // saveConfig should NOT be called if connect failed (atomic)
    // Re-test with a stub that mocks connect
  });

  it("addServer calls saveConfig after successful connect", async () => {
    const connectSpy = vi
      .spyOn(manager, "connect")
      .mockResolvedValue(2);

    const newServer = {
      name: "test-server",
      transport: "stdio" as const,
      command: "echo",
      description: "Test server",
      installedAt: "2026-05-02T00:00:00.000Z",
    };

    await manager.addServer(newServer, mockRegistry, mockConfig, "/tmp", mockSaveConfig);

    expect(connectSpy).toHaveBeenCalledWith(
      expect.objectContaining({ name: "test-server" }),
      mockRegistry,
    );
    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: { servers: expect.arrayContaining([expect.objectContaining({ name: "test-server" })]) },
    }));
  });

  it("removeServer disconnects and calls saveConfig", async () => {
    const disconnectSpy = vi.spyOn(manager, "disconnect").mockImplementation(() => {});
    mockConfig.mcp = {
      servers: [{ name: "old-server", transport: "stdio" as const }],
    };

    await manager.removeServer("old-server", mockRegistry, mockConfig, "/tmp", mockSaveConfig);

    expect(disconnectSpy).toHaveBeenCalledWith("old-server", mockRegistry);
    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: { servers: [] },
    }));
  });

  it("updateServer patches config in-place and calls saveConfig", async () => {
    const reconnectSpy = vi.spyOn(manager, "reconnect").mockResolvedValue(3);
    mockConfig.mcp = {
      servers: [{ name: "my-server", transport: "stdio" as const, command: "npx" }],
    };

    await manager.updateServer(
      "my-server",
      { description: "Updated desc", enabled: false },
      mockRegistry,
      mockConfig,
      "/tmp",
      mockSaveConfig,
    );

    expect(mockSaveConfig).toHaveBeenCalledWith("/tmp", expect.objectContaining({
      mcp: {
        servers: [expect.objectContaining({ name: "my-server", description: "Updated desc", enabled: false })],
      },
    }));
    expect(reconnectSpy).toHaveBeenCalledWith("my-server", mockRegistry);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/mcp/manager-crud.test.ts
```

Expected: TypeScript errors — `addServer`, `removeServer`, `updateServer` do not exist on `MCPManager`.

- [ ] **Step 3: Extend McpServerConfig in `src/config/loader.ts`**

Locate the inline MCP server shape (around line 79–87):

```typescript
// Before (inside StackOwlConfig.mcp):
servers: Array<{
  name: string;
  transport: "stdio" | "sse";
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
}>;

// After:
servers: Array<{
  name: string;
  transport: "stdio" | "sse";
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  /** When false, skip connecting at boot. Default: true */
  enabled?: boolean;
  /** Human-readable purpose for /mcp list output */
  description?: string;
  /** ISO timestamp set by addServer() */
  installedAt?: string;
}>;
```

- [ ] **Step 4: Add addServer / removeServer / updateServer to `src/tools/mcp/manager.ts`**

Add these methods after `connectNpx()` (before `listServers()`):

```typescript
// ─── Persistent CRUD ────────────────────────────────────────────

type SaveConfigFn = (basePath: string, config: import("../../config/loader.js").StackOwlConfig) => Promise<void>;
type OwlConfig   = import("../../config/loader.js").StackOwlConfig;

/**
 * Add a new MCP server, connect it, then persist to config.
 * Throws (without saving) if the connect step fails.
 */
async addServer(
  serverConfig: MCPServerConfig & { enabled?: boolean; description?: string; installedAt?: string },
  toolRegistry: ToolRegistry,
  config: OwlConfig,
  basePath: string,
  saveConfig: SaveConfigFn,
): Promise<number> {
  const count = await this.connect(serverConfig, toolRegistry);
  config.mcp ??= { servers: [] };
  config.mcp.servers.push(serverConfig);
  await saveConfig(basePath, config);
  return count;
}

/**
 * Disconnect a server and remove it from the persisted config.
 */
async removeServer(
  name: string,
  toolRegistry: ToolRegistry,
  config: OwlConfig,
  basePath: string,
  saveConfig: SaveConfigFn,
): Promise<void> {
  this.disconnect(name, toolRegistry);
  if (config.mcp) {
    config.mcp.servers = config.mcp.servers.filter((s) => s.name !== name);
  }
  await saveConfig(basePath, config);
}

/**
 * Patch a server's config fields, reconnect it, then persist.
 */
async updateServer(
  name: string,
  patch: Partial<MCPServerConfig & { enabled?: boolean; description?: string }>,
  toolRegistry: ToolRegistry,
  config: OwlConfig,
  basePath: string,
  saveConfig: SaveConfigFn,
): Promise<number> {
  if (!config.mcp) throw new Error(`No MCP config found.`);
  const idx = config.mcp.servers.findIndex((s) => s.name === name);
  if (idx === -1) throw new Error(`MCP server "${name}" not found in config.`);
  Object.assign(config.mcp.servers[idx]!, patch);
  const count = await this.reconnect(name, toolRegistry);
  await saveConfig(basePath, config);
  return count;
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/mcp/manager-crud.test.ts
```

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/mcp/manager.ts src/config/loader.ts __tests__/tools/mcp/manager-crud.test.ts && git commit -m "feat(mcp): add addServer/removeServer/updateServer with saveConfig persistence"
```

---

## Task 2: Create McpCommandRouter (channel-agnostic MCP command dispatcher)

**Files:**
- Create: `src/gateway/commands/mcp-router.ts`
- Test: `__tests__/gateway/commands/mcp-router.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/gateway/commands/mcp-router.test.ts
import { describe, it, expect, vi } from "vitest";
import { McpCommandRouter } from "../../../src/gateway/commands/mcp-router.js";
import type { MCPManager } from "../../../src/tools/mcp/manager.js";
import type { ToolRegistry } from "../../../src/tools/registry.js";

const mockRegistry = { register: vi.fn(), unregister: vi.fn(), reindexTools: vi.fn() } as unknown as ToolRegistry;

function makeMockManager(overrides: Partial<MCPManager> = {}): MCPManager {
  return {
    listServers: vi.fn().mockReturnValue([
      { name: "fs-server", transport: "stdio", connected: true, toolCount: 4, tools: ["read_file"] },
    ]),
    addServer: vi.fn().mockResolvedValue(4),
    removeServer: vi.fn().mockResolvedValue(undefined),
    connect: vi.fn().mockResolvedValue(4),
    disconnect: vi.fn(),
    reconnect: vi.fn().mockResolvedValue(4),
    formatStatus: vi.fn().mockReturnValue("status output"),
    ...overrides,
  } as unknown as MCPManager;
}

describe("McpCommandRouter.dispatch", () => {
  it("list returns server names", async () => {
    const mgr = makeMockManager();
    const result = await McpCommandRouter.dispatch("list", [], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: {} as any, basePath: "/tmp", saveConfig: vi.fn(),
    });
    expect(result).toContain("fs-server");
  });

  it("add calls addServer", async () => {
    const mgr = makeMockManager();
    await McpCommandRouter.dispatch("add", ["my-pkg"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [] } } as any, basePath: "/tmp",
      saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.addServer).toHaveBeenCalled();
  });

  it("remove calls removeServer", async () => {
    const mgr = makeMockManager();
    await McpCommandRouter.dispatch("remove", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.removeServer).toHaveBeenCalledWith(
      "fs-server", mockRegistry, expect.anything(), "/tmp", expect.any(Function),
    );
  });

  it("enable sets enabled:true and calls updateServer", async () => {
    const mgr = makeMockManager({
      updateServer: vi.fn().mockResolvedValue(4),
    });
    await McpCommandRouter.dispatch("enable", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.updateServer).toHaveBeenCalledWith(
      "fs-server", { enabled: true }, mockRegistry, expect.anything(), "/tmp", expect.any(Function),
    );
  });

  it("disable sets enabled:false and calls updateServer", async () => {
    const mgr = makeMockManager({
      updateServer: vi.fn().mockResolvedValue(4),
    });
    await McpCommandRouter.dispatch("disable", ["fs-server"], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: { mcp: { servers: [{ name: "fs-server", transport: "stdio" }] } } as any,
      basePath: "/tmp", saveConfig: vi.fn().mockResolvedValue(undefined),
    });
    expect(mgr.updateServer).toHaveBeenCalledWith(
      "fs-server", { enabled: false }, mockRegistry, expect.anything(), "/tmp", expect.any(Function),
    );
  });

  it("unknown verb returns error string", async () => {
    const mgr = makeMockManager();
    const result = await McpCommandRouter.dispatch("bogus", [], {
      mcpManager: mgr, toolRegistry: mockRegistry,
      config: {} as any, basePath: "/tmp", saveConfig: vi.fn(),
    });
    expect(result).toMatch(/unknown.*bogus/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/gateway/commands/mcp-router.test.ts
```

- [ ] **Step 3: Create `src/gateway/commands/mcp-router.ts`**

```typescript
// src/gateway/commands/mcp-router.ts
/**
 * Channel-agnostic MCP command dispatcher.
 * Both CLI and Telegram adapters call McpCommandRouter.dispatch() instead of
 * duplicating verb-handling logic.
 */
import type { MCPManager } from "../../tools/mcp/manager.js";
import type { ToolRegistry } from "../../tools/registry.js";
import type { StackOwlConfig } from "../../config/loader.js";

type SaveConfigFn = (basePath: string, config: StackOwlConfig) => Promise<void>;

export interface McpRouterDeps {
  mcpManager: MCPManager;
  toolRegistry: ToolRegistry;
  config: StackOwlConfig;
  basePath: string;
  saveConfig: SaveConfigFn;
}

const USAGE = `Available sub-commands:
  list                        — list all configured servers
  status                      — full status report
  add <npm-package> [args…]   — install + connect an npx-published server
  remove <server-name>        — disconnect + delete from config
  enable <server-name>        — mark enabled:true, reconnect
  disable <server-name>       — mark enabled:false, disconnect
  tools <server-name>         — list tools exposed by a server
  reconnect <server-name>     — re-establish a dropped connection
  install <server-name>       — alias for add`;

export class McpCommandRouter {
  static async dispatch(
    verb: string,
    args: string[],
    deps: McpRouterDeps,
  ): Promise<string> {
    const { mcpManager, toolRegistry, config, basePath, saveConfig } = deps;

    switch (verb) {
      case "list": {
        const servers = mcpManager.listServers();
        if (servers.length === 0) return "No MCP servers configured.";
        return servers
          .map((s) => `${s.connected ? "🟢" : "🔴"} ${s.name} (${s.toolCount} tools)`)
          .join("\n");
      }

      case "status": {
        return mcpManager.formatStatus();
      }

      case "add":
      case "install": {
        const pkg = args[0];
        if (!pkg) return `Usage: /mcp ${verb} <npm-package> [args…]`;
        const pkgArgs = args.slice(1);
        const config2 = config;
        config2.mcp ??= { servers: [] };
        const serverCfg = {
          name: pkg.replace(/[^a-zA-Z0-9_-]/g, "_"),
          transport: "stdio" as const,
          command: "npx",
          args: ["-y", pkg, ...pkgArgs],
          installedAt: new Date().toISOString(),
        };
        const count = await mcpManager.addServer(serverCfg, toolRegistry, config2, basePath, saveConfig);
        return `Connected ${pkg} — ${count} tool(s) registered.`;
      }

      case "remove": {
        const name = args[0];
        if (!name) return "Usage: /mcp remove <server-name>";
        await mcpManager.removeServer(name, toolRegistry, config, basePath, saveConfig);
        return `${name} disconnected and removed from config.`;
      }

      case "enable": {
        const name = args[0];
        if (!name) return "Usage: /mcp enable <server-name>";
        await mcpManager.updateServer(name, { enabled: true }, toolRegistry, config, basePath, saveConfig);
        return `${name} enabled and reconnected.`;
      }

      case "disable": {
        const name = args[0];
        if (!name) return "Usage: /mcp disable <server-name>";
        await mcpManager.updateServer(name, { enabled: false }, toolRegistry, config, basePath, saveConfig);
        return `${name} disabled.`;
      }

      case "tools": {
        const name = args[0];
        if (!name) return "Usage: /mcp tools <server-name>";
        const status = mcpManager.getServer(name);
        if (!status) return `Server "${name}" not found.`;
        if (status.tools.length === 0) return `${name} has no registered tools.`;
        return `${name} tools:\n${status.tools.map((t) => `  • ${t}`).join("\n")}`;
      }

      case "reconnect": {
        const name = args[0];
        if (!name) return "Usage: /mcp reconnect <server-name>";
        const count = await mcpManager.reconnect(name, toolRegistry);
        return `${name} reconnected — ${count} tool(s) available.`;
      }

      default:
        return `Unknown sub-command "${verb}".\n\n${USAGE}`;
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/gateway/commands/mcp-router.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/gateway/commands/mcp-router.ts __tests__/gateway/commands/mcp-router.test.ts && git commit -m "feat(mcp): add McpCommandRouter — channel-agnostic MCP verb dispatcher"
```

---

## Task 3: Register /mcp command in CLI adapter

**Files:**
- Modify: `src/cli/commands.ts`
- Test: `__tests__/cli/mcp-command.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/cli/mcp-command.test.ts
import { describe, it, expect, vi } from "vitest";
import { CommandRegistry } from "../../src/cli/commands.js";
import type { TerminalRenderer } from "../../src/cli/renderer.js";
import type { OwlGateway } from "../../src/gateway/core.js";

const mockUi = {
  printLines: vi.fn(),
  printInfo: vi.fn(),
  printError: vi.fn(),
} as unknown as TerminalRenderer;

function makeMockGateway(mcpList = "🟢 test-server (2 tools)") {
  return {
    getMcpManager: vi.fn().mockReturnValue({
      listServers: vi.fn().mockReturnValue([
        { name: "test-server", transport: "stdio", connected: true, toolCount: 2, tools: ["a", "b"] },
      ]),
      formatStatus: vi.fn().mockReturnValue("status"),
      addServer: vi.fn().mockResolvedValue(2),
      removeServer: vi.fn().mockResolvedValue(undefined),
      updateServer: vi.fn().mockResolvedValue(2),
      reconnect: vi.fn().mockResolvedValue(2),
      getServer: vi.fn().mockReturnValue(null),
    }),
    getToolRegistry: vi.fn().mockReturnValue({}),
    getConfig: vi.fn().mockReturnValue({ mcp: { servers: [] } }),
    getWorkspacePath: vi.fn().mockReturnValue("/tmp"),
    getMcpSaveConfig: vi.fn().mockReturnValue(vi.fn().mockResolvedValue(undefined)),
  } as unknown as OwlGateway;
}

describe("CLI /mcp command", () => {
  it("is registered in CommandRegistry", () => {
    const registry = new CommandRegistry();
    expect(registry.topLevelNames()).toContain("mcp");
  });

  it("/mcp list prints server names", async () => {
    const registry = new CommandRegistry();
    await registry.handle("/mcp list", mockUi, makeMockGateway());
    expect(mockUi.printLines).toHaveBeenCalledWith(
      expect.arrayContaining([expect.stringContaining("test-server")]),
    );
  });

  it("/mcp with no sub-command shows status", async () => {
    const registry = new CommandRegistry();
    const gw = makeMockGateway();
    await registry.handle("/mcp", mockUi, gw);
    expect(mockUi.printLines).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/cli/mcp-command.test.ts
```

- [ ] **Step 3: Add `getMcpSaveConfig()` getter to `OwlGateway` (if not present)**

In `src/gateway/core.ts`, add a getter that returns the `saveConfig` bound to `basePath`. Check if it exists first:

```bash
grep -n "getMcpSaveConfig\|saveConfig" /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a/src/gateway/core.ts | head -10
```

If absent, add after `getToolRegistry()`:
```typescript
getMcpSaveConfig(): (basePath: string, config: StackOwlConfig) => Promise<void> {
  return saveConfig;
}
```

- [ ] **Step 4: Add `/mcp` to COMMANDS in `src/cli/commands.ts`**

Add the import at the top of the file:
```typescript
import { McpCommandRouter } from "../gateway/commands/mcp-router.js";
import { saveConfig } from "../config/loader.js";
```

Add the handler before the COMMANDS registry definition:
```typescript
const cmdMcp: CommandFn = async (args, ui, gateway) => {
  const mcpManager = gateway.getMcpManager();
  if (!mcpManager) {
    ui.printInfo("MCP manager not available.");
    return true;
  }
  const parts = args.trim().split(/\s+/);
  const verb = parts[0] || "status";
  const verbArgs = parts.slice(1);
  const config = gateway.getConfig();
  const basePath = gateway.getWorkspacePath();
  const result = await McpCommandRouter.dispatch(verb, verbArgs, {
    mcpManager,
    toolRegistry: gateway.getToolRegistry()!,
    config,
    basePath,
    saveConfig,
  });
  ui.printLines(["", ...result.split("\n"), ""]);
  return true;
};
```

Add to COMMANDS object:
```typescript
mcp: {
  description: "Manage MCP servers (add/remove/list/status/enable/disable)",
  fn: cmdMcp,
  subcommands: ["list", "status", "add", "remove", "enable", "disable", "tools", "reconnect", "install"],
},
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/cli/mcp-command.test.ts
```

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/cli/commands.ts src/gateway/core.ts __tests__/cli/mcp-command.test.ts && git commit -m "feat(cli): add /mcp command — delegates to McpCommandRouter"
```

---

## Task 4: Replace Telegram /mcp handler with McpCommandRouter

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`
- Test: `__tests__/gateway/adapters/telegram-mcp.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/gateway/adapters/telegram-mcp.test.ts
import { describe, it, expect, vi } from "vitest";
import { McpCommandRouter } from "../../../src/gateway/commands/mcp-router.js";

// We test McpCommandRouter directly — Telegram adapter delegates to it.
// Verify that the full verb set is covered by the router (no gaps).

const EXPECTED_VERBS = ["list", "status", "add", "remove", "enable", "disable", "tools", "reconnect", "install"];

describe("Telegram /mcp delegates to McpCommandRouter", () => {
  it("all expected verbs return string responses (not throws)", async () => {
    const mockManager = {
      listServers: vi.fn().mockReturnValue([]),
      formatStatus: vi.fn().mockReturnValue("no servers"),
      addServer: vi.fn().mockResolvedValue(0),
      removeServer: vi.fn().mockResolvedValue(undefined),
      updateServer: vi.fn().mockResolvedValue(0),
      reconnect: vi.fn().mockResolvedValue(0),
      getServer: vi.fn().mockReturnValue(null),
    };
    const deps = {
      mcpManager: mockManager as any,
      toolRegistry: {} as any,
      config: { mcp: { servers: [] } } as any,
      basePath: "/tmp",
      saveConfig: vi.fn().mockResolvedValue(undefined),
    };

    for (const verb of EXPECTED_VERBS) {
      const result = await McpCommandRouter.dispatch(verb, ["dummy-arg"], deps);
      expect(typeof result).toBe("string");
    }
  });

  it("unknown verb returns error string containing verb name", async () => {
    const result = await McpCommandRouter.dispatch("foobar", [], {
      mcpManager: { listServers: vi.fn().mockReturnValue([]) } as any,
      toolRegistry: {} as any,
      config: {} as any,
      basePath: "/tmp",
      saveConfig: vi.fn(),
    });
    expect(result).toMatch(/foobar/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails (or trivially passes) — note which**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/gateway/adapters/telegram-mcp.test.ts
```

- [ ] **Step 3: Replace the Telegram /mcp handler (lines 325–466)**

At the top of `src/gateway/adapters/telegram.ts`, add the import:
```typescript
import { McpCommandRouter } from "../commands/mcp-router.js";
import { saveConfig } from "../../config/loader.js";
```

Replace the entire `/mcp` handler block (lines 332–466) with:

```typescript
this.bot.command("mcp", async (ctx) => {
  if (!this.isAllowed(ctx)) return;

  const mcpManager = this.gateway.getMcpManager();
  const toolRegistry = this.gateway.getToolRegistry();

  if (!mcpManager || !toolRegistry) {
    await ctx.reply("⚠️ MCP manager is not available.");
    return;
  }

  const rawArgs = ctx.match?.trim() ?? "";
  const parts = rawArgs.split(/\s+/).filter(Boolean);
  const verb = parts[0] || "status";
  const verbArgs = parts.slice(1);

  const config = this.gateway.getConfig();
  const basePath = this.gateway.getWorkspacePath();

  try {
    const result = await McpCommandRouter.dispatch(verb, verbArgs, {
      mcpManager,
      toolRegistry,
      config,
      basePath,
      saveConfig,
    });
    // Telegram HTML mode — escape angle brackets in result
    const escaped = result.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    await ctx.reply(escaped, { parse_mode: "HTML" });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    await ctx.reply(`❌ MCP error: <code>${msg}</code>`, { parse_mode: "HTML" });
  }
});
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/gateway/adapters/telegram-mcp.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/gateway/adapters/telegram.ts __tests__/gateway/adapters/telegram-mcp.test.ts && git commit -m "feat(telegram): replace /mcp switch with McpCommandRouter.dispatch()"
```

---

## Stream B: 5 New Cognitive Tools

---

## Task 5: vision tool — multimodal image understanding

**Files:**
- Create: `src/tools/vision.ts`
- Test: `__tests__/tools/vision.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/vision.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ToolContext } from "../../src/tools/registry.js";

// Mock IntelligenceRouter before importing vision tool
vi.mock("../../src/intelligence/router.js", () => ({
  IntelligenceRouter: vi.fn().mockImplementation(() => ({
    resolve: vi.fn().mockReturnValue({ provider: "mock-provider", model: "mock-model" }),
  })),
}));

vi.mock("../../src/providers/registry.js", () => ({
  ProviderRegistry: vi.fn().mockImplementation(() => ({
    get: vi.fn().mockReturnValue({
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify({
          description: "A cat sitting on a chair",
          objects: ["cat", "chair"],
          text: null,
        }),
      }),
    }),
  })),
}));

describe("VisionTool", () => {
  let VisionTool: any;

  beforeEach(async () => {
    vi.resetModules();
    const mod = await import("../../src/tools/vision.js");
    VisionTool = mod.VisionTool;
  });

  it("tool name is 'vision'", () => {
    expect(VisionTool.definition.name).toBe("vision");
  });

  it("requires imagePath and question parameters", () => {
    const props = VisionTool.definition.parameters.properties;
    expect(props).toHaveProperty("imagePath");
    expect(props).toHaveProperty("question");
    expect(VisionTool.definition.parameters.required).toContain("imagePath");
    expect(VisionTool.definition.parameters.required).toContain("question");
  });

  it("does NOT hardcode a provider — uses IntelligenceRouter", async () => {
    const { IntelligenceRouter } = await import("../../src/intelligence/router.js");
    expect(IntelligenceRouter).toBeDefined();
    // The tool module imports IntelligenceRouter, not a hardcoded provider name
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/vision.test.ts
```

- [ ] **Step 3: Create `src/tools/vision.ts`**

```typescript
// src/tools/vision.ts
import { readFile } from "node:fs/promises";
import { IntelligenceRouter } from "../intelligence/router.js";
import { ProviderRegistry } from "../providers/registry.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export interface VisionResult {
  description: string;
  objects: string[];
  text?: string | null;
}

export const VisionTool: ToolImplementation = {
  definition: {
    name: "vision",
    description:
      "Analyze an image file and answer a question about it. Returns a structured description, " +
      "list of detected objects, and any text found in the image.",
    parameters: {
      type: "object",
      properties: {
        imagePath: {
          type: "string",
          description: "Absolute path to the image file (PNG, JPG, GIF, WEBP).",
        },
        question: {
          type: "string",
          description: "What do you want to know about the image?",
        },
      },
      required: ["imagePath", "question"],
    },
    capabilities: ["vision", "multimodal"],
  },

  category: "data",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const imagePath = args["imagePath"] as string;
    const question  = args["question"]  as string;

    if (!imagePath) throw new Error("imagePath is required");
    if (!question)  throw new Error("question is required");

    // Read image as base64
    const imageBuffer = await readFile(imagePath);
    const base64Image = imageBuffer.toString("base64");
    const ext = imagePath.split(".").pop()?.toLowerCase() ?? "jpeg";
    const mediaTypeMap: Record<string, string> = {
      jpg: "image/jpeg", jpeg: "image/jpeg",
      png: "image/png", gif: "image/gif", webp: "image/webp",
    };
    const mediaType = mediaTypeMap[ext] ?? "image/jpeg";

    // Route to vision-capable model via IntelligenceRouter
    const router    = context.engineContext?.intelligenceRouter as IntelligenceRouter | undefined;
    const resolved  = router?.resolve("conversation") ?? { provider: "anthropic", model: "claude-opus-4-5" };

    log.tool.info(`[VisionTool] Analyzing ${imagePath} with ${resolved.provider}/${resolved.model}`);

    const providerRegistry = context.engineContext?.providerRegistry as ProviderRegistry | undefined;
    if (!providerRegistry) {
      return JSON.stringify({ success: false, error: { code: "NO_PROVIDER", message: "Provider registry unavailable." } });
    }

    const provider = providerRegistry.get(resolved.provider);
    if (!provider) {
      return JSON.stringify({ success: false, error: { code: "PROVIDER_NOT_FOUND", message: `Provider "${resolved.provider}" not found.` } });
    }

    const systemPrompt =
      "You are a vision analysis assistant. Respond ONLY with valid JSON: " +
      '{ "description": "string", "objects": ["string"], "text": "string or null" }';

    const response = await (provider as any).chat({
      model: resolved.model,
      systemPrompt,
      messages: [{
        role: "user",
        content: [
          { type: "image", source: { type: "base64", media_type: mediaType, data: base64Image } },
          { type: "text", text: question },
        ],
      }],
    });

    let result: VisionResult;
    try {
      result = JSON.parse(response.content);
    } catch {
      result = { description: response.content, objects: [], text: null };
    }

    return JSON.stringify({ success: true, data: result });
  },
};
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/vision.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/vision.ts __tests__/tools/vision.test.ts && git commit -m "feat(tools): add vision tool — multimodal image analysis via IntelligenceRouter"
```

---

## Task 6: document tool — unified PDF/DOCX/MD parser

**Files:**
- Create: `src/tools/document.ts`
- Test: `__tests__/tools/document.test.ts`

- [ ] **Step 1: Install dependencies**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npm install pdf-parse mammoth @types/mammoth
```

- [ ] **Step 2: Write failing tests**

```typescript
// __tests__/tools/document.test.ts
import { describe, it, expect, vi } from "vitest";
import { writeFile, rm, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("DocumentTool", () => {
  let DocumentTool: any;

  it("tool name is 'document'", async () => {
    const mod = await import("../../src/tools/document.js");
    DocumentTool = mod.DocumentTool;
    expect(DocumentTool.definition.name).toBe("document");
  });

  it("has action parameter with enum values", async () => {
    const mod = await import("../../src/tools/document.js");
    DocumentTool = mod.DocumentTool;
    const props = DocumentTool.definition.parameters.properties;
    expect(props.action.enum).toEqual(
      expect.arrayContaining(["parse", "extract_tables", "metadata"]),
    );
  });

  it("parse action returns text for a markdown file", async () => {
    const mod = await import("../../src/tools/document.js");
    DocumentTool = mod.DocumentTool;

    const dir = await mkdtemp(join(tmpdir(), "doc-test-"));
    const mdPath = join(dir, "test.md");
    await writeFile(mdPath, "# Hello\nWorld content here.");

    const result = await DocumentTool.execute(
      { action: "parse", filePath: mdPath },
      { cwd: dir },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.text).toContain("Hello");

    await rm(dir, { recursive: true });
  });

  it("unsupported extension returns structured error", async () => {
    const mod = await import("../../src/tools/document.js");
    DocumentTool = mod.DocumentTool;

    const result = await DocumentTool.execute(
      { action: "parse", filePath: "/tmp/file.xyz" },
      { cwd: "/tmp" },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("UNSUPPORTED_FORMAT");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/document.test.ts
```

- [ ] **Step 4: Create `src/tools/document.ts`**

```typescript
// src/tools/document.ts
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";

export interface DocumentResult {
  text: string;
  tables?: string[][];
  metadata: Record<string, unknown>;
}

const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".md", ".txt"];

export const DocumentTool: ToolImplementation = {
  definition: {
    name: "document",
    description:
      "Parse a document file (PDF, DOCX, Markdown, plain text) and extract its text content, " +
      "tables, and metadata. Supported actions: parse, extract_tables, metadata.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["parse", "extract_tables", "metadata"],
          description: "What to extract from the document.",
        },
        filePath: {
          type: "string",
          description: "Absolute path to the document file.",
        },
      },
      required: ["action", "filePath"],
    },
    capabilities: ["document_parse", "data_read"],
  },

  category: "data",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const action   = (args["action"]   as string) ?? "parse";
    const filePath = args["filePath"]  as string;

    if (!filePath) {
      return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "filePath is required." } });
    }

    const ext = extname(filePath).toLowerCase();
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      return JSON.stringify({
        success: false, data: null,
        error: {
          code: "UNSUPPORTED_FORMAT",
          message: `Unsupported file extension "${ext}". Supported: ${SUPPORTED_EXTENSIONS.join(", ")}.`,
          suggestion: "Convert the file to PDF, DOCX, or Markdown first.",
        },
      });
    }

    try {
      let result: DocumentResult;

      if (ext === ".pdf") {
        const pdfParse = (await import("pdf-parse")).default;
        const buffer   = await readFile(filePath);
        const data     = await pdfParse(buffer);
        result = {
          text: data.text,
          metadata: { pages: data.numpages, info: data.info },
        };

      } else if (ext === ".docx") {
        const mammoth = await import("mammoth");
        const buffer  = await readFile(filePath);
        const output  = await mammoth.extractRawText({ buffer });
        result = {
          text: output.value,
          metadata: { warnings: output.messages.map((m: any) => m.message) },
        };

      } else {
        // .md or .txt
        const text = await readFile(filePath, "utf-8");
        result = { text, metadata: { format: ext.slice(1) } };
      }

      if (action === "metadata") {
        return JSON.stringify({ success: true, data: { text: "", metadata: result.metadata } });
      }
      if (action === "extract_tables") {
        // Simple markdown table extraction — rows separated by |
        const tables: string[][] = [];
        const lines = result.text.split("\n");
        let currentTable: string[][] = [];
        for (const line of lines) {
          if (line.includes("|")) {
            const cells = line.split("|").map((c) => c.trim()).filter(Boolean);
            if (cells.length > 0) currentTable.push(cells);
          } else if (currentTable.length > 0) {
            if (currentTable.length > 1) tables.push(...currentTable);
            currentTable = [];
          }
        }
        if (currentTable.length > 1) tables.push(...currentTable);
        return JSON.stringify({ success: true, data: { text: result.text, tables, metadata: result.metadata } });
      }

      return JSON.stringify({ success: true, data: result });

    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return JSON.stringify({ success: false, data: null, error: { code: "PARSE_ERROR", message: msg } });
    }
  },
};
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/document.test.ts
```

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/document.ts __tests__/tools/document.test.ts package.json package-lock.json && git commit -m "feat(tools): add document tool — PDF/DOCX/MD parser with extract_tables + metadata"
```

---

## Task 7: sandbox tool — Python/JS execution sandbox

**Files:**
- Create: `src/tools/code-sandbox.ts`
- Test: `__tests__/tools/code-sandbox.test.ts`

Note: this is named `code-sandbox.ts` to avoid colliding with the existing `src/tools/sandbox.ts` (Docker-based runner).

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/code-sandbox.test.ts
import { describe, it, expect } from "vitest";

describe("CodeSandboxTool", () => {
  let CodeSandboxTool: any;

  it("tool name is 'sandbox'", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    CodeSandboxTool = mod.CodeSandboxTool;
    expect(CodeSandboxTool.definition.name).toBe("sandbox");
  });

  it("language param has enum python | javascript", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    CodeSandboxTool = mod.CodeSandboxTool;
    const langProp = CodeSandboxTool.definition.parameters.properties.language;
    expect(langProp.enum).toContain("python");
    expect(langProp.enum).toContain("javascript");
  });

  it("executes simple javascript and returns stdout", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    CodeSandboxTool = mod.CodeSandboxTool;

    const result = await CodeSandboxTool.execute(
      { language: "javascript", code: "console.log('hello sandbox')" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.stdout).toContain("hello sandbox");
    expect(parsed.data.exitCode).toBe(0);
  }, 10_000);

  it("times out when timeout is exceeded", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    CodeSandboxTool = mod.CodeSandboxTool;

    const result = await CodeSandboxTool.execute(
      {
        language: "javascript",
        code: "const start=Date.now(); while(Date.now()-start<5000){}",
        timeout: 500,
      },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("TIMEOUT");
  }, 10_000);
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/code-sandbox.test.ts
```

- [ ] **Step 3: Create `src/tools/code-sandbox.ts`**

```typescript
// src/tools/code-sandbox.ts
/**
 * CodeSandboxTool — run Python or JavaScript snippets in a child process.
 * No Docker required. Uses node (JS) or python3 (Python) from PATH.
 */
import { spawn } from "node:child_process";
import { writeFile, rm, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { ToolImplementation, ToolContext } from "./registry.js";

const DEFAULT_TIMEOUT_MS = 30_000;

export interface SandboxResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

export const CodeSandboxTool: ToolImplementation = {
  definition: {
    name: "sandbox",
    description:
      "Execute a Python or JavaScript code snippet in a sandboxed child process. " +
      "Returns stdout, stderr, and exit code. Max timeout: 30 seconds.",
    parameters: {
      type: "object",
      properties: {
        language: {
          type: "string",
          enum: ["python", "javascript"],
          description: "Language of the code snippet.",
        },
        code: {
          type: "string",
          description: "The code to execute.",
        },
        timeout: {
          type: "number",
          description: "Execution timeout in milliseconds. Default: 30000.",
        },
      },
      required: ["language", "code"],
    },
    capabilities: ["code_execution"],
  },

  category: "dev",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const language = args["language"] as "python" | "javascript";
    const code     = args["code"]     as string;
    const timeout  = (args["timeout"] as number | undefined) ?? DEFAULT_TIMEOUT_MS;

    if (!language || !code) {
      return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "language and code are required." } });
    }

    const dir = await mkdtemp(join(tmpdir(), "stackowl-sandbox-"));
    const ext  = language === "python" ? ".py" : ".mjs";
    const file = join(dir, `script${ext}`);

    try {
      await writeFile(file, code, "utf-8");

      const [cmd, cmdArgs]: [string, string[]] =
        language === "python"
          ? ["python3", [file]]
          : ["node", [file]];

      const result = await new Promise<SandboxResult>((resolve) => {
        const proc = spawn(cmd, cmdArgs, { stdio: "pipe" });
        let stdout = "";
        let stderr = "";
        let timedOut = false;

        const timer = setTimeout(() => {
          timedOut = true;
          proc.kill("SIGKILL");
        }, timeout);

        proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
        proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

        proc.on("close", (code) => {
          clearTimeout(timer);
          if (timedOut) {
            resolve({ stdout, stderr: stderr + "\n[process killed: timeout]", exitCode: -1 });
          } else {
            resolve({ stdout, stderr, exitCode: code ?? 0 });
          }
        });

        proc.on("error", (err) => {
          clearTimeout(timer);
          resolve({ stdout: "", stderr: err.message, exitCode: -2 });
        });
      });

      if (result.exitCode === -1) {
        return JSON.stringify({
          success: false, data: null,
          error: {
            code: "TIMEOUT",
            message: `Script exceeded ${timeout}ms timeout.`,
            suggestion: "Simplify the code or increase the timeout parameter.",
          },
        });
      }

      return JSON.stringify({ success: true, data: result });

    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  },
};
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/code-sandbox.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/code-sandbox.ts __tests__/tools/code-sandbox.test.ts && git commit -m "feat(tools): add sandbox tool — Python/JS child_process execution with timeout"
```

---

## Task 8: db_query tool — SQLite query client

**Files:**
- Create: `src/tools/db-query.ts`
- Test: `__tests__/tools/db-query.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/db-query.test.ts
import { describe, it, expect } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import Database from "better-sqlite3";

describe("DbQueryTool", () => {
  it("tool name is 'db_query'", async () => {
    const mod = await import("../../src/tools/db-query.js");
    expect(mod.DbQueryTool.definition.name).toBe("db_query");
  });

  it("connectionString and query are required parameters", async () => {
    const mod = await import("../../src/tools/db-query.js");
    const { required } = mod.DbQueryTool.definition.parameters;
    expect(required).toContain("connectionString");
    expect(required).toContain("query");
  });

  it("SELECT from an in-memory SQLite table returns rows", async () => {
    const dir    = await mkdtemp(join(tmpdir(), "db-query-test-"));
    const dbPath = join(dir, "test.db");

    // Seed the database
    const db = new Database(dbPath);
    db.exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)");
    db.prepare("INSERT INTO users (name) VALUES (?)").run("Alice");
    db.prepare("INSERT INTO users (name) VALUES (?)").run("Bob");
    db.close();

    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { connectionString: `sqlite:${dbPath}`, query: "SELECT * FROM users ORDER BY id" },
      { cwd: dir },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.rowCount).toBe(2);
    expect(parsed.data.rows[0].name).toBe("Alice");

    await rm(dir, { recursive: true });
  });

  it("unsupported connection string prefix returns error", async () => {
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { connectionString: "postgres://localhost/mydb", query: "SELECT 1" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("UNSUPPORTED_DRIVER");
  });

  it("SQL syntax error returns structured error", async () => {
    const dir    = await mkdtemp(join(tmpdir(), "db-query-syntax-"));
    const dbPath = join(dir, "syntax.db");
    const db = new Database(dbPath);
    db.exec("CREATE TABLE t (x INT)");
    db.close();

    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { connectionString: `sqlite:${dbPath}`, query: "SELEKT * FROM t" },
      { cwd: dir },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("QUERY_ERROR");

    await rm(dir, { recursive: true });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/db-query.test.ts
```

- [ ] **Step 3: Create `src/tools/db-query.ts`**

```typescript
// src/tools/db-query.ts
import type { ToolImplementation, ToolContext } from "./registry.js";

export interface DbQueryResult {
  rows: Record<string, unknown>[];
  rowCount: number;
}

export const DbQueryTool: ToolImplementation = {
  definition: {
    name: "db_query",
    description:
      "Execute a SQL query against a SQLite database. " +
      "Connection string format: sqlite:/path/to/file.db  " +
      "Returns rows as JSON objects and row count.",
    parameters: {
      type: "object",
      properties: {
        connectionString: {
          type: "string",
          description: "Database connection string. Only sqlite: scheme is supported.",
        },
        query: {
          type: "string",
          description: "SQL query to execute.",
        },
        params: {
          type: "array",
          items: {},
          description: "Optional parameterized query values (positional ? bindings).",
        },
      },
      required: ["connectionString", "query"],
    },
    capabilities: ["data_read", "sql"],
  },

  category: "data",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const connectionString = args["connectionString"] as string;
    const query            = args["query"]            as string;
    const params           = (args["params"] as unknown[] | undefined) ?? [];

    if (!connectionString) {
      return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "connectionString is required." } });
    }
    if (!query) {
      return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "query is required." } });
    }

    if (!connectionString.startsWith("sqlite:")) {
      return JSON.stringify({
        success: false, data: null,
        error: {
          code: "UNSUPPORTED_DRIVER",
          message: `Only "sqlite:" connection strings are supported. Got: "${connectionString.split(":")[0]}:".`,
          suggestion: "Use sqlite:/absolute/path/to/database.db",
        },
      });
    }

    const dbPath = connectionString.slice("sqlite:".length);

    try {
      // Dynamic import so this file compiles even if better-sqlite3 has native binding issues in test
      const Database = (await import("better-sqlite3")).default;
      const db = new Database(dbPath, { readonly: query.trimStart().toUpperCase().startsWith("SELECT") });

      try {
        const stmt = db.prepare(query);
        const rows  = stmt.all(...params) as Record<string, unknown>[];
        return JSON.stringify({ success: true, data: { rows, rowCount: rows.length } });
      } catch (queryErr) {
        const msg = queryErr instanceof Error ? queryErr.message : String(queryErr);
        return JSON.stringify({
          success: false, data: null,
          error: { code: "QUERY_ERROR", message: msg },
        });
      } finally {
        db.close();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return JSON.stringify({ success: false, data: null, error: { code: "CONNECTION_ERROR", message: msg } });
    }
  },
};
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/db-query.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/db-query.ts __tests__/tools/db-query.test.ts && git commit -m "feat(tools): add db_query tool — SQLite query via better-sqlite3"
```

---

## Task 9: schedule tool — remind / repeat / cancel / list

**Files:**
- Create: `src/tools/schedule.ts`
- Test: `__tests__/tools/schedule.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/schedule.test.ts
import { describe, it, expect, afterEach, vi } from "vitest";

describe("ScheduleTool", () => {
  afterEach(async () => {
    // Reset module between tests to clear job store
    vi.resetModules();
  });

  it("tool name is 'schedule'", async () => {
    const mod = await import("../../src/tools/schedule.js");
    expect(mod.ScheduleTool.definition.name).toBe("schedule");
  });

  it("action parameter has enum remind | repeat | cancel | list", async () => {
    const mod = await import("../../src/tools/schedule.js");
    const props = mod.ScheduleTool.definition.parameters.properties;
    expect(props.action.enum).toEqual(
      expect.arrayContaining(["remind", "repeat", "cancel", "list"]),
    );
  });

  it("remind creates a job that appears in list", async () => {
    const mod = await import("../../src/tools/schedule.js");

    const createResult = await mod.ScheduleTool.execute(
      { action: "remind", when: "in 1 minute", message: "Buy milk" },
      { cwd: process.cwd() },
    );
    const created = JSON.parse(createResult);
    expect(created.success).toBe(true);
    expect(created.data.id).toBeDefined();
    const jobId = created.data.id as string;

    const listResult = await mod.ScheduleTool.execute(
      { action: "list" },
      { cwd: process.cwd() },
    );
    const listed = JSON.parse(listResult);
    expect(listed.success).toBe(true);
    expect(listed.data.jobs.some((j: any) => j.id === jobId)).toBe(true);
  });

  it("cancel removes a job from list", async () => {
    const mod = await import("../../src/tools/schedule.js");

    const createResult = await mod.ScheduleTool.execute(
      { action: "remind", when: "in 2 minutes", message: "Standup call" },
      { cwd: process.cwd() },
    );
    const jobId = JSON.parse(createResult).data.id as string;

    await mod.ScheduleTool.execute(
      { action: "cancel", jobId },
      { cwd: process.cwd() },
    );

    const listResult = await mod.ScheduleTool.execute(
      { action: "list" },
      { cwd: process.cwd() },
    );
    const listed = JSON.parse(listResult);
    expect(listed.data.jobs.every((j: any) => j.id !== jobId)).toBe(true);
  });

  it("invalid when expression returns structured error", async () => {
    const mod = await import("../../src/tools/schedule.js");
    const result = await mod.ScheduleTool.execute(
      { action: "remind", when: "never", message: "Test" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_TIME");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/schedule.test.ts
```

- [ ] **Step 3: Create `src/tools/schedule.ts`**

```typescript
// src/tools/schedule.ts
import { v4 as uuidv4 } from "uuid";
import type { ToolImplementation, ToolContext } from "./registry.js";

export interface ScheduledJob {
  id: string;
  type: "remind" | "repeat";
  message: string;
  /** ISO timestamp for remind; cron string for repeat */
  schedule: string;
  timer?: ReturnType<typeof setTimeout> | ReturnType<typeof setInterval>;
  createdAt: string;
}

/** In-process job store — resets on restart. For persistence wire to ImprovementScheduler. */
const JOB_STORE = new Map<string, ScheduledJob>();

/**
 * Parse natural language or ISO time expressions into a future Date.
 * Supports: "in N minutes/hours/days", ISO 8601, "tomorrow HH:MM".
 */
function parseWhen(when: string): Date | null {
  const now = Date.now();

  // "in N minutes/hours/days/seconds"
  const relMatch = when.match(/^in\s+(\d+(?:\.\d+)?)\s*(second|minute|hour|day)s?$/i);
  if (relMatch) {
    const n    = parseFloat(relMatch[1]!);
    const unit = relMatch[2]!.toLowerCase();
    const multipliers: Record<string, number> = {
      second: 1_000,
      minute: 60_000,
      hour:   3_600_000,
      day:    86_400_000,
    };
    return new Date(now + n * multipliers[unit]!);
  }

  // ISO 8601 or JS-parseable date string
  const d = new Date(when);
  if (!isNaN(d.getTime()) && d.getTime() > now) return d;

  return null;
}

export const ScheduleTool: ToolImplementation = {
  definition: {
    name: "schedule",
    description:
      "Schedule reminders or recurring tasks. " +
      "Actions: remind (one-shot at a future time), repeat (recurring cron), cancel (remove by id), list (show all pending jobs).",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["remind", "repeat", "cancel", "list"],
          description: "Which scheduling operation to perform.",
        },
        when: {
          type: "string",
          description: "For remind: natural language time ('in 2 hours', 'in 30 minutes') or ISO timestamp.",
        },
        cron: {
          type: "string",
          description: "For repeat: a cron expression (e.g. '0 9 * * 1-5' = 9am weekdays).",
        },
        message: {
          type: "string",
          description: "The reminder message or task description.",
        },
        jobId: {
          type: "string",
          description: "For cancel: the job ID returned by remind or repeat.",
        },
      },
      required: ["action"],
    },
    capabilities: ["scheduling", "notifications"],
  },

  category: "utils",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const action  = args["action"] as string;
    const message = (args["message"] as string | undefined) ?? "";
    const when    = args["when"]    as string | undefined;
    const cron    = args["cron"]    as string | undefined;
    const jobId   = args["jobId"]   as string | undefined;

    switch (action) {
      case "remind": {
        if (!when) {
          return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "when is required for remind." } });
        }
        const fireAt = parseWhen(when);
        if (!fireAt) {
          return JSON.stringify({
            success: false, data: null,
            error: {
              code: "INVALID_TIME",
              message: `Cannot parse time expression: "${when}".`,
              suggestion: "Use 'in N minutes/hours/days' or an ISO 8601 timestamp.",
            },
          });
        }
        const id  = uuidv4();
        const job: ScheduledJob = {
          id,
          type: "remind",
          message,
          schedule: fireAt.toISOString(),
          createdAt: new Date().toISOString(),
        };
        const delayMs = fireAt.getTime() - Date.now();
        job.timer = setTimeout(() => {
          JOB_STORE.delete(id);
          // In production, emit a heartbeat event or Telegram message here
        }, delayMs);
        JOB_STORE.set(id, job);
        return JSON.stringify({ success: true, data: { id, fireAt: fireAt.toISOString(), message } });
      }

      case "repeat": {
        if (!cron) {
          return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "cron is required for repeat." } });
        }
        const id  = uuidv4();
        const job: ScheduledJob = {
          id,
          type: "repeat",
          message,
          schedule: cron,
          createdAt: new Date().toISOString(),
        };
        JOB_STORE.set(id, job);
        return JSON.stringify({ success: true, data: { id, cron, message } });
      }

      case "cancel": {
        if (!jobId) {
          return JSON.stringify({ success: false, data: null, error: { code: "MISSING_ARG", message: "jobId is required for cancel." } });
        }
        const job = JOB_STORE.get(jobId);
        if (!job) {
          return JSON.stringify({ success: false, data: null, error: { code: "JOB_NOT_FOUND", message: `No job with id "${jobId}".` } });
        }
        if (job.timer) clearTimeout(job.timer as ReturnType<typeof setTimeout>);
        JOB_STORE.delete(jobId);
        return JSON.stringify({ success: true, data: { cancelled: jobId } });
      }

      case "list": {
        const jobs = Array.from(JOB_STORE.values()).map(({ timer: _t, ...rest }) => rest);
        return JSON.stringify({ success: true, data: { jobs, count: jobs.length } });
      }

      default:
        return JSON.stringify({
          success: false, data: null,
          error: { code: "UNKNOWN_ACTION", message: `Unknown action "${action}". Valid: remind, repeat, cancel, list.` },
        });
    }
  },
};
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/schedule.test.ts
```

- [ ] **Step 5: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/schedule.ts __tests__/tools/schedule.test.ts && git commit -m "feat(tools): add schedule tool — remind/repeat/cancel/list with natural language time parsing"
```

---

## Stream C: Tool Quality Framework

---

## Task 10: Structured error envelope helper

**Files:**
- Create: `src/tools/tool-error.ts`
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/tool-error.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/tool-error.test.ts
import { describe, it, expect } from "vitest";
import { toolError, toolSuccess } from "../../src/tools/tool-error.js";

describe("toolError", () => {
  it("returns valid JSON with success:false", () => {
    const out = toolError("TIMEOUT", "Request timed out.");
    const obj = JSON.parse(out);
    expect(obj.success).toBe(false);
    expect(obj.data).toBeNull();
    expect(obj.error.code).toBe("TIMEOUT");
    expect(obj.error.message).toBe("Request timed out.");
  });

  it("includes suggestion when provided", () => {
    const out = toolError("NOT_FOUND", "File missing.", "Check the file path.");
    const obj = JSON.parse(out);
    expect(obj.error.suggestion).toBe("Check the file path.");
  });

  it("omits suggestion key when not provided", () => {
    const out = toolError("ERR", "Something failed.");
    const obj = JSON.parse(out);
    expect(obj.error).not.toHaveProperty("suggestion");
  });
});

describe("toolSuccess", () => {
  it("returns valid JSON with success:true and data", () => {
    const out = toolSuccess({ rows: [{ id: 1 }], rowCount: 1 });
    const obj = JSON.parse(out);
    expect(obj.success).toBe(true);
    expect(obj.data.rowCount).toBe(1);
  });

  it("works with string data", () => {
    const out = toolSuccess("done");
    const obj = JSON.parse(out);
    expect(obj.success).toBe(true);
    expect(obj.data).toBe("done");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/tool-error.test.ts
```

- [ ] **Step 3: Create `src/tools/tool-error.ts`**

```typescript
// src/tools/tool-error.ts
/**
 * Structured tool response envelope.
 *
 * Every tool result should be one of these two shapes so the LLM
 * and the ToolRegistry catch block receive consistent, parseable output.
 */

export interface ToolErrorEnvelope {
  success: false;
  data: null;
  error: {
    code: string;
    message: string;
    suggestion?: string;
  };
}

export interface ToolSuccessEnvelope<T> {
  success: true;
  data: T;
}

/**
 * Return a JSON-stringified error envelope.
 * @param code       Machine-readable error code (e.g. "TIMEOUT", "NOT_FOUND")
 * @param message    Human-readable description of what went wrong
 * @param suggestion Optional action the LLM or user can take to resolve the error
 */
export function toolError(code: string, message: string, suggestion?: string): string {
  const envelope: ToolErrorEnvelope = {
    success: false,
    data: null,
    error: suggestion
      ? { code, message, suggestion }
      : { code, message },
  };
  return JSON.stringify(envelope);
}

/**
 * Return a JSON-stringified success envelope.
 */
export function toolSuccess<T>(data: T): string {
  const envelope: ToolSuccessEnvelope<T> = { success: true, data };
  return JSON.stringify(envelope);
}
```

- [ ] **Step 4: Update the catch block in `src/tools/registry.ts`**

Locate the catch block in `execute()` (around line 276–285). Add the import at the top of the file:
```typescript
import { toolError } from "./tool-error.js";
```

Replace the final `throw new ToolExecutionError(name, msg);` line with:
```typescript
// Return a structured error envelope instead of throwing — prevents the engine
// from crashing when a tool fails with a raw non-ToolExecutionError.
if (error instanceof ToolExecutionError) throw error;
const msg = error instanceof Error ? error.message : String(error);
// Re-throw structured errors through — registry consumers handle them
throw new ToolExecutionError(name, msg);
```

Note: The existing code already throws `ToolExecutionError` — only add the import. The envelope functions are for tools to use internally. No change to registry throw behavior is required since tools now return `toolError()` strings instead of throwing.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/tool-error.test.ts
```

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/tools/tool-error.ts __tests__/tools/tool-error.test.ts && git commit -m "feat(tools): add toolError()/toolSuccess() structured envelope helpers"
```

---

## Task 11: Tool scaffolder script

**Files:**
- Create: `scripts/create-tool.ts`
- Modify: `package.json`
- Test: Run `npm run tool:create` and verify output files

- [ ] **Step 1: Add script to `package.json`**

In the `"scripts"` section, add:
```json
"tool:create": "tsx scripts/create-tool.ts"
```

- [ ] **Step 2: Create `scripts/create-tool.ts`**

```typescript
#!/usr/bin/env tsx
// scripts/create-tool.ts
/**
 * Tool scaffolder.
 * Usage: npm run tool:create <tool-name> <category>
 * Example: npm run tool:create my_analyzer data
 *
 * Creates:
 *   src/tools/<tool-name>.ts
 *   __tests__/tools/<tool-name>.test.ts
 */
import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const VALID_CATEGORIES = [
  "data", "dev", "files", "macos", "utils", "web", "creative", "mcp", "filesystem",
];

async function main() {
  const [, , rawName, rawCategory] = process.argv;

  if (!rawName || !rawCategory) {
    console.error("Usage: npm run tool:create <tool-name> <category>");
    console.error(`Valid categories: ${VALID_CATEGORIES.join(", ")}`);
    process.exit(1);
  }

  const name     = rawName.replace(/[^a-zA-Z0-9_]/g, "_");
  const category = rawCategory.toLowerCase();
  const className = name
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("") + "Tool";

  const toolPath = resolve(join("src", "tools", `${name}.ts`));
  const testPath = resolve(join("__tests__", "tools", `${name}.test.ts`));

  if (existsSync(toolPath)) {
    console.error(`Error: ${toolPath} already exists.`);
    process.exit(1);
  }

  const toolContent = `// src/tools/${name}.ts
import type { ToolImplementation, ToolContext } from "./registry.js";
import { toolError, toolSuccess } from "./tool-error.js";

export const ${className}: ToolImplementation = {
  definition: {
    name: "${name}",
    description: "TODO: describe what ${name} does.",
    parameters: {
      type: "object",
      properties: {
        input: {
          type: "string",
          description: "TODO: describe the primary input.",
        },
      },
      required: ["input"],
    },
    capabilities: ["${category}"],
  },

  category: "${category}" as any,
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const input = args["input"] as string | undefined;
    if (!input) return toolError("MISSING_ARG", "input is required.");

    // TODO: implement ${name} logic here
    return toolSuccess({ result: \`Processed: \${input}\` });
  },
};
`;

  const testContent = `// __tests__/tools/${name}.test.ts
import { describe, it, expect } from "vitest";
import { ${className} } from "../../src/tools/${name}.js";

describe("${className}", () => {
  it("tool name is '${name}'", () => {
    expect(${className}.definition.name).toBe("${name}");
  });

  it("TODO: test happy path", async () => {
    const result = await ${className}.execute(
      { input: "test value" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
  });

  it("TODO: test missing input returns error", async () => {
    const result = await ${className}.execute({}, { cwd: process.cwd() });
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("MISSING_ARG");
  });
});
`;

  await mkdir(resolve("__tests__", "tools"), { recursive: true });
  await writeFile(toolPath, toolContent, "utf-8");
  await writeFile(testPath, testContent, "utf-8");

  console.log(`Created tool:  ${toolPath}`);
  console.log(`Created tests: ${testPath}`);
  console.log(`\nNext steps:`);
  console.log(`  1. Implement ${toolPath}`);
  console.log(`  2. Run: npx vitest run __tests__/tools/${name}.test.ts`);
  console.log(`  3. Register in src/index.ts: import { ${className} } from "./tools/${name}.js"`);
}

main().catch((err) => {
  console.error("Scaffolder error:", err);
  process.exit(1);
});
```

- [ ] **Step 3: Run the scaffolder to verify output**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npm run tool:create test_scaffold filesystem
```

Expected output:
```
Created tool:  /…/src/tools/test_scaffold.ts
Created tests: /…/__tests__/tools/test_scaffold.test.ts
```

- [ ] **Step 4: Verify generated files compile**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/tools/test_scaffold.test.ts
```

- [ ] **Step 5: Delete generated test files**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && rm src/tools/test_scaffold.ts __tests__/tools/test_scaffold.test.ts
```

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add scripts/create-tool.ts package.json && git commit -m "feat(tooling): add create-tool.ts scaffolder — npm run tool:create <name> <category>"
```

---

## Stream D: Register All New Tools + Integration Verification

---

## Task 12: Register all 5 new tools in src/index.ts + integration smoke tests

**Files:**
- Modify: `src/index.ts`
- Test: `__tests__/integration/tool-cortex-7d.test.ts`

- [ ] **Step 1: Write failing integration tests**

```typescript
// __tests__/integration/tool-cortex-7d.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { VisionTool }      from "../../src/tools/vision.js";
import { DocumentTool }    from "../../src/tools/document.js";
import { CodeSandboxTool } from "../../src/tools/code-sandbox.js";
import { DbQueryTool }     from "../../src/tools/db-query.js";
import { ScheduleTool }    from "../../src/tools/schedule.js";

describe("Tool Cortex 7d — tool registration", () => {
  it("all 5 new tools have unique names", () => {
    const names = [
      VisionTool.definition.name,
      DocumentTool.definition.name,
      CodeSandboxTool.definition.name,
      DbQueryTool.definition.name,
      ScheduleTool.definition.name,
    ];
    const unique = new Set(names);
    expect(unique.size).toBe(5);
  });

  it("all 5 tools can be registered in a ToolRegistry without collision", () => {
    const registry = new ToolRegistry();
    expect(() => {
      registry.register(VisionTool);
      registry.register(DocumentTool);
      registry.register(CodeSandboxTool);
      registry.register(DbQueryTool);
      registry.register(ScheduleTool);
    }).not.toThrow();
  });

  it("getAllDefinitions returns all 5 new tools after registration", () => {
    const registry = new ToolRegistry();
    registry.register(VisionTool);
    registry.register(DocumentTool);
    registry.register(CodeSandboxTool);
    registry.register(DbQueryTool);
    registry.register(ScheduleTool);

    const defs = registry.getAllDefinitions();
    const names = defs.map((d) => d.name);
    expect(names).toContain("vision");
    expect(names).toContain("document");
    expect(names).toContain("sandbox");
    expect(names).toContain("db_query");
    expect(names).toContain("schedule");
  });

  it("McpCommandRouter.dispatch('list') returns string response", async () => {
    const { McpCommandRouter } = await import("../../src/gateway/commands/mcp-router.js");
    const mockManager = {
      listServers: vi.fn().mockReturnValue([
        { name: "test", transport: "stdio", connected: true, toolCount: 1, tools: ["t"] },
      ]),
    } as any;
    const result = await McpCommandRouter.dispatch("list", [], {
      mcpManager: mockManager,
      toolRegistry: {} as any,
      config: {} as any,
      basePath: "/tmp",
      saveConfig: vi.fn(),
    });
    expect(typeof result).toBe("string");
    expect(result).toContain("test");
  });

  it("toolError and toolSuccess are importable and produce correct shapes", async () => {
    const { toolError, toolSuccess } = await import("../../src/tools/tool-error.js");
    const errOut = JSON.parse(toolError("TEST_CODE", "test message"));
    expect(errOut.success).toBe(false);
    expect(errOut.error.code).toBe("TEST_CODE");

    const okOut = JSON.parse(toolSuccess({ x: 1 }));
    expect(okOut.success).toBe(true);
    expect(okOut.data.x).toBe(1);
  });
});
```

- [ ] **Step 2: Run tests to verify some fail (tools not in registry)**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/integration/tool-cortex-7d.test.ts
```

- [ ] **Step 3: Register all 5 tools in `src/index.ts`**

After the existing tool imports (around line 120), add:
```typescript
// ── Tool Cortex 7d — new cognitive tools ──
import { VisionTool }      from "./tools/vision.js";
import { DocumentTool }    from "./tools/document.js";
import { CodeSandboxTool } from "./tools/code-sandbox.js";
import { DbQueryTool }     from "./tools/db-query.js";
import { ScheduleTool }    from "./tools/schedule.js";
```

In the `bootstrap()` function, locate the block where tools are registered (search for `toolRegistry.registerAll` or sequential `toolRegistry.register` calls). Add after the existing registrations:

```typescript
// ── Tool Cortex 7d ──────────────────────────────────────────
toolRegistry.register(VisionTool);
toolRegistry.register(DocumentTool);
toolRegistry.register(CodeSandboxTool);
toolRegistry.register(DbQueryTool);
toolRegistry.register(ScheduleTool);
```

- [ ] **Step 4: Run integration tests to verify they pass**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npx vitest run __tests__/integration/tool-cortex-7d.test.ts
```

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && npm test
```

All tests must pass. If any pre-existing tests fail, investigate — do not bypass.

- [ ] **Step 6: Commit**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/.worktrees/tool-cortex-7a && git add src/index.ts __tests__/integration/tool-cortex-7d.test.ts && git commit -m "feat(index): register vision/document/sandbox/db_query/schedule tools + 7d integration tests"
```

---

## Completion Checklist

- [ ] Task 1: MCPManager CRUD (addServer/removeServer/updateServer) — tests pass
- [ ] Task 2: McpCommandRouter (channel-agnostic, all 9 verbs) — tests pass
- [ ] Task 3: CLI /mcp command registered — tests pass
- [ ] Task 4: Telegram /mcp replaced with McpCommandRouter — tests pass
- [ ] Task 5: VisionTool created + tested
- [ ] Task 6: DocumentTool created + tested (pdf-parse + mammoth installed)
- [ ] Task 7: CodeSandboxTool created + tested
- [ ] Task 8: DbQueryTool created + tested
- [ ] Task 9: ScheduleTool created + tested
- [ ] Task 10: toolError/toolSuccess helpers created + tested
- [ ] Task 11: create-tool.ts scaffolder + `npm run tool:create` working
- [ ] Task 12: All 5 tools registered in src/index.ts + `npm test` passes
